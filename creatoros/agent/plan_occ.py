"""Bind supported plan writes to an unambiguous, model-visible, scoped read."""
from collections import Counter
import json

from creatoros.ai.types import ToolCall
from creatoros.tools.discovery_gateway import gateway_source_action, resolved_canonical
from creatoros.tools.plans import GetPlanArgs
from creatoros.tools.results import ToolResult


def _unavailable(message, reason):
    return None, ToolResult(message, True, reason, details={"pre_dispatch": True}), None


def _plan_id(value):
    try:
        return GetPlanArgs.model_validate({"action": "get", "plan_id": value}).plan_id
    except (ValueError, TypeError):
        return None


def prepare_plan_change(context, call, visible_messages):
    """Bind an omitted version for supported plan changes without bypassing CAS."""
    if call.name != "manage_plan":
        return call, None, None
    try:
        args = json.loads(call.arguments or "{}")
    except (ValueError, TypeError):
        return call, None, None
    if not isinstance(args, dict) or args.get("action") not in {"update", "pause", "resume", "skip", "retry"}:
        return call, None, None
    if "expected_version" in args:
        return call, None, {"source": "explicit"}
    identity = _plan_id(args.get("plan_id"))
    if (identity is None or context is None or not context.creator_id
            or not context.agent_session_id or context.session_file is None):
        return _unavailable("缺少当前会话可信的计划读取依据，请先读取目标计划。", "plan_base_unavailable")

    messages = list(visible_messages)
    call_counts = Counter(c.get("id") for m in messages if isinstance(m, dict) and m.get("role") == "assistant"
                          for c in (m.get("tool_calls") or []) if isinstance(c, dict) and isinstance(c.get("id"), str))
    result_counts = Counter(m.get("tool_call_id") for m in messages if isinstance(m, dict) and m.get("role") == "tool"
                            and isinstance(m.get("tool_call_id"), str))
    calls, versions, invalid_read = {}, [], False
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "assistant":
            for previous in message.get("tool_calls") or []:
                if isinstance(previous, dict) and isinstance(previous.get("id"), str):
                    calls[previous["id"]] = previous
            continue
        if message.get("role") != "tool":
            continue
        try:
            if gateway_source_action(message.get("creatoros_source"), context) != "manage_plan.get":
                continue
        except (ValueError, TypeError, OSError):
            continue
        call_id = message.get("tool_call_id")
        resolved = resolved_canonical(calls.get(call_id) or {}, message)
        if (resolved is None or resolved[0] != "manage_plan" or resolved[1].get("action") != "get"
                or _plan_id(resolved[1].get("plan_id")) != identity):
            continue
        if call_counts[call_id] != 1 or result_counts[call_id] != 1:
            invalid_read = True
            continue
        try:
            result = json.loads(message.get("content", ""))
            version = result.get("version") if isinstance(result, dict) else None
            clean = (isinstance(result, dict) and result.get("id") == identity
                     and result.get("creator_id") == context.creator_id
                     and result.get("session_id") == context.agent_session_id
                     and result.get("ok") is not False and not result.get("error")
                     and not result.get("error_type") and not result.get("outcome_unknown")
                     and type(version) is int and version >= 0 and isinstance(result.get("steps"), list))
            if not clean:
                invalid_read = True
            else:
                versions.append((version, call_id))
        except (ValueError, TypeError, AttributeError):
            invalid_read = True
    if invalid_read or not versions:
        return _unavailable("缺少完整可信的目标计划读取结果，请先读取目标计划。", "plan_base_unavailable")
    if len({version for version, _ in versions}) != 1:
        return _unavailable("当前会话中该计划有多个已读版本，请明确选择版本并填写 expected_version。", "plan_base_ambiguous")
    args["expected_version"] = versions[0][0]
    amended = ToolCall(call.id, call.name, json.dumps(args, ensure_ascii=False, separators=(",", ":")))
    return amended, None, {"source": "visible_plan_read", "read_tool_call_id": versions[-1][1]}
