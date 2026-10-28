"""Small model-facing read models over the existing Studio DTOs.

This is not a new business API, sanitizer, validator, or source of truth. Values
are copied from the actual result without inventing status, causes or IDs. Keep
the original ToolResult.content for Trace/archives; only this projection belongs
in the next model request. Explicit Skill reads retain the complete requested
text, file tree and edit digest. Unknown tools/text keep their existing contract.
"""
from copy import deepcopy
from collections import Counter
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
    "family_id", "parent_id", "version_number", "version_status", "display_name",
    "outputs", "capabilities", "can_use", "can_edit",
)
_TOPIC = (
    "reference_id",
    "id", "series_id", "title", "brief", "angle", "rationale", "source",
    "status", "position", "selection_state", "batch_id", "candidate_id",
    "research_angle", "stale", "existing_run_id", "existing_run_status",
    "existing_run_version", "available_actions",
    "production_queue_status", "production_queue_position",
    "edit_digest", "management_actions", "production_task_count",
)
_RUN = (
    "id", "run_id", "creator_id", "creator_name", "series_id", "series_name",
    "topic_id", "topic_title", "status", "version", "active_revision_number",
    "allowed_actions", "error_type", "error_message", "accepted", "card_count", "url",
    "production_queue", "latest_run_id", "task_id", "production_collection_id", "kind", "task_kind",
)
_REVISION = (
    "id", "revision_id", "revision_number", "instruction", "artifact_digest",
    "artifact_available", "artifact_error",
)
_RESEARCH = (
    "reference_id",
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
    "iteration_id", "topic_id", "stage", "active_round", "next_action",
    "task_id", "task_kind", "topic_title", "topic_url", "collection_id", "collection_name", "collection_url", "last_error", "depends_on",
)
_ERROR = (
    "code", "error", "message", "error_type", "error_message", "status",
    "last_known_status", "retryable", "run_id", "id", "batch_id", "job_id",
    "request_id", "operation_id", "series_id", "creator_id", "topic_id",
    "revision_id", "skill_id", "url", "current_digest", "expected_digest",
    "current_version", "expected_version", "current_revision", "expected_revision",
    "referencing_files",
    "iteration_id",
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


def _production_tasks(data: dict) -> dict:
    return _pick(data, ("topic_id", "creator_id", "collection_id", "collection_url", "deduplicated")) | {
        "items": _list(data.get("items", []), lambda item: _pick(item, (
            "id", "topic_id", "title", "position", "run_id", "run_version", "status", "skill_summary", "url",
            "kind", "dispatch_state", "last_error", "depends_on")))
    }


def _run(data: dict) -> dict:
    result = _pick(data, _RUN)
    if "revisions" in data:
        result["revisions"] = _list(data["revisions"], lambda revision: _pick(revision, _REVISION) | (
            {"video": _pick(revision["video"], ("url", "duration_seconds", "width", "height", "byte_size", "sha256", "has_audio"))}
            if isinstance(revision, dict) and isinstance(revision.get("video"), dict) else {}))
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
_EXTRACTION_TOOLS = {"extract_skill_from_images", "extract_skills_from_artifact",
                     "revise_extracted_skills", "get_skill_extraction"}
_EXTRACTION_WRITES = {"edit_extracted_skills", "save_extracted_skills", "cancel_skill_extraction"}
_JOB_RECEIPT = ("id", "status", "name", "description", "display_name", "task_kind", "mode",
                "digest", "revision", "operation", "queue_state", "note", "error", "error_type",
                "error_message", "last_known_status", "next_action", "url", "allowed_actions", "cancel_requested")
_SKILL_BATCH = ("status", "partial", "completed", "applied_count", "total_count",
                "outcome_unknown", "failed_index", "applied", "failed")


def _skill_job(data: dict, *, receipt_only: bool = False, explicit_read: bool = False) -> dict:
    """Submit/write/list receipts are slim; explicit reads keep unique inputs and drafts."""
    if "items" in data:
        result = _pick(data, ("page",))
        result["items"] = _list(data["items"], lambda item:
            _skill_job(item, receipt_only=True) if isinstance(item, dict) else deepcopy(item))
        return result
    pending = data.get("status") in {"queued", "running", "unknown"} or bool(data.get("operation"))
    if receipt_only or (pending and not explicit_read):
        result = _pick(data, _JOB_RECEIPT)
        if "skills" in data:
            result["skills"] = _list(data["skills"], lambda skill:
                _pick(skill, ("name", "description", "display_name", "role", "output_kind")))
        if "saved_skills" in data:
            result["saved_skills"] = _list(data["saved_skills"], lambda skill: _pick(skill, _SKILL + ("digest",)))
        if pending:
            result["next_action"] = "wait_background"
        return result

    # Keep distinct reference text and all draft files; only remove transport
    # bookkeeping and literal duplicate text, never summarise/rewrite the body.
    result = {key: deepcopy(value) for key, value in data.items() if key not in {
        "progress", "progress_directory", "draft_directory", "thread_id", "request_id",
        "created_at", "updated_at", "queue", "actions"}}
    bodies = [skill["skill_md"] for skill in data.get("skills", [])
              if isinstance(skill, dict) and isinstance(skill.get("skill_md"), str)]
    content, source = data.get("content"), data.get("source_text")
    if data.get("task_kind") == "author":
        for key, value in (("content", content), ("source_text", source)):
            if isinstance(value, str) and value and any(value in body for body in bodies):
                result.pop(key, None)
    if isinstance(content, str) and content and content == source:
        result.pop("content", None)  # source_text names the original, not the current draft
    if "saved_skills" in result:
        result["saved_skills"] = _list(result["saved_skills"], lambda skill: _pick(skill, _SKILL + ("digest",)))
    if pending:
        result["next_action"] = "wait_background"
    return result


PROJECTED_TOOLS = frozenset({
    "list_creators", "list_creator_series", "list_series_topics", "list_producer_skills",
    "get_producer_skill", "update_producer_skill_file", "get_creator_tasks",
    "prepare_topic_selection", "queue_topics", "install_producer_skill", "get_skill_install",
    "create_skill_draft",
    "manage_skill_iteration",
    "manage_topics", "manage_production_task",
    "manage_media_collection",
    "manage_skill_library", "manage_material", "manage_plan",
}) | _RESEARCH_TOOLS | _RUN_TOOLS | _DISCUSSION_TOOLS | _SERIES_WRITE_TOOLS | _EXTRACTION_TOOLS | _EXTRACTION_WRITES


def project_model_data(tool_name: str, data: Any, *, is_error: bool = False) -> Any:
    """Copy the next-step facts, never overwrite or reinterpret the source DTO."""
    if tool_name not in PROJECTED_TOOLS or not isinstance(data, dict):
        return deepcopy(data)
    if tool_name in _EXTRACTION_TOOLS | _EXTRACTION_WRITES:
        if is_error:
            result = _pick(data, _JOB_RECEIPT) | _error(data)
            if "saved_skills" in data:
                result["saved_skills"] = _list(data["saved_skills"], lambda skill: _pick(skill, _SKILL + ("digest",)))
            return result
        return _skill_job(data, receipt_only=tool_name in _EXTRACTION_WRITES,
                          explicit_read=tool_name == "get_skill_extraction")
    if is_error and tool_name == "update_producer_skill_file":
        return _error(data) | _pick(data, _SKILL + ("digest",) + _SKILL_BATCH)
    if is_error and tool_name not in _RESEARCH_TOOLS | _DISCUSSION_TOOLS:
        return _error(data)
    if tool_name == "manage_production_task":
        result = _pick(data, ("action", "topic_id", "task_id", "run_id", "status", "message"))
        fields = ("id", "title", "status", "kind", "run_id", "run_version")
        if "items" in data:
            result["items"] = _list(data["items"], lambda item: _pick(item, fields))
        if "task" in data:
            result["task"] = _pick(data["task"], fields)
        return result
    if tool_name == "manage_plan":
        fields = ("id", "title", "version", "status", "session_id", "creator_id", "error", "message", "pause_reason", "continuation_state")
        def step(item):
            result = _pick(item, ("id", "title", "tool", "arguments", "depends_on", "wait_for_completion", "status", "error", "retryable", "skippable"))
            if isinstance(item, dict) and isinstance(item.get("result"), dict):
                value = item["result"]
                result["result"] = _pick(value, ("id", "topic_id", "series_id", "creator_id", "status", "run_id", "collection_id", "url", "collection_url", "question"))
                if isinstance(value.get("items"), list):
                    result["result"]["items"] = _list(value["items"], lambda row: _pick(row, ("id", "task_id", "run_id", "status", "terminal_status", "error", "title", "url")))
            return result
        projected = _pick(data, fields)
        if isinstance(data.get("steps"), list):
            projected["steps"] = _list(data["steps"], step)
        if isinstance(data.get("items"), list):
            projected["items"] = _list(data["items"], lambda item: _pick(item, fields))
        return projected
    if tool_name == "manage_skill_library":
        result = _pick(data, ("action", "creator_id", "series_id", "version", "defaults")) | _items(data, _SKILL + ("digest",))
        if "available_versions" in data:
            result["available_versions"] = _list(data["available_versions"], lambda item: _pick(item, _SKILL + ("digest",)))
        return result
    if tool_name == "manage_material":
        fields = ("id", "name", "kind", "sha256", "size_bytes", "duration_seconds", "source_url", "url", "analysis_available")
        return _pick(data, ("action", "creator_id", "status", "message", "analysis", "receipt") + fields) | _items(data, fields)
    if tool_name == "manage_media_collection":
        if data.get("export_id"):
            # Keep exact handoff identities for a later video task; UI receipts
            # still own downloads and user-facing replies omit internal IDs.
            return _pick(data, ("action", "name", "selected_count", "collection_id", "export_id", "checksum")) | {
                "message": "ZIP 已保存，下载按钮由界面在回复下方提供；无需写地址、导出编号或占位符。"}
        result = _pick(data, ("action", "id", "collection_id", "creator_id", "name", "version", "topic_id", "run_ids",
                              "snapshot_digest", "export_id", "selected_count", "size_bytes", "checksum", "deleted"))
        paging = data.get("requested_page") or {}
        offset, limit = paging.get("offset", 0), paging.get("limit", 20)
        items = data.get("items", [])
        if "items" in data and isinstance(items, list):
            result["items"] = [_pick(item, ("id", "name", "version", "topic_id", "run_id", "run_title",
                "revision_id", "revision_number", "order", "included", "checksum", "width", "height"))
                for item in items[offset:offset + limit]]
            result["page"] = {"offset": offset, "limit": limit, "total": len(items),
                              "next_offset": offset + limit if offset + limit < len(items) else None}
            if "snapshot_digest" in data:
                result["selected_count"] = sum(item.get("included", False) for item in items)
        if "sources" in data:
            sources = data["sources"]
            result["sources"] = [_pick(source, ("run_id", "run_title", "topic_id", "topic_title", "revision_id",
                "revision_number", "status", "image_count", "error")) for source in sources[offset:offset + limit]]
            result["source_page"] = {"offset": offset, "limit": limit, "total": len(sources),
                                     "next_offset": offset + limit if offset + limit < len(sources) else None}
        return result
    if tool_name == "manage_topics":
        result = _pick(data, ("action", "ok", "id", "status", "deduplicated", "series_id",
                              "topic_ids", "title", "changed_fields", "effect", "url"))
        if "topic" in data:
            result["topic"] = _topic(data["topic"])
        if "items" in data:
            result.update(_items(data, _TOPIC, item_projector=_topic))
        if "production_tasks" in data:
            result["production_tasks"] = _production_tasks(data["production_tasks"])
        return result
    if tool_name == "manage_skill_iteration":
        result = _pick(data, ("id", "status", "revision", "series_id", "skill_id", "topic_id",
                            "round_count", "max_rounds", "best_candidate_id", "best_score",
                            "current_round", "score", "passed", "note", "error", "url", "allowed_actions",
                            "stage", "next_action", "can_resume", "can_continue"))
        if "active_round" in data:
            result["active_round"] = _pick(data["active_round"], (
                "number", "status", "production_submitted", "production_status", "trial_job_id",
                "trial_id", "trial_url", "image_count"))
        if "judge" in data:
            result["judge"] = _pick(data["judge"], ("version", "threshold"))
        if "latest_feedback" in data:
            result["latest_feedback"] = _pick(data["latest_feedback"], ("summary", "score", "passed"))
        return result
    if tool_name == "create_skill_draft":
        result = _pick(data, ("id", "status", "digest", "url", "note"))
        if "skills" in data:
            result["skills"] = _list(data["skills"], lambda skill: _pick(skill, ("name", "role", "output_kind")))
        if "files" in data:
            result["files"] = _list(data["files"], lambda file: _pick(file, ("role", "path", "kind", "size")))
        return result
    if tool_name == "list_creators":
        return _items(data, _CREATOR)
    if tool_name == "list_creator_series":
        return _pick(data, ("creator_id", "creator_name")) | _items(data, _SERIES)
    if tool_name == "list_series_topics":
        return _items(data, _TOPIC, item_projector=_topic) | _pick(data, ("series_id", "url"))
    if tool_name == "list_producer_skills":
        return _items(data, _SKILL + (("digest",) if data.get("include_versions") else ()))
    if tool_name in {"get_producer_skill", "update_producer_skill_file"}:
        # A requested file's contents are data: do not sanitize/truncate its body.
        result = _pick(data, _SKILL + ("path", "kind", "content", "content_ref", "digest", "operation"))
        if "page" in data:
            result["page"] = _pick(data["page"], _PAGE)
        if "files" in data:
            result["files"] = _list(data["files"], lambda file: _pick(file, ("path", "kind", "size")))
        if "items" in data:
            result.update(_items(data, _SKILL + ("digest",)))
        if "bindings" in data:
            result["bindings"] = _list(data["bindings"], lambda row:
                _pick(row, ("series_id", "series_name", "role", "skill_id", "revision")))
        if tool_name == "update_producer_skill_file":
            # The caller just supplied the exact text. Keep the authoritative
            # saved identity/digest, not a second copy of the write arguments.
            result.pop("content", None)
            result.update(_pick(data, _SKILL_BATCH))
        return result
    if tool_name in _RUN_TOOLS:
        if "collection_id" in data and "items" in data:
            return _production_tasks(data)
        if "items" in data:
            return _pick(data, ("accepted_count",)) | {"items": _list(data["items"], lambda item:
                _pick(item, ("topic_id", "run_id", "status", "accepted", "error", "error_code", "url", "iteration_id")))}
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
        result = _items(data, _TASK) | _pick(data, ("as_of", "filter"))
        if "summary" in data:
            result["summary"] = _pick(data["summary"], ("active", "awaiting_approval", "failed", "needs_attention"))
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
    arguments = {}
    visible_skill_text = {}
    projected = deepcopy(messages)
    call_ids = Counter(call["id"] for message in projected for call in message.get("tool_calls", [])
                       if isinstance(call, dict) and isinstance(call.get("id"), str))
    for message in projected:
        for call in message.get("tool_calls", []):
            if isinstance(call, dict) and "id" in call:
                names[call["id"]] = (call.get("function") or {}).get("name", call.get("name"))
                raw = (call.get("function") or {}).get("arguments", call.get("arguments", {}))
                try:
                    arguments[call["id"]] = json.loads(raw) if isinstance(raw, str) else raw
                except (TypeError, ValueError):
                    arguments[call["id"]] = {}
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
        if name in {"get_producer_skill", "get_skill_extraction"} and not prefix:
            try:
                if call_ids[message.get("tool_call_id")] != 1:
                    continue  # Legacy reused IDs cannot identify a unique earlier result.
                view = json.loads(message["content"])
                args = arguments.get(message.get("tool_call_id")) or {}
                identity = view.get("skill_id") or view.get("id") or args.get("skill_id")
                digest = view.get("digest")
                if not isinstance(identity, str) or not identity:
                    continue
                if name == "get_producer_skill" and (not isinstance(digest, str) or not digest):
                    continue
                changed = False
                fields = ("content",) if name == "get_producer_skill" else ("content", "source_text", "instruction")
                targets = [(view, field, field) for field in fields]
                if name == "get_skill_extraction" and isinstance(view.get("skills"), list):
                    targets.extend((skill, "skill_md", f"skills[{index}].skill_md")
                                   for index, skill in enumerate(view["skills"]) if isinstance(skill, dict))
                for container, field, location in targets:
                    text = container.get(field)
                    if not isinstance(text, str) or not text:
                        continue
                    key = (name, identity, view.get("path", args.get("path", "SKILL.md")), digest,
                           json.dumps([view.get("page"), view.get("revision"), view.get("operation")], sort_keys=True), location, text)
                    # References never outlive their full text in this request.
                    if key in visible_skill_text:
                        container.pop(field)
                        container[field + "_ref"] = {"tool_call_id": visible_skill_text[key], "field": location}
                        changed = True
                    else:
                        visible_skill_text[key] = message["tool_call_id"]
                if changed:
                    message["content"] = project_model_content(name, json.dumps(view, ensure_ascii=False))
            except (ValueError, TypeError, AttributeError):
                pass  # Unexpected shapes retain the ordinary projected result.
    return projected
