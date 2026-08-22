from pydantic import ValidationError

from ..ai.types import ToolCall
from .definitions import tool_registry


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
