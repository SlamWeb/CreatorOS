"""Object-level Skill references and source media, using guarded Studio HTTP."""
from hashlib import sha256
import json
from typing import Literal
from urllib.parse import quote
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from creatoros.integrations.studio import StudioClientError
from .studio import _call


class StageSkillArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    skill_id: str = Field(min_length=1, max_length=120)
    role: Literal["primary", "supporting"]


class StageDefaultsArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    image: list[StageSkillArgs] = Field(max_length=12)
    video: list[StageSkillArgs] = Field(max_length=12)


class ManageSkillLibraryArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    action: Literal["get", "add", "remove", "set_pool"]
    creator_id: str | None = Field(default=None, min_length=1, max_length=80, description="省略用当前账号。")
    series_id: str | None = Field(default=None, min_length=1, max_length=80, description="get读取栏目池；set_pool更新此栏目。")
    skill_id: str | None = Field(default=None, min_length=1, max_length=120)
    expected_version: int | None = Field(default=None, ge=0)
    skill_ids: list[str] | None = Field(default=None, max_length=100)
    defaults: StageDefaultsArgs | None = None

    @model_validator(mode="after")
    def action_fields(self):
        required, optional = {
            "get": (set(), {"series_id"}),
            "add": ({"skill_id"}, set()),
            "remove": ({"skill_id", "expected_version"}, set()),
            "set_pool": ({"series_id", "expected_version", "skill_ids"}, {"defaults"}),
        }[self.action]
        present = {key for key in self.model_fields_set if key not in {"action", "creator_id"}
                   and getattr(self, key) is not None}
        if not required <= present or present - required - optional:
            raise ValueError(f"{self.action} 的对象字段不完整或混用。")
        return self


class ManageMaterialArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    action: Literal["list", "get", "import_link"]
    creator_id: str | None = Field(default=None, min_length=1, max_length=80, description="省略用当前账号。")
    material_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    source_url: str | None = Field(default=None, min_length=1, max_length=2000, description="用户提供的音乐/视频分享链接，不传主机路径。")
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def action_fields(self):
        if self.action == "get" and (not self.material_id or self.source_url):
            raise ValueError("get 需要 material_id，不能带分享链接。")
        if self.action == "import_link" and (not self.source_url or self.material_id):
            raise ValueError("import_link 需要 source_url，不能带素材 ID。")
        if self.action == "list" and (self.material_id or self.source_url):
            raise ValueError("list 不接受素材 ID 或分享链接。")
        return self


def _creator(creator_id, context):
    bound = getattr(context, "creator_id", None)
    if bound and creator_id and creator_id != bound:
        raise StudioClientError("这个对话只处理当前账号。", code="agent_scope_rejected")
    creator_id = bound or creator_id
    if not creator_id:
        raise StudioClientError("请先选择账号。", code="creator_required")
    return creator_id


def _request_id(action, owner, value, context):
    turn = getattr(context, "user_request_id", None)
    source = [action, owner, value, getattr(context, "agent_session_id", None), turn]
    return sha256(json.dumps(source, sort_keys=True, ensure_ascii=False).encode()).hexdigest() if turn else uuid4().hex


def _preserve_defaults(column, account, selected_ids):
    """Keep compatible legacy defaults without asking the Agent to set roles."""
    versions = [*account.get("items", []), *account.get("available_versions", []),
                *column.get("items", []), *column.get("available_versions", [])]
    families = {item["id"]: item.get("family_id", item["id"]) for item in versions if "id" in item}
    selected = {families[value] for value in selected_ids if value in families}
    defaults = {}
    for kind in ("image", "video"):
        kept = [item for item in column.get("defaults", {}).get(kind, [])
                if families.get(item.get("skill_id")) in selected]
        defaults[kind] = kept if any(item.get("role") == "primary" for item in kept) else []
    return defaults


def manage_skill_library(action, creator_id=None, series_id=None, skill_id=None,
                         expected_version=None, skill_ids=None, defaults=None, context=None):
    def invoke(client):
        owner = _creator(creator_id, context)
        account = f"/api/creators/{quote(owner, safe='')}/skills"
        column = f"/api/series/{quote(series_id, safe='')}/skills" if series_id else None
        if action == "get":
            data = client.request("GET", column or account)
        elif action == "add":
            data = client.request("POST", account, payload={"skill_id": skill_id,
                "request_id": _request_id("library-add", owner, skill_id, context)})
        elif action == "remove":
            data = client.request("DELETE", account + "/" + quote(skill_id, safe=""), params={"expected_version": expected_version})
        else:
            if defaults is None:
                current = client.request("GET", column)
                account_skills = client.request("GET", account)
                stage_defaults = _preserve_defaults(current, account_skills, skill_ids)
            else:
                stage_defaults = StageDefaultsArgs.model_validate(defaults).model_dump()
            data = client.request("PUT", column, payload={"expected_version": expected_version,
                "skill_ids": skill_ids, "defaults": stage_defaults})
        return {**data, "action": action}
    return _call(invoke, context)


def manage_material(action, creator_id=None, material_id=None, source_url=None,
                    offset=0, limit=20, context=None):
    def invoke(client):
        owner = _creator(creator_id, context)
        root = f"/api/creators/{quote(owner, safe='')}/materials"
        if action == "get":
            data = client.request("GET", root + "/" + quote(material_id, safe=""))
        elif action == "import_link":
            data = client.request("POST", root, payload={"source_url": source_url,
                "request_id": _request_id("material-link", owner, source_url, context)}, timeout_seconds=150)
        else:
            data = client.request("GET", root)
            items = data.get("items", [])
            data = {**data, "items": items[offset:offset + limit], "page": {
                "offset": offset, "limit": limit, "total": len(items),
                "next_offset": offset + limit if offset + limit < len(items) else None}}
        return {**data, "action": action, "creator_id": owner}
    return _call(invoke, context)
