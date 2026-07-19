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
import json
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
        return None, f"{city}：城市查询失败（HTTP {resp.status_code}）"
    try:
        data = resp.json()
    except ValueError:
        return None, f"{city}：城市查询返回非JSON响应"
    if data.get("code") != "200":
        return None, f"{city}：城市查询失败（错误码 {data.get('code')}）"

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
    try:
        data = resp.json()
    except ValueError:
        return None, "非JSON响应"
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
            return f"{result}：指数查询失败（HTTP {resp.status_code}）"
        try:
            data = resp.json()
        except ValueError:
            return f"{result}：指数查询返回非JSON响应"
        if data.get("code") != "200":
            return f"{result}：指数查询失败（错误码 {data.get('code')}）"

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
# 用户城市设置（持久化到 JSON 文件）
# ============================================================

USERS_FILE = Path(__file__).resolve().parent / "user_cities.json"


def _load_user_cities() -> dict:
    if USERS_FILE.exists():
        try:
            return json.loads(USERS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def _save_user_cities(data: dict):
    USERS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def set_user_city(openid: str, city: str):
    users = _load_user_cities()
    users[openid] = city
    _save_user_cities(users)


def get_user_city(openid: str) -> str:
    return _load_user_cities().get(openid, "")


# ============================================================
# 功能 5：出行建议（天气简报 + 智能建议）
# ============================================================

def get_travel_advice(city: str) -> str:
    """获取天气简报 + 出行建议"""
    try:
        location_id, city_name = _lookup_city(city)
        if location_id is None:
            return city_name

        # 获取实况
        now_data, err1 = _fetch_weather_api("/v7/weather/now", location_id)
        # 获取今日预报
        forecast_data, err2 = _fetch_weather_api("/v7/weather/3d", location_id)

        if not now_data and not forecast_data:
            return f"{city_name}：天气查询失败"

        now = now_data.get("now", {}) if now_data else {}
        today = forecast_data.get("daily", [{}])[0] if forecast_data else {}

        # 天气简报
        weather_text = now.get("text", "") or today.get("textDay", "未知")
        temp = now.get("temp", "") or today.get("tempMax", "")
        feels_like = now.get("feelsLike", "")
        temp_min = today.get("tempMin", "")
        temp_max = today.get("tempMax", "")
        humidity = now.get("humidity", "")
        wind_dir = now.get("windDir", "") or today.get("windDirDay", "")
        wind_scale = now.get("windScale", "") or today.get("windScaleDay", "")
        uv = today.get("uvIndex", "")

        temp_range = f"{temp_min}~{temp_max}°C" if temp_min and temp_max else f"{temp}°C"

        lines = [
            f"🚶 {city_name} 出行建议",
            "━━━━━━━━━━━━",
            f"🌤 天气：{weather_text}",
            f"🌡 温度：{temp_range}（当前 {temp}°C，体感 {feels_like}°C）",
            f"💨 风力：{wind_dir} {wind_scale}级",
            f"💧 湿度：{humidity}%",
        ]
        if uv:
            lines.append(f"☀️ 紫外线：{uv}")

        # 根据当地日出日落时间判断白天/夜间
        from datetime import datetime
        now_time = datetime.now()
        sunrise_str = today.get("sunrise", "")  # 格式如 "07:46" 或 "2026-07-19T07:46+08:00"
        sunset_str = today.get("sunset", "")
        try:
            # 兼容两种格式：纯 HH:MM 或 ISO 格式（取 T 后的 HH:MM）
            sunrise_hm = sunrise_str[11:16] if "T" in sunrise_str else sunrise_str[:5]
            sunset_hm = sunset_str[11:16] if "T" in sunset_str else sunset_str[:5]
            sunrise_t = datetime.strptime(sunrise_hm, "%H:%M").time()
            sunset_t = datetime.strptime(sunset_hm, "%H:%M").time()
            is_daytime = sunrise_t <= now_time.time() <= sunset_t
            is_night = not is_daytime
        except (ValueError, IndexError):
            # 解析失败时回退到默认时段
            is_daytime = 6 <= now_time.hour <= 18
            is_night = now_time.hour >= 20 or now_time.hour < 6

        lines.append("━━━━━━━━━━━━")
        lines.append("📋 出行建议：")
        advice = _generate_advice(weather_text, int(temp or 0), int(temp_min or 0),
                                  int(humidity or 0), int(wind_scale or 0), int(uv or 0),
                                  is_daytime, is_night)
        lines.extend(advice)

        return "\n".join(lines)
    except requests.exceptions.Timeout:
        return f"{city}：查询超时，请稍后再试"
    except Exception as e:
        return f"查询出错：{e}"


def _generate_advice(weather_text: str, temp: int, temp_min: int,
                     humidity: int, wind_scale: int, uv: int,
                     is_daytime: bool, is_night: bool) -> list:
    """根据天气要素 + 当地日出日落时段生成出行建议列表"""
    advice = []

    # 降水（全天有效）
    if any(w in weather_text for w in ["雨", "雪", "雷"]):
        advice.append("☔ 有降水，记得带伞，路面湿滑注意安全")

    # 高温（白天更相关）
    if temp >= 35:
        if is_daytime:
            advice.append("🥵 高温天气，减少户外活动，注意防暑补水")
        else:
            advice.append("🥵 夜间仍较闷热，注意通风降温")
    elif temp >= 30 and is_daytime:
        advice.append("🌞 气温较高，外出注意防晒补水")

    # 低温（夜间/清晨更相关）
    if temp <= 0 or temp_min <= 0:
        advice.append("🧣 气温较低，注意防寒保暖")
    elif temp <= 10:
        if is_night:
            advice.append("🧥 夜间偏凉，外出建议穿厚外套")
        else:
            advice.append("🧥 天气偏凉，建议穿外套")

    # 紫外线（仅白天 6:00~18:00 有意义）
    if is_daytime:
        if uv >= 7:
            advice.append("🕶 紫外线强，建议涂防晒霜、戴帽子")
        elif uv >= 4:
            advice.append("🧢 紫外线中等，外出可适当防晒")

    # 大风（全天有效）
    if wind_scale >= 6:
        advice.append("💨 风力较大，注意高空坠物")

    # 湿度
    if humidity >= 85:
        advice.append("💧 湿度大，衣物不易晾干")
    elif humidity <= 30 and humidity > 0:
        advice.append("🏜 空气干燥，注意保湿补水")

    # 夜间出行提醒
    if is_night and not any(w in weather_text for w in ["雨", "雪", "雷"]):
        advice.append("🌙 夜间出行注意安全，建议结伴而行")

    # 适宜
    if not advice:
        advice.append("✅ 天气适宜，适合出行活动")

    return advice


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
            "【实况天气】\n"
            "【3天预报】\n"
            "【生活指数】\n"
            "【多城对比】\n"
            "【出行建议】\n"
            "【设置城市】\n"
            "━━━━━━━━━━━━\n"
            f"每人每天限查{DAILY_LIMIT}次"
        )

    # 设置城市
    if content.startswith("设置城市"):
        city = content.replace("设置城市", "").strip()
        city = re.sub(r"[\s：:，,。]", "", city)
        if not city:
            return "请发送：设置城市 + 城市名\n例如：设置城市 南京"
        location_id, city_name = _lookup_city(city)
        if not location_id:
            return f"未找到「{city}」这个城市，请检查城市名"
        set_user_city(openid, city_name)
        return f"✅ 已设置城市：{city_name}\n发送「出行建议」即可获取天气+出行建议"

    # 出行建议
    if "出行" in content or "建议" in content:
        # 优先从消息中提取城市，否则用用户设置的城市
        cities = extract_cities(content.replace("出行建议", "").replace("出行", "").replace("建议", ""))
        if cities:
            return get_travel_advice(cities[0])
        user_city = get_user_city(openid)
        if user_city:
            return get_travel_advice(user_city)
        return "你还没有设置城市哦～\n请先发送：设置城市 南京\n\n或者直接发送：南京出行建议"

    # 提取城市
    cities = extract_cities(content)

    # 多城市 → 直接对比
    if len(cities) >= 2:
        return get_multi_city(cities)

    # 判断是否包含功能关键词
    has_forecast_kw = any(kw in content for kw in ["预报", "未来", "明天", "三天", "3天"])
    has_index_kw = any(kw in content for kw in ["指数", "穿衣", "紫外线", "洗车", "运动"])
    has_weather_kw = "天气" in content

    # 有城市名
    if cities:
        city = cities[0]
        if has_forecast_kw:
            return get_forecast(city)
        if has_index_kw:
            return get_life_index(city)
        return get_weather(city)

    # 无城市名：尝试用用户设置的城市
    user_city = get_user_city(openid)
    if user_city:
        if has_forecast_kw:
            return get_forecast(user_city)
        if has_index_kw:
            return get_life_index(user_city)
        if has_weather_kw:
            return get_weather(user_city)

    # 无城市且未设置：提示设置城市
    if has_weather_kw or has_forecast_kw or has_index_kw:
        return "你还没有设置城市哦～\n请先发送：设置城市 南京\n\n或者直接发送：南京天气"

    return "暂不支持该指令，发送「帮助」查看可用功能～"


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
                "🌤【实况天气】\n"
                "📅【3天预报】\n"
                "🏃【生活指数】\n"
                "🌍【多城对比】\n"
                "🚶【出行建议】\n"
                "📍【设置城市】\n\n"
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
