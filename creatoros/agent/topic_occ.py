"""Bind a topic edit to an unambiguous, model-visible, scoped read.

This helper does not fetch data, authorize a write or bypass the backend CAS.
Its caller must pass the actual model context used for the current response.
"""
from collections import Counter
import json
import re

from creatoros.ai.types import ToolCall
from creatoros.tools.discovery_gateway import gateway_source_action, resolved_canonical
from creatoros.tools.results import ToolResult


_DIGEST = re.compile(r"^[a-f0-9]{64}$")


def _call_id(call):
    return call.get("id") if isinstance(call, dict) else None


def _unavailable(message, reason):
    return None, ToolResult(message, True, reason, details={"pre_dispatch": True}), None


def prepare_topic_update(context, call, visible_messages):
    """Return (canonical call, pre-dispatch error, provenance).

    Only omitted expected_digest is filled. An explicitly supplied value,
    including null, remains untouched for the original parser and backend CAS.
    """
    if call.name != "manage_topics":
        return call, None, None
    try:
        args = json.loads(call.arguments or "{}")
    except (ValueError, TypeError):
        return call, None, None
    if not isinstance(args, dict) or args.get("action") != "update":
        return call, None, None
    if "expected_digest" in args:
        return call, None, {"source": "explicit"}
    topic_id = args.get("topic_id")
    if (context is None or not context.creator_id or context.session_file is None
            or not isinstance(topic_id, str) or not topic_id):
        return _unavailable("缺少当前会话内可信的选题读取依据，请先读取目标选题。", "topic_base_unavailable")

    messages = list(visible_messages)
    counts = Counter(_call_id(c) for m in messages if isinstance(m, dict) and m.get("role") == "assistant"
                     for c in (m.get("tool_calls") or []) if isinstance(_call_id(c), str))
    calls, versions, invalid_read = {}, [], False
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "assistant":
            for previous in message.get("tool_calls") or []:
                identity = _call_id(previous)
                if isinstance(identity, str) and counts[identity] == 1:
                    calls[identity] = previous
            continue
        if message.get("role") != "tool":
            continue
        source = message.get("creatoros_source")
        try:
            if gateway_source_action(source, context) != "manage_topics.get":
                continue
        except (ValueError, TypeError, OSError):
            continue
        call_id = message.get("tool_call_id")
        prior_call = calls.get(call_id)
        resolved = resolved_canonical(prior_call or {}, message)
        if (resolved is None or resolved[0] != "manage_topics" or resolved[1].get("action") != "get"
                or resolved[1].get("topic_id") != topic_id):
            continue
        try:
            result = json.loads(message.get("content", ""))
            clean = (isinstance(result, dict) and result.get("action") == "get"
                     and result.get("ok") is not False and not result.get("error")
                     and not result.get("error_type") and not result.get("outcome_unknown")
                     and result.get("status") not in {"failed", "error", "unknown"})
            topic = result.get("topic") if clean else None
            digest = topic.get("edit_digest") if isinstance(topic, dict) and topic.get("id") == topic_id else None
            if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
                invalid_read = True
            else:
                versions.append((digest, call_id))
        except (ValueError, TypeError, AttributeError):
            invalid_read = True
    if invalid_read or not versions:
        return _unavailable("当前会话没有完整可靠的目标选题读取结果，请先核对并读取原选题。", "topic_base_unavailable")
    if len({digest for digest, _ in versions}) != 1:
        return _unavailable("当前会话中该选题出现多个不同版本；请确认要修改哪个版本。", "topic_base_ambiguous")
    args["expected_digest"] = versions[0][0]
    amended = ToolCall(call.id, call.name, json.dumps(args, ensure_ascii=False, separators=(",", ":")))
    return amended, None, {"source": "visible_topic_read", "read_tool_call_id": versions[-1][1]}
