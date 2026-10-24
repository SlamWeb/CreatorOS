"""Account-bound collection management; exporting never starts production."""
from hashlib import sha256
import json
from typing import Literal
from urllib.parse import quote
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .studio import _call


class CollectionChanges(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    run_ids: list[str] | None = Field(default=None, max_length=500,
        description="完整额外来源Run列表，不含自动收集的Run；移出集合不删除原图。")
    order_ids: list[str] | None = Field(default=None, max_length=10000)
    excluded_ids: list[str] | None = Field(default=None, max_length=10000)

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_dump(exclude_none=True):
            raise ValueError("至少提供一个要修改的字段。")
        return self


class ManageMediaCollectionArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)
    action: Literal["list", "get", "create", "update", "remove", "export"]
    creator_id: str = Field(min_length=1, max_length=80, description="账号由宿主会话绑定。")
    collection_id: str | None = Field(default=None, min_length=1, max_length=80)
    name: str | None = Field(default=None, min_length=1, max_length=120,
        description="create时为新名称；list时为名称筛选，不直接猜ID或自动挑选同名集合。")
    topic_id: str | None = Field(default=None, min_length=1, max_length=80,
        description="仅create：自动收集同选题全部Run与版本，不生产。")
    run_ids: list[str] | None = Field(default=None, max_length=500)
    changes: CollectionChanges | None = None
    expected_version: int | None = Field(default=None, ge=1)
    snapshot_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    export_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$",
        description="仅get：读取已导出的准确ZIP回执，不重新打包。")
    include_sources: bool = Field(default=False, description="仅list：附加可加入的任务索引，不读取所有图片。")
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def fields_for_action(self):
        required, optional = {
            "list": (set(), {"name", "include_sources", "offset", "limit"}),
            "get": ({"collection_id"}, {"export_id", "offset", "limit"}),
            "create": ({"name"}, {"topic_id", "run_ids"}),
            "update": ({"collection_id", "expected_version", "changes"}, set()),
            "remove": ({"collection_id", "expected_version"}, set()),
            "export": ({"collection_id"}, {"expected_version", "snapshot_digest"}),
        }[self.action]
        supplied = {key for key in self.model_fields_set if key not in {"action", "creator_id"}
                    and getattr(self, key) is not None}
        if not required <= supplied or supplied - required - optional:
            raise ValueError(f"{self.action}需要{sorted(required)}，仅可额外提供{sorted(optional)}。")
        if self.action == "export" and ((self.expected_version is None) != (self.snapshot_digest is None)):
            raise ValueError("指定选片快照需同时提供version与snapshot_digest，也可均省略导出当前选片。")
        return self


def manage_media_collection(action, creator_id, collection_id=None, name=None, topic_id=None,
        run_ids=None, changes=None, expected_version=None, snapshot_digest=None, export_id=None,
        include_sources=False, offset=0, limit=20, context=None):
    root = f"/api/creators/{quote(creator_id, safe='')}/collections"
    path = root + (f"/{quote(collection_id, safe='')}" if collection_id else "")

    def invoke(client):
        if action == "list":
            data = client.request("GET", root)
            if name is not None:
                data["items"] = [item for item in data["items"] if name.casefold() in item["name"].casefold()]
            if include_sources:
                data["sources"] = client.request("GET", root + "/sources")["items"]
        elif action == "get":
            data = client.request("GET", path + (f"/exports/{export_id}" if export_id else ""))
        elif action == "create":
            payload = {"name": name, "topic_id": topic_id, "run_ids": run_ids or []}
            turn = getattr(context, "user_request_id", None)
            identity = ["media-collection-create-v1", creator_id,
                        getattr(context, "agent_session_id", None) or str(getattr(context, "session_file", "")),
                        turn, payload]
            payload["request_id"] = sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest() if turn else uuid4().hex
            data = client.request("POST", root, payload=payload)
        elif action == "update":
            fields = CollectionChanges.model_validate(changes).model_dump(exclude_none=True)
            data = client.request("PATCH", path, payload={"expected_version": expected_version, **fields})
        elif action == "remove":
            data = client.request("DELETE", path, params={"expected_version": expected_version}, payload={})
        else:
            version, snapshot = expected_version, snapshot_digest
            if version is None:
                current = client.request("GET", path)
                version, snapshot = current["version"], current["snapshot_digest"]
            data = client.request("POST", path + "/exports", payload={
                "expected_version": version, "snapshot_digest": snapshot}, timeout_seconds=120)
        result = {**data, "action": action, "creator_id": creator_id,
                  "requested_page": {"offset": offset, "limit": limit}}
        if collection_id or action == "create":
            result["collection_id"] = collection_id or data["id"]
        return result

    return _call(invoke, context)
