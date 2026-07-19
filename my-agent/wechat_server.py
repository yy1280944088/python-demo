"""
微信公众号天气查询服务
==================================
功能：
  1. 实况天气查询（发送：北京天气）
  2. 未来3天预报（发送：北京预报）
  3. 生活指数（发送：北京指数）
  4. 多城市对比（发送：北京 上海 广州 天气）

依赖安装：
    pip install flask requests python-dotenv

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

# 加载 .env 文件中的环境变量（基于脚本所在目录，不受工作目录影响）
load_dotenv(Path(__file__).resolve().parent / ".env")

app = Flask(__name__)

# 公众号后台配置的 Token
WECHAT_TOKEN = os.getenv("WECHAT_TOKEN", "my_weather_bot")

# ============================================================
# 频率限制：每人每天最多查询 N 次
# ============================================================
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "10"))
usage_record = {}


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
# 功能 4：多城市对比
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
# 消息路由：解析用户意图
# 规则：输入包含多个城市 → 多城对比；一个城市 → 天气/预报/指数
# ============================================================

MAJOR_CITIES = [
    "北京", "上海", "天津", "重庆",
    "哈尔滨", "长春", "沈阳", "呼和浩特", "石家庄", "太原",
    "济南", "郑州", "西安", "兰州", "银川", "西宁", "乌鲁木齐",
    "成都", "贵阳", "昆明", "拉萨", "武汉", "长沙", "南昌",
    "合肥", "南京", "杭州", "福州", "广州", "南宁", "海口",
    "台北", "香港", "澳门",
    "深圳", "厦门", "青岛", "大连", "宁波", "苏州", "无锡",
    "珠海", "三亚", "桂林", "丽江", "洛阳", "烟台", "温州",
    "常州", "徐州", "泉州", "佛山", "东莞", "中山", "惠州",
    "绍兴", "嘉兴", "金华", "台州", "芜湖", "潍坊", "威海",
    "秦皇岛", "唐山", "包头", "鄂尔多斯", "汉中", "延安",
    "日喀则", "那曲", "昌都", "林芝",
]

CITY_PATTERN = re.compile("|".join(sorted(MAJOR_CITIES, key=len, reverse=True)))


def extract_cities(text: str) -> list:
    """从用户消息中提取城市名列表"""
    # 策略1：按分隔符拆分（空格、逗号、顿号、分号）
    cleaned = re.sub(
        r"(帮我|请|对比|比较|一下|看看|查一下|查询|天气|预报|指数|怎么样|如何|的|和|与|跟|还有)",
        "", text
    )
    parts = [c.strip() for c in re.split(r"[\s,，、;；]+", cleaned) if c.strip()]
    if len(parts) >= 2:
        return parts

    # 策略2：用已知城市名正则匹配（处理无分隔符的情况，如"上海拉萨"）
    found = CITY_PATTERN.findall(text)
    unique = list(dict.fromkeys(found))
    if unique:
        return unique

    # 策略3：去除关键词后剩余部分当作单个城市名
    city = cleaned.strip()
    city = re.sub(r"[？?！!。，,\s]", "", city)
    if city:
        return [city]

    return []


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
            "【多城对比】上海 拉萨\n"
            "━━━━━━━━━━━━\n"
            f"每人每天限查{DAILY_LIMIT}次"
        )

    # 提取城市
    cities = extract_cities(content)

    if not cities:
        return "暂不支持该指令，发送「帮助」查看可用功能～"

    # 多城市 → 直接对比
    if len(cities) >= 2:
        return get_multi_city(cities)

    # 单城市 → 根据关键词决定查什么
    city = cities[0]

    if "预报" in content or "未来" in content or "明天" in content or "三天" in content or "3天" in content:
        return get_forecast(city)

    if "指数" in content or "穿衣" in content or "紫外线" in content or "洗车" in content or "运动" in content:
        return get_life_index(city)

    # 默认查实况天气
    return get_weather(city)


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
                "🌍【多城对比】上海 拉萨\n\n"
                "━━━━━━━━━━━━\n"
                f"每人每天限查{DAILY_LIMIT}次\n"
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
    print("🚀 微信公众号天气服务已启动！")
    print(f"   监听端口：80")
    print(f"   接口地址：http://你的服务器IP/wechat")
    print(f"   Token：{WECHAT_TOKEN}")
    print(f"   每日限额：{DAILY_LIMIT} 次/人")
    app.run(host="0.0.0.0", port=80)
