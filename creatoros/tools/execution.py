from pydantic import ValidationError

from ..ai.types import ToolCall
from ..context import RuntimeContext
from .definitions import tool_registry
from .results import ToolResult
from .host_contract import AccountArgumentRejected, bind_account_arguments


def execute_tool_call(
    tool_call: ToolCall,
    context: RuntimeContext | None = None,
    *,
    model_requested: bool = False,
) -> ToolResult:
    tool_name = tool_call.name
    tool = tool_registry.get(tool_name)

    if tool is None:
        return ToolResult(
            content=f"未知工具：{tool_name}",
            is_error=True,
            error_type="unknown_tool",
        )

    if model_requested and (not tool.expose_to_model or (
        context is not None and context.allowed_tools is not None
        and tool_name not in context.allowed_tools
    )):
        return ToolResult(
            content=f"工具 {tool_name} 不对模型开放。运营生产请使用 start_content_run。",
            is_error=True,
            error_type="tool_not_exposed",
        )

    before_dispatch = True
    try:
        raw_arguments = (bind_account_arguments(tool_name, tool_call.arguments, context)
                         if model_requested else tool_call.arguments)
        arguments = tool.parse_arguments(raw_arguments)
        if model_requested and context and context.plan_continuation_id:
            actions = {"manage_topics": {"get", "list"}, "manage_material": {"get", "list"},
                       "manage_media_collection": {"get", "list"}, "manage_skill_library": {"get"}}
            if tool_name in actions and arguments.get("action") not in actions[tool_name]:
                raise AccountArgumentRejected("自动续接只能查看数据；调整操作需写入当前计划。")
            if tool_name == "manage_plan":
                if (arguments.get("plan_id") != context.plan_continuation_id
                        or arguments.get("action") not in {"get", "update", "resume", "retry", "skip"}):
                    raise AccountArgumentRejected("自动续接只能检查和调整触发本次通知的计划。")
        if model_requested and tool_name == "manage_material" and arguments.get("action") == "import_link":
            return ToolResult(content="BGM 分享链接无需导入或解析。请把用户完整分享文本放入视频任务 inputs.bgm_reference，和素材集、剪辑 Skill、剪辑要求一起提交给 Codex；不要再请求上传音轨。",
                              is_error=True, error_type="bgm_direct_handoff_required")
        before_dispatch = False
        result = tool.execute(context=context, **arguments)
        result = result if isinstance(result, ToolResult) else ToolResult(content=str(result))
        if model_requested:
            from .model_projection import project_model_content
            # Formatting must never turn a completed write into a failed action.
            try:
                result.model_content = project_model_content(tool_name, result.content, is_error=result.is_error)
            except (TypeError, ValueError, KeyError, AttributeError) as error:
                result.details["projection_error"] = type(error).__name__
        return result
    except AccountArgumentRejected as error:
        return ToolResult(content=str(error), is_error=True, error_type="agent_scope_rejected")
    except ValidationError as error:
        return ToolResult(
            content=f"工具 {tool_name} 参数无效：{error}",
            is_error=True,
            error_type="invalid_arguments",
            retryable=True,
            details={"validation_errors": error.errors(), "pre_dispatch": before_dispatch},
        )
    except Exception as error:
        return ToolResult(
            content=f"工具 {tool_name} 执行失败：{error}",
            is_error=True,
            error_type="tool_exception",
            details={"exception_type": type(error).__name__},
        )
