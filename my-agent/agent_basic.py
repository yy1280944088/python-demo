"""
入门级 Agent 示例
=================
核心思想：Agent = LLM + 工具 + 循环（思考 → 行动 → 观察）

这个 Agent 具备：
1. 系统提示词（定义角色和行为）
2. 可调用的工具（查天气）
3. 自主循环决策（LLM 自己决定何时调用工具、何时给出最终回答）
"""

import json
import os
import requests
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

# 加载 .env 文件中的环境变量（基于脚本所在目录，不受工作目录影响）
load_dotenv(Path(__file__).resolve().parent / ".env")

# ============================================================
# 第 1 步：定义工具（Tools）
# ============================================================
# 工具是 Agent 与外部世界交互的能力，以 JSON Schema 描述给 LLM

tools = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询指定城市的天气信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "城市名称，例如：北京、上海",
                    }
                },
                "required": ["city"],
            },
        },
    },
]


# ============================================================
# 第 2 步：实现工具的具体逻辑
# ============================================================
# 当 LLM 决定调用某个工具时，Agent 负责执行对应的 Python 函数

def get_weather(city: str) -> str:
    """通过和风天气 API 获取当前实况天气（数据来自中国气象局观测站）"""
    api_key = os.getenv("QWEATHER_API_KEY")
    api_host = os.getenv("QWEATHER_API_HOST")
    if not api_key:
        return "错误：请先在 .env 文件中配置 QWEATHER_API_KEY"
    if not api_host:
        return "错误：请先在 .env 文件中配置 QWEATHER_API_HOST"

    try:
        # 第 1 步：通过城市名查询 Location ID
        geo_url = f"https://{api_host}/geo/v2/city/lookup"
        geo_params = {"location": city, "key": api_key}
        geo_resp = requests.get(geo_url, params=geo_params, timeout=10)
        if geo_resp.status_code != 200:
            return f"{city}：城市查询失败（HTTP {geo_resp.status_code}）: {geo_resp.text[:200]}"
        try:
            geo_data = geo_resp.json()
        except ValueError:
            return f"{city}：城市查询返回非JSON响应: {geo_resp.text[:200]}"

        if geo_data.get("code") != "200":
            return f"{city}：城市查询失败（错误码 {geo_data.get('code')}）"

        locations = geo_data.get("location", [])
        if not locations:
            return f"{city}：未找到该城市"

        location_id = locations[0]["id"]
        location_name = locations[0]["name"]

        # 第 2 步：查询当前实况天气
        weather_url = f"https://{api_host}/v7/weather/now"
        weather_params = {"location": location_id, "key": api_key}
        weather_resp = requests.get(weather_url, params=weather_params, timeout=10)
        if weather_resp.status_code != 200:
            return f"{location_name}：天气查询失败（HTTP {weather_resp.status_code}）: {weather_resp.text[:200]}"
        try:
            weather_data = weather_resp.json()
        except ValueError:
            return f"{location_name}：天气查询返回非JSON响应: {weather_resp.text[:200]}"

        if weather_data.get("code") != "200":
            return f"{location_name}：天气查询失败（错误码 {weather_data.get('code')}）"

        now = weather_data["now"]
        lines = [
            f"{location_name} 当前实况天气：",
            f"  天气状况：{now['text']}",
            f"  温度：{now['temp']}°C（体感 {now['feelsLike']}°C）",
            f"  湿度：{now['humidity']}%",
            f"  风向：{now['windDir']} {now['windScale']}级",
            f"  降水量：{now['precip']}mm",
            f"  观测时间：{now['obsTime']}",
        ]
        return "\n".join(lines)
    except requests.exceptions.Timeout:
        return f"{city}：查询超时，请稍后再试"
    except Exception as e:
        return f"{city}：查询出错 - {e}"


# 工具名称 → 函数的映射表
tool_functions = {
    "get_weather": get_weather,
}


# ============================================================
# 第 3 步：构建 Agent 循环
# ============================================================
# 这是 Agent 的核心 —— 一个 while 循环，LLM 自主决定何时停止

def run_agent(user_message: str, max_turns: int = 10):
    """
    运行 Agent 的主循环

    参数:
        user_message: 用户输入的消息
        max_turns: 最大循环次数（防止无限循环）
    """
    client = OpenAI(
        base_url="https://api.deepseek.com/v1",
        api_key=os.getenv("DEEPSEEK_API_KEY"),
    )

    # 初始化对话历史（messages 列表）
    messages = [
        {
            "role": "system",
            "content": (
                "你是一个天气查询助手，可以帮助用户查询各城市的天气信息。"
                "请根据用户的问题，合理使用工具来获取天气信息，然后给出清晰的回答。"
                "回答请使用中文。"
            ),
        },
        {
            "role": "user",
            "content": user_message,
        },
    ]

    print(f"\n{'='*50}")
    print(f"🧑 用户: {user_message}")
    print(f"{'='*50}")

    for turn in range(max_turns):
        # ---- 调用 LLM ----
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=messages,
            tools=tools,
            tool_choice="auto",  # LLM 自主决定是否使用工具
        )

        assistant_message = response.choices[0].message

        # ---- 情况 A：LLM 没有调用工具 → 最终回答 ----
        if not assistant_message.tool_calls:
            print(f"\n🤖 助手: {assistant_message.content}")
            return assistant_message.content

        # ---- 情况 B：LLM 调用了工具 → 执行工具并把结果加入对话 ----
        # 先把助手的消息（包含工具调用请求）加入历史
        messages.append(assistant_message)

        # 逐个执行工具调用
        for tool_call in assistant_message.tool_calls:
            func_name = tool_call.function.name
            func_args = json.loads(tool_call.function.arguments)

            print(f"\n🔧 调用工具: {func_name}({func_args})")

            # 执行对应的工具函数
            if func_name in tool_functions:
                result = tool_functions[func_name](**func_args)
            else:
                result = f"未知工具: {func_name}"

            print(f"📋 工具结果: {result}")

            # 把工具执行结果加入对话历史
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })

    return "达到最大循环次数，Agent 停止运行"


# ============================================================
# 第 4 步：运行 Agent（交互式对话）
# ============================================================
if __name__ == "__main__":
    print("🌤 天气查询助手已启动（输入 q 退出）")
    while True:
        user_input = input("\n🧑 请输入你的问题: ").strip()
        if user_input.lower() in ("q", "quit", "exit"):
            print("👋 再见！")
            break
        if not user_input:
            continue
        run_agent(user_input)
