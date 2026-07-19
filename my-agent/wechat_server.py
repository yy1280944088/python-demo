"""
微信公众号天气查询服务（全功能版）
==================================
功能：
  1. 实况天气查询（发送：北京天气）
  2. 未来3天预报（发送：北京预报）
  3. 生活指数（发送：北京指数）
  4. 空气质量（发送：北京空气）
  5. 天气预警（发送：北京预警）
  6. 多城市对比（发送：北京 上海 广州 天气）
  7. AI 智能对话（发送任意非天气指令，自动调用 DeepSeek）

依赖安装：
    pip install flask requests python-dotenv openai

运行：
    python wechat_server.py
"""

import os
import re
import hashlib
import time
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import requests
from flask import Flask, request, make_response
from dotenv import load_dotenv
from openai import OpenAI

# 加载 .env 文件中的环境变量（基于脚本所在目录，不受工作目录影响）
load_dotenv(Path(__file__).resolve().parent / ".env")

app = Flask(__name__)

# 公众号后台配置的 Token
WECHAT_TOKEN = os.getenv("WECHAT_TOKEN", "my_weather_bot")

# 是否开启 AI 对话（非天气指令时调用 DeepSeek）
AI_CHAT_ENABLED = os.getenv("AI_CHAT_ENABLED", "true").lower() == "true"

# ============================================================
# 频率限制：每人每天最多查询 N 次（天气）/ AI 对话 M 次
# ============================================================
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "10"))
AI_DAILY_LIMIT = int(os.getenv("AI_DAILY_LIMIT", "3"))  # AI 对话单独限额
usage_record = {}   # 天气+通用计数
ai_usage_record = {}  # AI 对话单独计数


def check_rate_limit(openid: str) -> bool:
    """检查用户是否超过每日总限额"""
    today = date.today().isoformat()
    record = usage_record.get(openid)

    if record is None or record["date"] != today:
        usage_record[openid] = {"date": today, "count": 1}
        return True

    if record["count"] >= DAILY_LIMIT:
        return False

    record["count"] += 1
    return True


def check_ai_rate_limit(openid: str) -> bool:
    """检查用户 AI 对话是否超过每日限额"""
    today = date.today().isoformat()
    record = ai_usage_record.get(openid)

    if record is None or record["date"] != today:
        ai_usage_record[openid] = {"date": today, "count": 1}
        return True

    if record["count"] >= AI_DAILY_LIMIT:
        return False

    record["count"] += 1
    return True


# ============================================================
# 和风天气 API 通用工具
# ============================================================

def _get_api_config():
    """获取和风天气 API 配置"""
    api_key = os.getenv("QWEATHER_API_KEY")
    api_host = os.getenv("QWEATHER_API_HOST")
    return api_key, api_host


def _lookup_city(city: str):
    """城市名 → (location_id, location_name)，失败返回 (None, error_msg)"""
    api_key, api_host = _get_api_config()
    if not api_key or not api_host:
        return None, "错误：天气服务未配置"

    geo_url = f"https://{api_host}/geo/v2/city/lookup"
    resp = requests.get(geo_url, params={"location": city, "key": api_key}, timeout=10)
    if resp.status_code != 200:
        return None, f"{city}：城市查询失败"
    data = resp.json()
    if data.get("code") != "200":
        return None, f"{city}：城市查询失败"

    locations = data.get("location", [])
    if not locations:
        return None, f"未找到「{city}」这个城市"

    return locations[0]["id"], locations[0]["name"]


def _fetch_weather_api(path: str, location_id: str):
    """通用天气 API 请求，返回 (data, error_msg)"""
    api_key, api_host = _get_api_config()
    url = f"https://{api_host}{path}"
    resp = requests.get(url, params={"location": location_id, "key": api_key}, timeout=10)
    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code}"
    data = resp.json()
    code = data.get("code")
    if code == "200":
        return data, None
    if code == "204":
        return None, "204"
    return None, f"错误码 {code}"


# ============================================================
# 功能 1：实况天气
# ============================================================

