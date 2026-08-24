import json

from .builtins import (
    get_current_date,
    get_current_time,
    read_file,
    read_tool_result,
    write_file,
)
from .models import (
    AddAuthorArgs,
    AskAuthorArgs,
    ReadFileArgs,
    ReadToolResultArgs,
    WriteFileArgs,
)
from .personclone import add_author, ask_author, list_authors


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
            description="读取 CreatorOS 项目目录内不超过 128 KiB 的 UTF-8 文本文件；敏感路径拒绝读取。",
            execute=read_file,
            args_model=ReadFileArgs,
        ),
        Tool(
            name="read_tool_result",
            description="按 result_ref 分段读取 Session 中未截断的历史工具结果文本。",
            execute=read_tool_result,
            args_model=ReadToolResultArgs,
        ),
        Tool(
            name="write_file",
            description="在 CreatorOS 项目目录内创建新的 UTF-8 文本文件，不覆盖已有文件。",
            execute=write_file,
            args_model=WriteFileArgs,
        ),
        Tool(
            name="list_authors",
            description="列出 PersonClone 作者及推荐的回答模式；没有 Narrative Schema 的作者默认使用 strong_identity。",
            parameters={"type": "object", "properties": {}, "required": []},
            execute=list_authors,
        ),
        Tool(
            name="add_author",
            description="请求 PersonClone 抓取并建立一个新的知乎作者数字分身；返回异步任务状态。",
            execute=add_author,
            args_model=AddAuthorArgs,
        ),
        Tool(
            name="ask_author",
            description="把问题交给指定的 PersonClone 作者数字分身，并返回它生成的回答。",
            execute=ask_author,
            args_model=AskAuthorArgs,
        ),
    ]
}


tools = [tool.to_schema() for tool in tool_registry.values()]
