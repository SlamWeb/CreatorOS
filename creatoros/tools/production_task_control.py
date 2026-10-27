"""Scoped control of existing production tasks through the Studio API."""
import json
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .results import ToolResult
from .studio import _call


class ManageProductionTaskArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)
    action: Literal["list", "stop", "remove"]
    topic_id: str = Field(min_length=1, max_length=80, description="真实选题 ID。")
    task_id: str | None = Field(default=None, min_length=1, max_length=80,
        description="stop/remove 的真实制作任务 ID。")

    @model_validator(mode="after")
    def check_task(self):
        if (self.action == "list") != (self.task_id is None):
            raise ValueError("list 不传 task_id；stop/remove 必须传 task_id。")
        return self


def _task(item):
    return {key: item.get(key) for key in ("id", "title", "status", "kind", "run_id", "run_version")}


def _error(message, code):
    return ToolResult(content=json.dumps({"error": code, "message": message}, ensure_ascii=False),
                      is_error=True, error_type=code)


def manage_production_task(action, topic_id, task_id=None, context=None):
    path = f"/api/topics/{quote(topic_id, safe='')}/production-tasks"

    def perform(client):
        data = client.request("GET", path)
        if action == "list":
            return {"action": action, "topic_id": topic_id, "items": [_task(row) for row in data["items"]]}
        task = next((row for row in data["items"] if row["id"] == task_id), None)
        if task is None:
            return _error("没有找到这个选题下可操作的制作任务。", "not_found")
        if action == "remove":
            client.request("POST", f"{path}/{quote(task_id, safe='')}/remove", payload={})
            return {"action": action, "topic_id": topic_id, "task_id": task_id,
                    "status": "removed", "message": "已从任务列表移除，产物和历史保留。"}
        run_id = task.get("run_id")
        if not run_id and task.get("dispatch_state") == "waiting":
            result = client.request("POST", f"{path}/{quote(task_id, safe='')}/cancel", payload={})
            return {"action": action, "topic_id": topic_id, "task": _task(result["items"][0])}
        if not run_id or task.get("status") not in {"queued", "producing"}:
            return _error("任务当前不能停止；请查看其最新状态。", "invalid_state")
        result = client.request("POST", f"/api/runs/{quote(run_id, safe='')}/cancel",
                                payload={"expected_version": task["run_version"]})
        status = result["status"]
        return {"action": action, "topic_id": topic_id, "task_id": task_id,
                "run_id": run_id, "status": status,
                "message": "停止请求已发送，正在等待任务退出。" if status == "producing" else "任务已停止。"}

    return _call(perform, context)
