"""Model-facing adapters for read-only discussion and explicit revision requests."""
from urllib.parse import quote
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .studio import _call
from .discussion_wait import observe_discussion
from .results import ToolResult
import json


class DiscussContentRunArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)
    run_id: str = Field(min_length=1, max_length=80, description="从已有生产任务取得的 Run ID。")
    request_id: str = Field(min_length=8, max_length=64, description="本条讨论请求的幂等 ID；结果未知时复用。")
    revision_id: str = Field(min_length=1, max_length=80, description="要讨论的已验收版本 ID。")
    artifact_digest: str = Field(pattern=r"^[a-f0-9]{64}$", description="该版本验收详情中的产物摘要。")
    message: str = Field(min_length=1, max_length=8000, description="用户明确提出的讨论问题或观察。")


class GetContentDiscussionArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    run_id: str = Field(min_length=1, max_length=80, description="已有讨论任务对应的 Run ID。")


class RequestContentRevisionArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)
    run_id: str = Field(min_length=1, max_length=80, description="从已有生产任务取得的 Run ID。")
    instruction: str = Field(min_length=1, max_length=10000, description="用户明确要求的返工内容。")
    expected_version: int = Field(gt=0, description="当前 Run 版本；先查询后提交，过期会被拒绝。")


class CreatorTasksArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    creator_id: str = Field(min_length=1, max_length=80, description="当前账号的真实 ID。")
    series_id: str | None = Field(default=None, min_length=1, max_length=80,
                                  description="可选真实栏目 ID，用于缩小任务范围。")
    statuses: list[Literal["queued", "researching", "running", "producing", "validating",
                          "ready", "completed", "awaiting_approval", "approved",
                          "interrupted", "failed", "cancelled", "unknown", "stale"]] | None = Field(
        default=None, min_length=1, max_length=14,
        description="只返回指定状态的任务及对应入口；用户只要失败时填 failed，省略表示全部状态。")


def discuss_content_run(run_id, request_id, revision_id, artifact_digest, message, context=None):
    payload = {"request_id": request_id, "revision_id": revision_id,
               "artifact_digest": artifact_digest, "message": message}
    def submit(client):
        record = client.request("POST", f"/api/runs/{quote(run_id, safe='')}/discussion", payload=payload)
        return observe_discussion(client, record, context)
    return _call(submit, context)


def get_content_discussion(run_id, context=None):
    def query(client):
        history = client.request("GET", f"/api/runs/{quote(run_id, safe='')}/discussion")
        active = next((item for item in reversed(history.get("items", []))
                       if item.get("status") in {"queued", "running"}), None)
        if active is not None:
            return observe_discussion(client, active, context)
        return ToolResult(content=json.dumps(history, ensure_ascii=False))
    return _call(query, context)


def request_content_revision(run_id, instruction, expected_version, context=None):
    # RunRevisionRequest intentionally accepts only these two fields.
    payload = {"instruction": instruction, "expected_version": expected_version}
    return _call(lambda client: client.request(
        "POST", f"/api/runs/{quote(run_id, safe='')}/revisions", payload=payload), context)


def get_creator_tasks(creator_id, series_id=None, statuses=None, context=None):
    params = {"series_id": series_id} if series_id is not None else {}
    if statuses is not None:
        params["statuses"] = statuses
    return _call(lambda client: client.request(
        "GET", f"/api/creators/{quote(creator_id, safe='')}/tasks", params=params or None), context)