def get_weather(city: str) -> str:
    """获取当前实况天气"""
    try:
        location_id, result = _lookup_city(city)
        if location_id is None:
            return result

        data, err = _fetch_weather_api("/v7/weather/now", location_id)
        if not data:
            return f"{result}：天气查询失败（{err}）"

        now = data["now"]
        return (
            f"🌤 {result} 当前天气\n"
            f"━━━━━━━━━━━━\n"
            f"天气：{now['text']}\n"
            f"温度：{now['temp']}°C（体感 {now['feelsLike']}°C）\n"
            f"湿度：{now['humidity']}%\n"
            f"风向：{now['windDir']} {now['windScale']}级\n"
            f"降水：{now['precip']}mm\n"
            f"时间：{now['obsTime']}"
        )
    except requests.exceptions.Timeout:
        return f"{city}：查询超时，请稍后再试"
    except Exception as e:
        return f"查询出错：{e}"


# ============================================================
# 功能 2：未来 3 天预报
# ============================================================

def get_forecast(city: str) -> str:
    """获取未来3天天气预报"""
    try:
        location_id, result = _lookup_city(city)
        if location_id is None:
            return result

        data, err = _fetch_weather_api("/v7/weather/3d", location_id)
        if not data:
            return f"{result}：预报查询失败（{err}）"

        lines = [f"📅 {result} 未来3天预报", "━━━━━━━━━━━━"]
        for day in data.get("daily", []):
            lines.append(
                f"{day['fxDate']}：{day['textDay']}转{day['textNight']}，"
                f"{day['tempMin']}~{day['tempMax']}°C，"
                f"{day['windDirDay']}{day['windScaleDay']}级"
            )
        return "\n".join(lines)
    except requests.exceptions.Timeout:
        return f"{city}：查询超时"
    except Exception as e:
        return f"查询出错：{e}"


# ============================================================
# 功能 3：生活指数（穿衣=3、紫外线=5、运动=1、洗车=2）
# ============================================================

def get_life_index(city: str) -> str:
    """获取生活指数"""
    try:
        location_id, result = _lookup_city(city)
        if location_id is None:
            return result

        # type=0 表示所有指数
        api_key, api_host = _get_api_config()
        url = f"https://{api_host}/v7/indices/1d"
        resp = requests.get(url, params={
            "location": location_id, "key": api_key, "type": "1,2,3,5"
        }, timeout=10)
        if resp.status_code != 200:
            return f"{result}：指数查询失败"
        data = resp.json()
        if data.get("code") != "200":
            return f"{result}：指数查询失败"

        lines = [f"🏃 {result} 生活指数", "━━━━━━━━━━━━"]
        for item in data.get("daily", []):
            lines.append(f"【{item['name']}】{item['category']}\n  {item['text']}")
        return "\n".join(lines)
    except requests.exceptions.Timeout:
        return f"{city}：查询超时"
    except Exception as e:
        return f"查询出错：{e}"


# ============================================================
# 功能 4：空气质量
# ============================================================

def get_air_quality(city: str) -> str:
    """获取空气质量"""
    try:
        location_id, result = _lookup_city(city)
        if location_id is None:
            return result

        data, err = _fetch_weather_api("/v7/air/now", location_id)
        if not data:
            if err == "204":
                return f"{result}：该地区暂无空气质量监测数据"
            return f"{result}：空气质量查询失败（{err}）"

        now = data["now"]
        return (
            f"🌬 {result} 空气质量\n"
            f"━━━━━━━━━━━━\n"
            f"AQI：{now['aqi']}（{now['category']}）\n"
            f"PM2.5：{now['pm2p5']}\n"
            f"PM10：{now['pm10']}\n"
            f"NO₂：{now['no2']}\n"
            f"SO₂：{now['so2']}\n"
            f"O₃：{now['o3']}\n"
            f"CO：{now['co']}\n"
            f"时间：{now['pubTime']}"
        )
    except requests.exceptions.Timeout:
        return f"{city}：查询超时"
    except Exception as e:
        return f"查询出错：{e}"


# ============================================================
# 功能 5：天气预警
# ============================================================

def get_warning(city: str) -> str:
    """获取天气预警"""
    try:
        location_id, result = _lookup_city(city)
        if location_id is None:
            return result

        data, err = _fetch_weather_api("/v7/warning/now", location_id)
        if not data:
            if err == "204":
                return f"✅ {result} 当前无天气预警"
            return f"{result}：预警查询失败（{err}）"

        warnings = data.get("warning", [])
        if not warnings:
            return f"✅ {result} 当前无天气预警"

        lines = [f"⚠️ {result} 天气预警", "━━━━━━━━━━━━"]
        for w in warnings:
            lines.append(
                f"【{w['typeName']}{w['level']}级】\n"
                f"  {w['text'][:150]}"
            )
        return "\n".join(lines)
    except requests.exceptions.Timeout:
        return f"{city}：查询超时"
    except Exception as e:
        return f"查询出错：{e}"


