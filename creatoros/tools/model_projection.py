"""Small model-facing read models over the existing Studio DTOs.

This is not a new business API, sanitizer, validator, or source of truth. Values
are copied from the actual result without inventing status, causes or IDs. Keep
the original ToolResult.content for Trace/archives; only this projection belongs
in the next model request. Explicit Skill reads retain the complete requested
text, file tree and edit digest. Unknown tools/text keep their existing contract.
"""
from copy import deepcopy
import json
from typing import Any


_CREATOR = (
    "id", "display_name", "platform", "account_handle", "timezone",
    "daily_content_limit", "is_active",
)
_SERIES = (
    "id", "creator_id", "name", "description", "audience", "skill_name",
    "mind_skill_id", "production_skill_id", "revision", "is_active",
    "topic_count", "available_topic_count", "latest_run_status",
)
_SKILL = (
    "id", "skill_id", "name", "description", "role", "producible",
    "available", "editable", "carousel_compatible", "compatibility_note",
    "github_url",
)
_TOPIC = (
    "id", "series_id", "title", "brief", "angle", "rationale", "source",
    "status", "position", "selection_state", "batch_id", "candidate_id",
    "research_angle", "stale", "existing_run_id", "existing_run_status",
    "existing_run_version", "available_actions",
)
_RUN = (
    "id", "run_id", "creator_id", "creator_name", "series_id", "series_name",
    "topic_id", "topic_title", "status", "version", "active_revision_number",
    "allowed_actions", "error_type", "error_message", "accepted", "card_count", "url",
)
_REVISION = (
    "id", "revision_id", "revision_number", "instruction", "artifact_digest",
    "artifact_available", "artifact_error",
)
_RESEARCH = (
    "id", "batch_id", "series_id", "requested_count", "instructions", "status", "note",
    "stale", "error_type", "error", "last_known_status", "url",
)
_DISCUSSION = (
    "id", "request_id", "run_id", "revision_id", "artifact_digest", "status",
    "message", "reply", "error", "error_type", "last_known_status", "url",
)
_TASK = (
    "id", "kind", "title", "status", "series_id", "run_id", "url",
    "updated_at", "last_activity_at",
)
_ERROR = (
    "code", "error", "message", "error_type", "error_message", "status",
    "last_known_status", "retryable", "run_id", "id", "batch_id", "job_id",
    "request_id", "operation_id", "series_id", "creator_id", "topic_id",
    "revision_id", "skill_id", "url", "current_digest", "expected_digest",
    "current_version", "expected_version", "current_revision", "expected_revision",
)
_PAGE = ("offset", "limit", "total", "total_chars", "has_more", "next_offset")


def _pick(data: Any, fields: tuple[str, ...]) -> Any:
    if not isinstance(data, dict):
        return deepcopy(data)
    return {key: deepcopy(data[key]) for key in fields if key in data}


def _items(data: dict, fields: tuple[str, ...], *, item_projector=None) -> dict:
    result = {}
    if "items" in data:
        result["items"] = _list(data["items"], item_projector or (lambda item: _pick(item, fields)))
    if "page" in data:
        result["page"] = _pick(data["page"], _PAGE)
    return result


def _list(value: Any, projector) -> Any:
    return [projector(item) for item in value] if isinstance(value, list) else deepcopy(value)


def _error(data: dict) -> dict:
    result = _pick(data, _ERROR)
    if isinstance(data.get("error"), dict):
        result["error"] = _pick(data["error"], _ERROR)
    return result


def _topic(data: Any) -> Any:
    result = _pick(data, _TOPIC)
    if isinstance(data, dict) and "sources" in data:
        result["sources"] = _list(data["sources"], lambda source: _pick(source, ("title", "url")))
    return result


def _run(data: dict) -> dict:
    result = _pick(data, _RUN)
    if "revisions" in data:
        result["revisions"] = _list(data["revisions"], lambda revision: _pick(revision, _REVISION))
    return result


def _discussion(data: Any) -> Any:
    result = _pick(data, _DISCUSSION)
    if isinstance(data, dict) and "context" in data:
        result["context"] = _pick(data["context"], (
            "revision_number", "image_count", "history_mode", "includes", "excludes",
        ))
        if isinstance(data["context"], dict) and "skills" in data["context"]:
            result["context"]["skills"] = _list(
                data["context"]["skills"], lambda skill: _pick(skill, ("role", "name")),
            )
    return result


_RESEARCH_TOOLS = {"research_series_topics", "get_topic_research"}
_RUN_TOOLS = {"start_content_run", "get_content_run", "request_content_revision"}
_DISCUSSION_TOOLS = {"discuss_content_run", "get_content_discussion"}
_SERIES_WRITE_TOOLS = {"compose_series", "update_series_composition", "assign_series"}
PROJECTED_TOOLS = frozenset({
    "list_creators", "list_creator_series", "list_series_topics", "list_producer_skills",
    "get_producer_skill", "update_producer_skill_file", "get_creator_tasks",
    "prepare_topic_selection", "queue_topics", "install_producer_skill", "get_skill_install",
}) | _RESEARCH_TOOLS | _RUN_TOOLS | _DISCUSSION_TOOLS | _SERIES_WRITE_TOOLS


