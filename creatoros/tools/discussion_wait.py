"""Observe one already-submitted visual discussion without resubmitting it."""
import json
from time import monotonic
from urllib.parse import quote

from ..integrations.studio import StudioClientError
from .results import ToolResult


def observe_discussion(client, record, context=None):
    observer = getattr(context, "discussion_progress", None)
    if observer is None or record.get("status") not in {"queued", "running"}:
        return discussion_result(record)

    stopping = context.stopping
    deadline = monotonic() + context.research_wait_timeout_seconds
    run_id, record_id = record["run_id"], record["id"]
    previous = None
    while True:
        view = {key: record.get(key) for key in
                ("id", "request_id", "run_id", "revision_id", "status", "reply", "error",
                 "updated_at", "events", "context")}
        if view != previous:
            observer(view)
            previous = view
        if record.get("status") not in {"queued", "running"}:
            return discussion_result(record)
        if stopping.is_set():
            return _observation_error(record, "discussion_wait_interrupted",
                                      "对话观察已中断；请查询同一讨论任务，不要重新提交。")
        remaining = deadline - monotonic()
        if remaining <= 0:
            return _observation_error(record, "discussion_wait_timeout",
                                      "等待已到上限，后台讨论未被重提或取消；请查询同一 Run 的讨论历史。")
        if stopping.wait(min(0.5, remaining)):
            continue
        try:
            history = client.request("GET", f"/api/runs/{quote(run_id, safe='')}/discussion",
                                     timeout_seconds=2)
        except StudioClientError as error:
            return _observation_error(record, error.code,
                                      f"讨论状态暂时无法读取：{error} 请查询同一任务，不要重新提交。")
        record = next((item for item in history.get("items", []) if item.get("id") == record_id), None)
        if record is None:
            return _observation_error(previous or {}, "discussion_record_missing",
                                      "讨论记录暂时未出现在历史中；没有重复提交，请稍后查询。")


def discussion_result(record):
    data = {key: record.get(key) for key in
            ("id", "request_id", "run_id", "revision_id", "status", "reply", "error", "context")}
    failed = data["status"] in {"failed", "interrupted"}
    if data["status"] in {"queued", "running"}:
        data["message"] = "讨论仍在后台运行；未重新提交。可查询同一讨论任务。"
    elif failed:
        data["message"] = data["error"] or "讨论未完成；原作品未修改。"
    return ToolResult(content=json.dumps(data, ensure_ascii=False), is_error=failed,
                      error_type=f"discussion_{data['status']}" if failed else None)


def _observation_error(record, error_type, message):
    data = {key: record.get(key) for key in ("id", "request_id", "run_id", "revision_id", "status")}
    data.update(status="unknown", last_known_status=record.get("status"), error_type=error_type, error=message)
    return ToolResult(content=json.dumps(data, ensure_ascii=False), is_error=True, error_type=error_type)
