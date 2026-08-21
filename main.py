import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url="https://api.deepseek.com",
)

messages = []

while True:
    user_input = input("你：")

    if user_input == "/exit":
        break

    messages.append({"role": "user", "content": user_input})

    response = client.chat.completions.create(
        model="deepseek-v4-flash",
        messages=messages,
        extra_body={"thinking": {"type": "disabled"}},
    )

    assistant_message = response.choices[0].message
    messages.append({"role": "assistant", "content": assistant_message.content})

    print("Agent:", assistant_message.content)
