import os
from datetime import datetime

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url="https://api.deepseek.com",
)


def get_current_time():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def get_current_date():
    return datetime.now().date().isoformat()


tool_registry = {
    "get_current_time": get_current_time,
    "get_current_date": get_current_date,
}


tools = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "获取当前本地时间。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_date",
            "description": "获取当前日期。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]

messages = []

while True:
    user_input = input("你：")

    if user_input == "/exit":
        break

    messages.append({"role": "user", "content": user_input})

    while True:
        response = client.chat.completions.create(
            model="deepseek-v4-flash",
            messages=messages,
            tools=tools,
            extra_body={"thinking": {"type": "disabled"}},
        )

        assistant_message = response.choices[0].message
        messages.append(assistant_message.model_dump(exclude_none=True))

        if not assistant_message.tool_calls:
            print("Agent:", assistant_message.content)
            break

        for tool_call in assistant_message.tool_calls:
            tool_function = tool_registry.get(tool_call.function.name)

            if tool_function is None:
                tool_result = f"未知工具：{tool_call.function.name}"
            else:
                tool_result = tool_function()

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result,
                }
            )
