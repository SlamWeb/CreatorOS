import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Protocol

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent
SYSTEM_PROMPT = "你是 CreatorOS 的 Agent，只在必要时调用已提供的工具。"
SESSION_FILE = PROJECT_ROOT / "sessions" / "latest.json"


def new_messages():
    return [{"role": "system", "content": SYSTEM_PROMPT}]


def load_messages():
    if not SESSION_FILE.exists():
        return new_messages()

    try:
        messages = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        if not isinstance(messages, list) or not all(
            isinstance(message, dict) for message in messages
        ):
            raise ValueError("会话文件必须是消息对象列表。")
        if not messages or messages[0].get("role") != "system":
            messages.insert(0, new_messages()[0])
        return messages
    except (OSError, ValueError, json.JSONDecodeError):
        print("[Session] 会话文件无效，将从新会话开始。")
        return new_messages()


def save_messages(messages):
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = SESSION_FILE.with_suffix(".tmp")
    temporary_file.write_text(
        json.dumps(messages, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_file.replace(SESSION_FILE)


def get_current_time():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def get_current_date():
    return datetime.now().date().isoformat()


class ReadFileArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    path: str = Field(description="相对于 CreatorOS 项目目录的文件路径。")
    offset: int = Field(default=1, ge=1, description="从第几行开始读取，第一行是 1。")
    limit: int | None = Field(default=None, ge=1, description="最多读取多少行，不填写则读取到文件结尾。")


class WriteFileArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    path: str = Field(description="相对于 CreatorOS 项目目录的新文件路径。")
    content: str = Field(description="要写入文件的完整文本内容。")


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class ModelResponse:
    content: str | None
    tool_calls: list[ToolCall]

    def to_message(self):
        message = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [
                {"id": call.id, "name": call.name, "arguments": call.arguments}
                for call in self.tool_calls
            ]
        return message


@dataclass
class TextDelta:
    content: str


@dataclass
class ToolCallDelta:
    index: int
    id: str | None
    name: str | None
    arguments: str


@dataclass
class StreamEnd:
    finish_reason: str | None


ModelStreamEvent = TextDelta | ToolCallDelta | StreamEnd


class ModelProvider(Protocol):
    def complete(self, messages: list[dict], tools: list[dict]) -> ModelResponse:
        ...

    def stream(
        self, messages: list[dict], tools: list[dict]
    ) -> Iterator[ModelStreamEvent]:
        ...


class DeepSeekProvider:
    def __init__(self, api_key, model="deepseek-v4-flash"):
        self.client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
        self.model = model

    def _to_openai_messages(self, messages):
        converted = []
        for message in messages:
            if message.get("role") != "assistant":
                converted.append(message)
                continue

            converted_message = dict(message)
            tool_calls = message.get("tool_calls", [])
            if tool_calls:
                converted_message["tool_calls"] = [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": call["arguments"],
                        },
                    }
                    for call in tool_calls
                ]
            converted.append(converted_message)
        return converted

    def complete(self, messages, tools):
        response = self.client.chat.completions.create(
            model=self.model,
            messages=self._to_openai_messages(messages),
            tools=tools,
            extra_body={"thinking": {"type": "disabled"}},
        )
        assistant_message = response.choices[0].message
        return ModelResponse(
            content=assistant_message.content,
            tool_calls=[
                ToolCall(
                    id=tool_call.id,
                    name=tool_call.function.name,
                    arguments=tool_call.function.arguments,
                )
                for tool_call in assistant_message.tool_calls or []
            ],
        )

    def stream(self, messages, tools):
        response_stream = self.client.chat.completions.create(
            model=self.model,
            messages=self._to_openai_messages(messages),
            tools=tools,
            stream=True,
            stream_options={"include_usage": True},
            extra_body={"thinking": {"type": "disabled"}},
        )
        finish_reason = None
        for chunk in response_stream:
            if not chunk.choices:
                continue

            choice = chunk.choices[0]
            finish_reason = choice.finish_reason or finish_reason
            delta = choice.delta
            if delta.content:
                yield TextDelta(content=delta.content)

            for tool_call in delta.tool_calls or []:
                function = tool_call.function
                yield ToolCallDelta(
                    index=tool_call.index,
                    id=tool_call.id,
                    name=function.name if function else None,
                    arguments=(function.arguments if function else None) or "",
                )

        yield StreamEnd(finish_reason=finish_reason)


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


