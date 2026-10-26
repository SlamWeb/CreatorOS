"""One bounded Topic interface; production is deliberately a separate action."""
import json
from hashlib import sha256
from typing import Literal
from urllib.parse import quote
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .results import ToolResult
from .studio import QueueTopicItem, _call, list_series_topics, queue_topics, production_tasks_receipt


class TopicQuery(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    state: Literal["all", "pending", "queued"] = "all"
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


class TopicChanges(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)
    title: str | None = Field(default=None, min_length=1, max_length=240)
    brief: str | None = Field(default=None, max_length=10_000,
                              description="完整切入点/场景；空字符串清空，不修改则省略。")

    @model_validator(mode="after")
    def nonempty(self):
        if self.title is None and self.brief is None:
            raise ValueError("至少提供一个修改字段；清空切入点用空字符串。")
        return self


class ResearchCandidate(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    batch_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    candidate_id: str = Field(min_length=1, max_length=80)


class ManageTopicsArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)
    action: Literal["list", "get", "create", "update", "remove"]
    series_id: str | None = Field(default=None, min_length=1, max_length=80,
                                 description="list/create的真实栏目ID；账号由宿主会话约束。")
    topic_id: str | None = Field(default=None, min_length=1, max_length=80,
                                description="get/update/remove操作的原选题ID，不新建替代。")
    query: TopicQuery | None = Field(default=None, description="仅list：筛选与分页。")
    topics: list[QueueTopicItem] | None = Field(default=None, min_length=1, max_length=50,
                                               description="仅create：用户明确要求新建的选题，保留reference_id。")
    changes: TopicChanges | None = Field(default=None, description="仅update：只提供需要修改的标题或完整场景。")
    expected_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$",
                                       description="仅update：先get取得当前edit_digest，不能猜或沿用旧摘要。")
    candidate: ResearchCandidate | None = Field(default=None,
        description="仅remove待选建议时提供列表中实际的批次和候选ID；已入队选题不提供。")

    @model_validator(mode="after")
    def action_fields(self):
        required, optional = {
            "list": ({"series_id"}, {"query"}),
            "get": ({"topic_id"}, set()),
            "create": ({"series_id", "topics"}, set()),
            "update": ({"topic_id", "changes", "expected_digest"}, set()),
            "remove": ({"topic_id"}, {"candidate"}),
        }[self.action]
        supplied = {key for key in self.model_fields_set if key != "action" and getattr(self, key) is not None}
        if not required <= supplied or supplied - required - optional:
            raise ValueError(f"{self.action}需要{sorted(required)}，仅可额外提供{sorted(optional)}。")
        return self


def _decorate(result, action):
    if not result.is_error:
        data = json.loads(result.content)
        result.content = json.dumps({"action": action, **data}, ensure_ascii=False)
    return result


def _write_identity(action, topic_id, payload, context):
    turn = getattr(context, "user_request_id", None)
    session_file = getattr(context, "session_file", None)
    if not turn or session_file is None:
        return uuid4().hex, None
    identity = {"tool": "manage_topics", "action": action, "topic_id": topic_id, "payload": payload,
                "turn": turn, "session": getattr(context, "agent_session_id", None) or str(session_file.resolve()),
                "creator": getattr(context, "creator_id", None)}
    key = sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return key, session_file.with_suffix(".actions") / f"{key}.json"


def manage_topics(action, series_id=None, topic_id=None, query=None, topics=None, changes=None,
                  expected_digest=None, candidate=None, context=None):
    if action == "list":
        options = TopicQuery.model_validate(query or {}).model_dump()
        result = list_series_topics(series_id, context=context, **options)
        if not result.is_error:
            data = json.loads(result.content)
            for item in data.get("items", []):
                if item.get("selection_state") == "pending":
                    item["management_actions"] = ["remove"]
            result.content = json.dumps(data, ensure_ascii=False)
        return _decorate(result, action)
    if action == "create":
        # Reuse the existing queue receipt and original reference provenance.
        return _decorate(queue_topics(series_id, topics, context=context), action)
    path = f"/api/topics/{quote(topic_id, safe='')}"
    if action == "get":
        def read(client):
            data = client.request("GET", path)
            result = _topic_read(data)
            if data.get("production_task_count", 0) > 0:
                result["production_tasks"] = production_tasks_receipt(
                    client.request("GET", f"{path}/production-tasks"), context)
            return result
        return _decorate(_call(read, context), action)
    if action == "update":
        patch = TopicChanges.model_validate(changes).model_dump(exclude_none=True)
        payload = {**patch, "expected_digest": expected_digest}
        key, marker = _write_identity(action, topic_id, payload, context)
        payload["request_id"] = key

        def update(client):
            if marker is not None:
                marker.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with marker.open("x", encoding="utf-8") as stream:
                        json.dump({"request_id": key, "topic_id": topic_id}, stream)
                except FileExistsError:
                    data = client.request("GET", f"{path}/edit-receipts/{key}")
                    if data.get("status") == "unknown":
                        return ToolResult(content=json.dumps({**data,
                            "message": "尚未查到这次修改的回执，结果不确定；没有再次写入，也没有新建选题。"}, ensure_ascii=False),
                            is_error=True, error_type="studio_outcome_unknown")
                    return _mutation_read(data)
            return _mutation_read(client.request("PATCH", path, payload=payload))
        return _decorate(_call(update, context), action)
    payload = candidate or {}
    key, _ = _write_identity(action, topic_id, payload, context)
    return _decorate(_call(lambda client: _mutation_read(client.request("POST", f"{path}/remove",
        payload={**payload, "request_id": key})), context), action)


def _topic_read(data):
    return {"topic": data, "series_id": data["series_id"], "url": f"/series/{quote(data['series_id'], safe='')}"}


def _mutation_read(data):
    series_id = data.get("series_id") or data.get("topic", {}).get("series_id")
    return {**data, **({"series_id": series_id, "url": f"/series/{quote(series_id, safe='')}"} if series_id else {})}
