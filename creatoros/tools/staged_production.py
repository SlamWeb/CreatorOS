"""Object-level Skill references and source media, using guarded Studio HTTP."""
from hashlib import sha256
import json
from typing import Annotated, Literal, Union
from urllib.parse import quote
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from creatoros.integrations.studio import StudioClientError
from .argument_contract import ActionContract
from .studio import _call


class _SkillLibraryAction(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    creator_id: str | None = Field(default=None, min_length=1, max_length=80,
                                   description="内部宿主绑定账号；模型不传。")


class GetSkillLibraryArgs(_SkillLibraryAction):
    action: Literal["get"]
    series_id: str | None = Field(default=None, min_length=1, max_length=80,
                                  description="省略读取账号库；指定时读取此栏目池。")


class AddSkillLibraryArgs(_SkillLibraryAction):
    action: Literal["add"]
    skill_id: str = Field(min_length=1, max_length=120)

class RemoveSkillLibraryArgs(_SkillLibraryAction):
    action: Literal["remove"]
    skill_id: str = Field(min_length=1, max_length=120)
    expected_version: int = Field(ge=0)


class SetSkillPoolArgs(_SkillLibraryAction):
    action: Literal["set_pool"]
    series_id: str = Field(min_length=1, max_length=80)
    expected_version: int = Field(ge=0)
    skill_ids: list[str] = Field(max_length=100)


ManageSkillLibraryArgs = ActionContract(Annotated[
    Union[GetSkillLibraryArgs, AddSkillLibraryArgs, RemoveSkillLibraryArgs, SetSkillPoolArgs],
    Field(discriminator="action"),
])


class ManageMaterialArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    action: Literal["list", "get", "import_link", "import_status", "analyze", "analysis", "analysis_status"]
    creator_id: str | None = Field(default=None, min_length=1, max_length=80, description="省略用当前账号。")
    material_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    source_url: str | None = Field(default=None, min_length=1, max_length=10000, description="用户提供的音乐/视频分享原文。")
    import_id: str | None = Field(default=None, min_length=1, max_length=100)
    start: float = Field(default=0, ge=0, description="音乐分析片段起点，秒。")
    duration: float | None = Field(default=None, gt=0, le=300, description="音乐分析片段时长，秒；不填使用默认。")
    force: bool = Field(default=False, description="明确要求重新分析时设为 true。")
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def action_fields(self):
        if self.action in {"get", "analyze", "analysis", "analysis_status"} and (not self.material_id or self.source_url):
            raise ValueError("此操作需要 material_id，不能带分享链接。")
        if self.action == "import_link" and (not self.source_url or self.material_id):
            raise ValueError("import_link 需要 source_url，不能带素材 ID。")
        if self.action == "import_status" and (not self.import_id or self.material_id or self.source_url):
            raise ValueError("import_status 只接受导入任务 ID。")
        if self.action != "import_status" and self.import_id:
            raise ValueError("只有 import_status 接受导入任务 ID。")
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
                         expected_version=None, skill_ids=None, context=None):
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
            current = client.request("GET", column)
            account_skills = client.request("GET", account)
            stage_defaults = _preserve_defaults(current, account_skills, skill_ids)
            data = client.request("PUT", column, payload={"expected_version": expected_version,
                "skill_ids": skill_ids, "defaults": stage_defaults})
        return {**data, "action": action}
    return _call(invoke, context)


def manage_material(action, creator_id=None, material_id=None, source_url=None,
                    import_id=None, start=0, duration=None, force=False, offset=0, limit=20, context=None):
    def invoke(client):
        owner = _creator(creator_id, context)
        root = f"/api/creators/{quote(owner, safe='')}/materials"
        if action == "analyze":
            data = client.request("POST", root + "/" + quote(material_id, safe="") + "/analysis/run", payload={"start": start, "duration": duration, "force": force})
        elif action in {"analysis", "analysis_status"}:
            suffix = "/analysis/status" if action == "analysis_status" else "/analysis"
            data = client.request("GET", root + "/" + quote(material_id, safe="") + suffix)
        elif action == "get":
            data = client.request("GET", root + "/" + quote(material_id, safe=""))
        elif action == "import_link":
            data = client.request("POST", root + "/imports", payload={"source_url": source_url})
        elif action == "import_status":
            data = client.request("GET", root + "/imports/" + quote(import_id, safe=""))
        else:
            data = client.request("GET", root)
            items = data.get("items", [])
            data = {**data, "items": items[offset:offset + limit], "page": {
                "offset": offset, "limit": limit, "total": len(items),
                "next_offset": offset + limit if offset + limit < len(items) else None}}
        return {**data, "action": action, "creator_id": owner}
    return _call(invoke, context)