def write_file(path, content):
    requested_path = (PROJECT_ROOT / path).resolve()
    try:
        requested_path.relative_to(PROJECT_ROOT)
    except ValueError:
        return "错误：只能写入 CreatorOS 项目目录内的文件。"

    if requested_path.exists():
        return f"错误：文件已存在，为避免覆盖：{path}"

    try:
        requested_path.write_text(content, encoding="utf-8")
        return f"已写入文件：{path}"
    except FileNotFoundError:
        return f"错误：父目录不存在：{path}"
    except OSError as error:
        return f"写入文件失败：{error}"


class Tool:
    def __init__(self, name, description, execute, parameters=None, args_model=None):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.execute = execute
        self.args_model = args_model

    def to_schema(self):
        parameters = (
            self.args_model.model_json_schema()
            if self.args_model is not None
            else self.parameters
        )
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": parameters,
            },
        }

    def parse_arguments(self, raw_arguments):
        if self.args_model is not None:
            return self.args_model.model_validate_json(raw_arguments or "{}").model_dump()

        arguments = json.loads(raw_arguments or "{}")
        if not isinstance(arguments, dict):
            raise ValueError("工具参数必须是 JSON object。")
        return arguments


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
            execute=read_file,
            args_model=ReadFileArgs,
        ),
        Tool(
            name="write_file",
            description="在 CreatorOS 项目目录内创建新的 UTF-8 文本文件，不覆盖已有文件。",
            execute=write_file,
            args_model=WriteFileArgs,
        ),
    ]
}


def execute_tool_call(tool_call: ToolCall):
    tool_name = tool_call.name
    tool = tool_registry.get(tool_name)

    if tool is None:
        return f"未知工具：{tool_name}"

    try:
        arguments = tool.parse_arguments(tool_call.arguments)
        return tool.execute(**arguments)
    except ValidationError as error:
        return f"工具 {tool_name} 参数无效：{error}"
    except Exception as error:
        return f"工具 {tool_name} 执行失败：{error}"


tools = [tool.to_schema() for tool in tool_registry.values()]


def llm(
    provider: ModelProvider,
    messages: list[dict],
    tools: list[dict],
) -> ModelResponse:
    return provider.complete(messages=messages, tools=tools)


def stream_llm(
    provider: ModelProvider,
    messages: list[dict],
    tools: list[dict],
) -> ModelResponse:
    text_parts = []
    tool_calls_by_index = {}

    for event in provider.stream(messages=messages, tools=tools):
        if isinstance(event, TextDelta):
            print(event.content, end="", flush=True)
            text_parts.append(event.content)
            continue

        if isinstance(event, ToolCallDelta):
            tool_call = tool_calls_by_index.setdefault(
                event.index,
                ToolCall(id=event.id or "", name=event.name or "", arguments=""),
            )
            if event.id:
                tool_call.id = event.id
            if event.name:
                tool_call.name = event.name
            tool_call.arguments += event.arguments

    print()
    return ModelResponse(
        content="".join(text_parts) or None,
        tool_calls=[tool_calls_by_index[index] for index in sorted(tool_calls_by_index)],
    )


def run_agent(provider: ModelProvider):
    messages = load_messages()
    save_messages(messages)

    try:
        while True:
            user_input = input("你：")

            if user_input == "/exit":
                break

            if user_input == "/reset":
                messages = new_messages()
                save_messages(messages)
                print("[Session] 已清空当前会话。")
                continue

            messages.append({"role": "user", "content": user_input})
            save_messages(messages)

            while True:
                print("Agent: ", end="", flush=True)
                response = stream_llm(
                    provider=provider, messages=messages, tools=tools
                )

                messages.append(response.to_message())
                save_messages(messages)

                if not response.tool_calls:
                    break

                for tool_call in response.tool_calls:
                    print(f"[Tool call] {tool_call.name}")
                    tool_result = execute_tool_call(tool_call)
                    print(f"[Tool result] {tool_result}")

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": tool_result,
                        }
                    )
                    save_messages(messages)
    except KeyboardInterrupt:
        print("\n[Session] 已保存当前会话。")
    finally:
        save_messages(messages)


if __name__ == "__main__":
    provider: ModelProvider = DeepSeekProvider(
        api_key=os.environ["DEEPSEEK_API_KEY"]
    )
    run_agent(provider)
