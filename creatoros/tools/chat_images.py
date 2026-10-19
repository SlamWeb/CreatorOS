"""Session-owned image capabilities; never accept a model-selected session/path."""
import hashlib
import json
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .results import ToolResult
from .studio import _call


class ReadImageArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    attachment_id: str | None = None
    skill_id: str | None = None
    path: str | None = None
    run_id: str | None = None
    revision_id: str | None = None
    image_index: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def source(self):
        if sum(bool(x) for x in (self.attachment_id, self.skill_id, self.run_id)) != 1:
            raise ValueError("只选择一个图片来源：会话附件、Skill 参考图或内容版本。")
        if bool(self.skill_id) != bool(self.path):
            raise ValueError("Skill 图片需要 skill_id 和相对 path。")
        if bool(self.run_id) != bool(self.revision_id) or bool(self.run_id) != bool(self.image_index):
            raise ValueError("产物图片需要 run_id、revision_id 和从 1 开始的 image_index。")
        return self


class ExtractSkillImagesArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    attachment_ids: list[str] = Field(min_length=1, max_length=6)
    mode: Literal["single", "mind", "visual", "pair"] = "single"
    instruction: str = Field(default="", max_length=4000)


class CreateSkillDraftArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)
    description: str = Field(min_length=1, max_length=1000)
    content: str = Field(min_length=1, max_length=20000,
        description="用户已提供的提示词或完整SKILL.md，保留原文，不重新提炼或改写核心要求。")
    role: Literal["legacy_end_to_end", "mind", "production"] = "legacy_end_to_end"
    attachment_ids: list[str] = Field(default_factory=list, max_length=6,
        description="当前会话中用户明确指定作为Skill参考的图片ID；没有参考图可为空。")
    display_name: str | None = Field(default=None, min_length=1, max_length=80)


class ImportContentImagesArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)
    series_id: str = Field(min_length=1)
    attachment_ids: list[str] = Field(min_length=1, max_length=20)
    title: str = Field(min_length=1, max_length=240)
    body: str = Field(default="", max_length=20000)


def _action(operation, payload, context):
    sid = getattr(context, "agent_session_id", None)
    if not sid:
        return ToolResult("图片操作需要当前 Studio 对话。", True, "session_required")
    if operation != "read":
        turn = getattr(context, "user_request_id", None)
        if not turn:
            return ToolResult("缺少当前用户请求标识。", True, "request_required")
        raw = json.dumps([sid, turn, operation, payload], sort_keys=True, ensure_ascii=False)
        payload = {**payload, "request_id": hashlib.sha256(raw.encode()).hexdigest()}
    result = _call(lambda client: client.request(
        "POST", f"/api/agent/sessions/{quote(sid, safe='')}/image-actions/{operation}", payload=payload), context)
    if operation == "read" and not result.is_error:
        result.details["image_attachments"] = [json.loads(result.content)["attachment"]]
    return result


def read_image(context=None, **kwargs):
    return _action("read", ReadImageArgs(**kwargs).model_dump(exclude_none=True), context)


def extract_skill_from_images(attachment_ids, mode="single", instruction="", context=None):
    return _action("extract", dict(attachment_ids=attachment_ids, mode=mode, instruction=instruction), context)


def create_skill_draft(name, description, content, role="legacy_end_to_end", attachment_ids=None,
                       display_name=None, context=None):
    payload = CreateSkillDraftArgs(name=name, description=description, content=content, role=role,
        attachment_ids=attachment_ids or [], display_name=display_name).model_dump()
    return _action("author", payload, context)


def import_content_images(series_id, attachment_ids, title, body="", context=None):
    return _action("import", dict(series_id=series_id, attachment_ids=attachment_ids, title=title, body=body), context)