# ============================================================
# 功能 6：多城市对比
# ============================================================

def get_multi_city(cities: list) -> str:
    """多城市天气对比"""
    lines = ["🌍 多城市天气对比", "━━━━━━━━━━━━"]
    for city in cities:
        location_id, result = _lookup_city(city)
        if location_id is None:
            lines.append(f"  {city}：{result}")
            continue

        data, err = _fetch_weather_api("/v7/weather/now", location_id)
        if not data:
            lines.append(f"  {result}：查询失败")
            continue

        now = data["now"]
        lines.append(
            f"  {result}：{now['text']}，{now['temp']}°C，"
            f"{now['windDir']}{now['windScale']}级，湿度{now['humidity']}%"
        )
    return "\n".join(lines)


# ============================================================
# 功能 7：AI 智能对话（DeepSeek）
# ============================================================

def ai_chat(user_message: str, openid: str = "") -> str:
    """调用 DeepSeek 进行智能对话"""
    if not AI_CHAT_ENABLED:
        return "AI 对话功能未开启，发送「帮助」查看可用指令。"

    # AI 对话单独限频
    if openid and not check_ai_rate_limit(openid):
        return f"今日 AI 对话次数已达上限（{AI_DAILY_LIMIT}次/天），天气查询不受影响～"

    try:
        client = OpenAI(
            base_url="https://api.deepseek.com/v1",
            api_key=os.getenv("DEEPSEEK_API_KEY"),
        )
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一个简洁的天气助手，也可以回答用户的一般问题。"
                        "回答请简短（不超过200字），使用中文。"
                    ),
                },
                {"role": "user", "content": user_message},
            ],
            max_tokens=500,
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"AI 对话出错：{e}"


# ============================================================
# 消息路由：解析用户意图
# ============================================================

def parse_city(text: str) -> str:
    """从用户消息中提取城市名"""
    keywords = ["天气", "预报", "指数", "空气", "预警", "查询", "查一下",
                "怎么样", "如何", "帮我", "请", "看看", "的", "质量"]
    for kw in keywords:
        text = text.replace(kw, "")
    text = re.sub(r"[？?！!。，,\s]", "", text)
    return text.strip()


def route_message(content: str, openid: str = "") -> str:
    """根据用户消息内容路由到对应功能"""

    # 帮助
    if content in ("帮助", "help", "?", "？"):
        return (
            "🌤 天气助手指令大全\n"
            "━━━━━━━━━━━━\n"
            "【实况天气】北京天气\n"
            "【3天预报】北京预报\n"
            "【生活指数】北京指数\n"
            "【空气质量】北京空气\n"
            "【天气预警】北京预警\n"
            "【多城对比】北京 上海 广州 天气\n"
            "【AI对话】直接发任意问题\n"
            "━━━━━━━━━━━━\n"
            f"每人每天限查{DAILY_LIMIT}次（天气）\n"
            f"AI对话限{AI_DAILY_LIMIT}次/天"
        )

    # 多城市对比：包含空格分隔的多个城市
    if " " in content and ("天气" in content or "对比" in content):
        cities_text = content.replace("天气", "").replace("对比", "").replace("比较", "")
        cities = [c.strip() for c in re.split(r"[\s,，、]+", cities_text) if c.strip()]
        if len(cities) >= 2:
            return get_multi_city(cities)

    # 预报
    if "预报" in content or "未来" in content or "明天" in content or "三天" in content or "3天" in content:
        city = parse_city(content)
        return get_forecast(city) if city else "请发送：城市名+预报，例如：北京预报"

    # 生活指数
    if "指数" in content or "穿衣" in content or "紫外线" in content or "洗车" in content or "运动" in content:
        city = parse_city(content)
        return get_life_index(city) if city else "请发送：城市名+指数，例如：北京指数"

    # 空气质量
    if "空气" in content or "aqi" in content.lower() or "pm2.5" in content.lower() or "雾霾" in content:
        city = parse_city(content)
        return get_air_quality(city) if city else "请发送：城市名+空气，例如：北京空气"

    # 天气预警
    if "预警" in content or "警报" in content:
        city = parse_city(content)
        return get_warning(city) if city else "请发送：城市名+预警，例如：北京预警"

    # 实况天气
    if "天气" in content:
        city = parse_city(content)
        return get_weather(city) if city else "请发送：城市名+天气，例如：北京天气"

    # 尝试当作城市名直接查天气（如用户只发"北京"）
    city = parse_city(content)
    if city and len(city) <= 10:
        # 看看是不是一个合法城市名（尝试查询）
        location_id, result = _lookup_city(city)
        if location_id:
            return get_weather(city)

    # 兜底：AI 对话
    return ai_chat(content, openid)


