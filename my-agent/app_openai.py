from openai import OpenAI

client = OpenAI(
    api_key=""
)

response = client.responses.create(
    model="gpt-4.1",
    input="你好，请介绍一下你自己"
)

print(response.output_text)
