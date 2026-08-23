from pydantic import ValidationError

from ..ai.types import ToolCall
from ..context import RuntimeContext
from .definitions import tool_registry
from .results import ToolResult


def execute_tool_call(
    tool_call: ToolCall,
    context: RuntimeContext | None = None,
) -> ToolResult:
    tool_name = tool_call.name
    tool = tool_registry.get(tool_name)

    if tool is None:
        return ToolResult(
            content=f"未知工具：{tool_name}",
            is_error=True,
            error_type="unknown_tool",
        )

    try:
        arguments = tool.parse_arguments(tool_call.arguments)
        result = tool.execute(context=context, **arguments)
        if isinstance(result, ToolResult):
            return result
        return ToolResult(content=str(result))
    except ValidationError as error:
        return ToolResult(
            content=f"工具 {tool_name} 参数无效：{error}",
            is_error=True,
            error_type="invalid_arguments",
            retryable=True,
            details={"validation_errors": error.errors()},
        )
    except Exception as error:
        return ToolResult(
            content=f"工具 {tool_name} 执行失败：{error}",
            is_error=True,
            error_type="tool_exception",
            details={"exception_type": type(error).__name__},
        )
