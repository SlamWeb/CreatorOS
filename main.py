import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url="https://api.deepseek.com",
)

messages = [
    {"role": "user", "content": "用一句话解释什么是 Agent Runtime。"}
]

response = client.chat.completions.create(
    model="deepseek-v4-flash",
    messages=messages,
    extra_body={"thinking": {"type": "disabled"}},
)

assistant_message = response.choices[0].message
messages.append({"role": "assistant", "content": assistant_message.content})

print("第一轮:", assistant_message.content)

messages.append({"role": "user", "content": "再用一句话解释 Tool Calling。"})

second_response = client.chat.completions.create(
    model="deepseek-v4-flash",
    messages=messages,
    extra_body={"thinking": {"type": "disabled"}},
)

second_assistant_message = second_response.choices[0].message
messages.append({"role": "assistant", "content": second_assistant_message.content})

print("第二轮:", second_assistant_message.content)