# ============================================================
# 微信消息处理
# ============================================================

def build_text_reply(to_user: str, from_user: str, content: str) -> str:
    """构建微信文本回复 XML"""
    return f"""<xml>
<ToUserName><![CDATA[{to_user}]]></ToUserName>
<FromUserName><![CDATA[{from_user}]]></FromUserName>
<CreateTime>{int(time.time())}</CreateTime>
<MsgType><![CDATA[text]]></MsgType>
<Content><![CDATA[{content}]]></Content>
</xml>"""


@app.route("/wechat", methods=["GET", "POST"])
def wechat():
    """微信公众号消息入口"""

    # ---- GET：服务器验证 ----
    if request.method == "GET":
        signature = request.args.get("signature", "")
        timestamp = request.args.get("timestamp", "")
        nonce = request.args.get("nonce", "")
        echostr = request.args.get("echostr", "")

        check_list = sorted([WECHAT_TOKEN, timestamp, nonce])
        check_str = "".join(check_list)
        if hashlib.sha1(check_str.encode("utf-8")).hexdigest() == signature:
            return echostr
        return "验证失败", 403

    # ---- POST：处理用户消息 ----
    xml_data = request.data
    root = ET.fromstring(xml_data)

    msg_type = root.find("MsgType").text
    from_user = root.find("FromUserName").text
    to_user = root.find("ToUserName").text

    # 关注事件：自动发送欢迎提示
    if msg_type == "event":
        event = root.find("Event")
        if event is not None and event.text == "subscribe":
            reply_content = (
                "👋 欢迎关注天气助手！\n"
                "━━━━━━━━━━━━\n"
                "发送以下指令即可查询：\n\n"
                "🌤【实况天气】北京天气\n"
                "📅【3天预报】北京预报\n"
                "🏃【生活指数】北京指数\n"
                "🌬【空气质量】北京空气\n"
                "⚠️【天气预警】北京预警\n"
                "🌍【多城对比】北京 上海 广州 天气\n"
                "🤖【AI对话】直接发任意问题\n\n"
                "━━━━━━━━━━━━\n"
                f"每人每天：天气{DAILY_LIMIT}次 / AI对话{AI_DAILY_LIMIT}次\n"
                "发送「帮助」随时查看指令"
            )
            reply_xml = build_text_reply(from_user, to_user, reply_content)
            resp = make_response(reply_xml)
            resp.content_type = "application/xml"
            return resp
        return "success"

    # 只处理文本消息
    if msg_type != "text":
        reply_content = "目前只支持文字消息哦～\n发送「帮助」查看所有指令"
        reply_xml = build_text_reply(from_user, to_user, reply_content)
        resp = make_response(reply_xml)
        resp.content_type = "application/xml"
        return resp

    content = root.find("Content").text.strip()

    # 频率限制检查
    if not check_rate_limit(from_user):
        reply_content = f"今日查询次数已达上限（{DAILY_LIMIT}次/天），明天再来吧～"
        reply_xml = build_text_reply(from_user, to_user, reply_content)
        resp = make_response(reply_xml)
        resp.content_type = "application/xml"
        return resp

    # 路由到对应功能
    reply_content = route_message(content, from_user)

    reply_xml = build_text_reply(from_user, to_user, reply_content)
    resp = make_response(reply_xml)
    resp.content_type = "application/xml"
    return resp


# ============================================================
# 启动服务
# ============================================================

if __name__ == "__main__":
    print("🚀 微信公众号天气服务已启动（全功能版）！")
    print(f"   监听端口：80")
    print(f"   接口地址：http://你的服务器IP/wechat")
    print(f"   Token：{WECHAT_TOKEN}")
    print(f"   AI 对话：{'开启' if AI_CHAT_ENABLED else '关闭'}")
    print(f"   每日限额：天气 {DAILY_LIMIT} 次/人，AI 对话 {AI_DAILY_LIMIT} 次/人")
    app.run(host="0.0.0.0", port=80)