def project_model_data(tool_name: str, data: Any, *, is_error: bool = False) -> Any:
    """Copy the next-step facts, never overwrite or reinterpret the source DTO."""
    if tool_name not in PROJECTED_TOOLS or not isinstance(data, dict):
        return deepcopy(data)
    if is_error and tool_name not in _RESEARCH_TOOLS | _DISCUSSION_TOOLS:
        return _error(data)
    if tool_name == "list_creators":
        return _items(data, _CREATOR)
    if tool_name == "list_creator_series":
        return _pick(data, ("creator_id", "creator_name")) | _items(data, _SERIES)
    if tool_name == "list_series_topics":
        return _items(data, _TOPIC, item_projector=_topic)
    if tool_name == "list_producer_skills":
        return _items(data, _SKILL)
    if tool_name in {"get_producer_skill", "update_producer_skill_file"}:
        # A requested file's contents are data: do not sanitize/truncate its body.
        result = _pick(data, _SKILL + ("path", "kind", "content", "digest"))
        if "page" in data:
            result["page"] = _pick(data["page"], _PAGE)
        if "files" in data:
            result["files"] = _list(data["files"], lambda file: _pick(file, ("path", "kind", "size")))
        return result
    if tool_name in _RUN_TOOLS:
        return _run(data)
    if tool_name in _RESEARCH_TOOLS:
        result = _pick(data, _RESEARCH)
        # The stored count is a request limit, not proof of that many results.
        # Count only the candidates actually returned; do not pad or truncate.
        if "count" in data:
            result["requested_count"] = deepcopy(data["count"])
        if is_error:
            result.update(_error(data))
        if "candidates" in data:
            result["candidates"] = _list(data["candidates"], lambda candidate:
                _topic(candidate) | _pick(candidate, ("queued",))
                if isinstance(candidate, dict) else deepcopy(candidate))
            if isinstance(data["candidates"], list):
                result["returned_count"] = len(data["candidates"])
        if "series_context" in data:
            result["series_context"] = _pick(data["series_context"], _SERIES)
        return result
    if tool_name in _DISCUSSION_TOOLS:
        result = _items(data, (), item_projector=_discussion) if "items" in data else _discussion(data)
        if is_error:
            result.update(_error(data))
        return result
    if tool_name == "get_creator_tasks":
        result = _items(data, _TASK) | _pick(data, ("as_of",))
        if "summary" in data:
            result["summary"] = _pick(data["summary"], ("active", "awaiting_approval", "failed"))
        return result
    if tool_name in _SERIES_WRITE_TOOLS:
        result = _pick(data, ("request_id", "deduplicated", "url"))
        if "series" in data:
            result["series"] = _pick(data["series"], _SERIES)
        return result
    if tool_name == "queue_topics":
        return _pick(data, (
            "request_id", "deduplicated", "operation_id", "series_id", "topic_ids", "status", "url",
        ))
    if tool_name == "prepare_topic_selection":
        result = _pick(data, ("operation_id", "status", "url"))
        if isinstance(data.get("preview"), dict):
            # The API intentionally omits confirmation tokens from this preview.
            result["preview"] = _pick(data["preview"], ("changes",))
        return result
    # Installation is still a background operation, not a production action.
    result = _pick(data, ("id", "status", "github_url", "role", "error", "url"))
    if data.get("status") in {"failed", "interrupted", "unknown"} and "message" in data:
        result["message"] = deepcopy(data["message"])
    if "skill" in data:
        result["skill"] = _pick(data["skill"], _SKILL)
    return result


def project_model_content(tool_name: str, content: str, *, is_error: bool = False) -> str:
    """Project JSON only; plain text and unhandled tools remain byte-for-byte."""
    if tool_name not in PROJECTED_TOOLS:
        return content
    try:
        data = json.loads(content)
        return json.dumps(project_model_data(tool_name, data, is_error=is_error), ensure_ascii=False)
    except (ValueError, TypeError, KeyError, AttributeError):
        # Unexpected DTO shapes must not invalidate an already completed action.
        return content


def project_tool_messages(messages: list[dict]) -> list[dict]:
    """Keep the ledger/archive original; project only the outbound model view."""
    names = {}
    projected = deepcopy(messages)
    for message in projected:
        for call in message.get("tool_calls", []):
            if isinstance(call, dict) and "id" in call:
                names[call["id"]] = (call.get("function") or {}).get("name", call.get("name"))
        if message.get("role") != "tool":
            continue
        name = names.get(message.get("tool_call_id"))
        content = message.get("content")
        if not isinstance(content, str) or name not in PROJECTED_TOOLS:
            continue
        prefix, body = "", content
        if content.startswith("[tool_error type=") and "\n" in content:
            prefix, body = content.split("\n", 1)
            prefix += "\n"
        message["content"] = prefix + project_model_content(name, body, is_error=bool(prefix))
    return projected
