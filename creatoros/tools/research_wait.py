"""Host-side observation: one submit, then read the same batch until terminal."""
import json
from time import monotonic

from ..integrations.studio import StudioClientError
from .results import ToolResult


def observe_research(client, batch, context=None):
    observer = getattr(context, "research_progress", None)
    if observer is None:
        return research_result(batch)
    stopping = context.stopping
    deadline = monotonic() + context.research_wait_timeout_seconds
    previous = None
    while True:
        view = {key: batch.get(key) for key in
                ("id", "status", "note", "error_type", "error", "url", "progress")}
        if view != previous:
            observer(view)
            previous = view
        if batch.get("status") != "researching":
            return research_result(batch)
        if stopping.is_set():
            return _observation_error(batch, "research_wait_interrupted", "对话观察已中断；先查询同一批次，不要重新提交。")
        remaining = deadline - monotonic()
        if remaining <= 0:
            return _observation_error(batch, "research_wait_timeout", "本次等待已到上限，后台任务未被重提或取消；请查询同一批次。")
        if stopping.wait(min(1.0, remaining)):
            continue
        try:
            batch = client.request("GET", f"/api/topic-research/{batch['id']}", timeout_seconds=2)
        except StudioClientError as error:
            return _observation_error(batch, error.code, f"调研状态暂时无法读取：{error} 请查询同一批次，不要重新提交。")


def _observation_error(batch, error_type, message):
    data = {"id": batch["id"], "status": "unknown", "url": batch.get("url"),
            "error_type": error_type, "error": message,
            "last_known_status": batch.get("status")}
    return ToolResult(content=json.dumps(data, ensure_ascii=False), is_error=True, error_type=error_type)


def research_result(batch):
    # Public activity belongs in the UI, not repeatedly in the LLM's context.
    data = {key: value for key, value in batch.items() if key != "progress"}
    failed = batch.get("status") in {"failed", "interrupted", "stale"}
    if failed:
        data["message"] = batch.get("error") or batch.get("note") or "调研未完成。"
        data["message"] += " 未自动重新提交；请先处理原因，再由用户明确决定是否重试。"
    return ToolResult(content=json.dumps(data, ensure_ascii=False), is_error=failed,
                      error_type=(batch.get("error_type") or f"research_{batch['status']}") if failed else None)
