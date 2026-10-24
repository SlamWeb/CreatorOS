"""Observe one accepted task in the host without spending model turns."""
import json
from threading import Event
from time import monotonic
from urllib.parse import quote

from ..integrations.studio import StudioClientError
from .results import ToolResult


DEFAULT_BACKGROUND_WAIT_SECONDS = 60.0
BACKGROUND_POLL_SECONDS = 2.0
ITERATION_ACTIVE = {"queued", "running", "waiting_production"}


def extraction_active(data):
    return data.get("status") in {"queued", "running"} or bool(data.get("operation"))


def _pending(data, kind):
    observed = {**data, "status": data.get("last_known_status", data.get("status"))}
    active = extraction_active(observed) if kind == "skill_extraction" else observed.get("status") in ITERATION_ACTIVE
    return ({"kind": kind, "id": data["id"], "status": data.get("status"),
             **({"operation": data["operation"]} if kind == "skill_extraction" and data.get("operation") else {}),
             **({"last_known_status": data["last_known_status"]} if "last_known_status" in data else {})}
            if active and data.get("id") else None)


def background_receipt(result, kind):
    """Attach host control facts; preserve errors and actual backend status."""
    try:
        data = json.loads(result.content)
    except (TypeError, ValueError):
        return result
    if not isinstance(data, dict):
        return result
    pending = _pending(data, kind)
    if pending:
        result.details["pending_task"] = pending
        if kind == "skill_iteration":
            data["backend_next_action"] = data.get("next_action")
            data["next_action"] = "wait_background"
            result.content = json.dumps(data, ensure_ascii=False)
    return result


def _result(data, *, host=False, error_type=None):
    result = ToolResult(json.dumps(data, ensure_ascii=False), is_error=bool(error_type), error_type=error_type)
    if host:
        result.model_content = json.dumps({key: value for key, value in data.items() if key != "progress"},
                                          ensure_ascii=False)
    return background_receipt(result, "skill_extraction")


def _unknown(data, error_type, message):
    return _result({"id": data["id"], "status": "unknown", "last_known_status": data.get("status"),
                    "operation": data.get("operation"), "url": data.get("url"),
                    "next_action": "wait_background", "error_type": error_type, "error": message},
                   host=True, error_type=error_type)


def observe_skill_extraction(client, job, context=None):
    observer = getattr(context, "background_progress", None)
    if observer is None:
        # Direct CLI calls retain their asynchronous body and do not poll.
        return _result(job)
    stopping = getattr(context, "stopping", None) or Event()
    timeout = getattr(context, "background_wait_timeout_seconds", DEFAULT_BACKGROUND_WAIT_SECONDS)
    deadline = monotonic() + max(0.0, timeout)
    previous = None
    while True:
        view = {key: job.get(key) for key in ("id", "status", "operation", "url", "note", "error",
                                             "error_type", "progress", "queue_state")}
        view["kind"] = "skill_extraction"
        view["url"] = view["url"] or f"/skills?extraction={quote(job['id'], safe='')}"
        if view != previous:
            observer(view)
            previous = view
        if not extraction_active(job):
            failed = job.get("status") in {"failed", "interrupted"}
            return _result(job, host=True, error_type=(job.get("error_type") or "skill_extraction_failed") if failed else None)
        if stopping.is_set():
            return _unknown(job, "background_wait_interrupted", "对话观察已结束；后台任务保留，请稍后查看同一任务。")
        remaining = deadline - monotonic()
        if remaining <= 0:
            return _unknown(job, "background_wait_timeout", "本次等待已到上限；后台任务保留，请稍后查看同一任务。")
        if stopping.wait(min(BACKGROUND_POLL_SECONDS, remaining)):
            continue
        remaining = deadline - monotonic()
        if remaining <= 0:
            return _unknown(job, "background_wait_timeout", "本次等待已到上限；后台任务保留，请稍后查看同一任务。")
        try:
            updated = client.request("GET", f"/api/skill-extractions/{quote(job['id'], safe='')}",
                                     timeout_seconds=min(2.0, remaining))
        except StudioClientError as error:
            return _unknown(job, error.code, "暂时无法读取状态；后台任务保留，请稍后查看同一任务。")
        if updated.get("id") != job["id"]:
            return _unknown(job, "studio_invalid_response", "读取的任务身份不一致；请稍后查看原任务。")
        job = updated
