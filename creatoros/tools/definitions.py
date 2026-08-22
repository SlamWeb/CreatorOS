import json

from .builtins import get_current_date, get_current_time, read_file, write_file
from .models import ReadFileArgs, WriteFileArgs


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


tools = [tool.to_schema() for tool in tool_registry.values()]
