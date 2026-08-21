import json
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url="https://api.deepseek.com",
)

PROJECT_ROOT = Path(__file__).resolve().parent


def get_current_time():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def get_current_date():
    return datetime.now().date().isoformat()


def read_file(path, offset=1, limit=None):
    if offset < 1:
        return "错误：offset 必须从 1 开始。"

    if limit is not None and limit < 1:
        return "错误：limit 必须大于 0。"

    requested_path = (PROJECT_ROOT / path).resolve()

    try:
        requested_path.relative_to(PROJECT_ROOT)
    except ValueError:
        return "错误：只能读取 CreatorOS 项目目录内的文件。"

    try:
        lines = requested_path.read_text(encoding="utf-8").splitlines()
        if not lines:
            return ""

        start_index = offset - 1
        if start_index >= len(lines):
            return f"错误：offset {offset} 超出文件范围（共 {len(lines)} 行）。"

        end_index = start_index + limit if limit is not None else len(lines)
        result = "\n".join(lines[start_index:end_index])

        if end_index < len(lines):
            remaining = len(lines) - end_index
            next_offset = end_index + 1
            result += f"\n\n[文件还有 {remaining} 行，可使用 offset={next_offset} 继续读取。]"

        return result
    except FileNotFoundError:
        return f"文件不存在：{path}"
    except IsADirectoryError:
        return f"这不是文件：{path}"
    except UnicodeDecodeError:
        return f"文件不是 UTF-8 文本：{path}"


class Tool:
    def __init__(self, name, description, parameters, execute):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.execute = execute

    def to_schema(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


tool_registry = {
    tool.name: tool
    for tool in [
        Tool(
            name="get_current_time",
            description="获取当前本地时间。",
            parameters={"type": "object", "properties": {}, "required": []},
            execute=get_current_time,
        ),
        Tool(
            name="get_current_date",
            description="获取当前日期。",
            parameters={"type": "object", "properties": {}, "required": []},
            execute=get_current_date,
        ),
        Tool(
            name="read_file",
            description="读取 CreatorOS 项目目录内的 UTF-8 文本文件。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对于 CreatorOS 项目目录的文件路径。",
                    },
                    "offset": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "从第几行开始读取，第一行是 1。",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "最多读取多少行，不填写则读取到文件结尾。",
                    },
                },
                "required": ["path"],
            },
            execute=read_file,
        ),
    ]
}


def execute_tool_call(tool_call):
    tool_name = tool_call.function.name
    tool = tool_registry.get(tool_name)

    if tool is None:
        return f"未知工具：{tool_name}"

    try:
        arguments = json.loads(tool_call.function.arguments or "{}")
        if not isinstance(arguments, dict):
            raise ValueError("工具参数必须是 JSON object。")

        return tool.execute(**arguments)
    except Exception as error:
        return f"工具 {tool_name} 执行失败：{error}"


tools = [tool.to_schema() for tool in tool_registry.values()]

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
            tool_result = execute_tool_call(tool_call)

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result,
                }
            )
