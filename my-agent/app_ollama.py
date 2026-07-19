from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:11434/v1",
    api_key="",  # 本地 Ollama 不校验，可以写任意字符串
)

response = client.chat.completions.create(
    model="qwen3:0.6b",
    messages=[
        {"role": "user", "content": "你好，请介绍一下你自己。"}
    ],
)

print(response.choices[0].message.content)
