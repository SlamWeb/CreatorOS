from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from creatoros.integrations.skill_pair import SkillPair
from creatoros.integrations.topic_references import TaskReferences
from .task_contracts import TaskExecutionSpec


class RunModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)


class SkillOverrides(RunModel):
    """An explicit one-run selection, never a change to a Series binding."""
    skill_id: str | None = Field(default=None, min_length=1, max_length=120)
    mind_skill_id: str | None = Field(default=None, min_length=1, max_length=120)
    production_skill_id: str | None = Field(default=None, min_length=1, max_length=120)
    expected_digests: dict[str, str] = Field(description="明确选择的每个 Skill ID 与读取时的完整目录 SHA256。")

    @model_validator(mode="after")
    def explicit_selection(self):
        ids = [value for value in (self.skill_id, self.mind_skill_id, self.production_skill_id) if value]
        if not ids or (self.skill_id and (self.mind_skill_id or self.production_skill_id)):
            raise ValueError("请选择完整 Skill 或内容/呈现 Skill，不能混用两种组合。")
        if any(not re.fullmatch(r"(?:knowledge-to-carousel|[a-z0-9-]+--[a-f0-9]{16})", value) for value in ids):
            raise ValueError("必须选择 Skill 库的真实 ID，不能提供本地路径。")
        if set(self.expected_digests) != set(ids) or any(
                not re.fullmatch(r"[a-f0-9]{64}", value) for value in self.expected_digests.values()):
            raise ValueError("请提供每个已选择 Skill 当前的完整目录 digest。")
        return self


class ContentRunInput(RunModel):
    production_protocol: Literal["legacy", "native-v1", "task-v1", "external-import"] = "legacy"
    execution_spec: TaskExecutionSpec | None = None
    creator_id: str = Field(min_length=1, pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
    series_id: str = Field(min_length=1, pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
    series_name: str = Field(min_length=1)
    series_description: str = ""
    audience: str = ""
    skill_name: str | None = Field(default=None, min_length=1)
    skill_path: str | None = None
    skill_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    composition: SkillPair | None = None
    creator_name: str | None = None
    creator_platform: str | None = None
    series_revision: int | None = None
    topic_source: str | None = None
    topic_id: str = Field(min_length=1, pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
    task_id: str | None = None
    task_skill_summary: str | None = None
    topic_title: str = Field(min_length=1)
    topic_brief: str | None = None
    task_references: TaskReferences | None = None
    skill_overrides: SkillOverrides | None = None

    @model_validator(mode="after")
    def binding(self):
        if self.production_protocol == "task-v1":
            if self.execution_spec is None or self.skill_name is not None or self.composition is not None:
                raise ValueError("统一任务使用 execution_spec，不能同时绑定旧 Skill 组合。")
            return self
        if self.execution_spec is not None:
            raise ValueError("execution_spec 需要 task-v1 协议。")
        if self.production_protocol == "external-import":
            if self.skill_name is not None or self.composition is not None:
                raise ValueError("外部导入产物不能声称由栏目 Skill 生成。")
            return self
        if (self.skill_name is None) == (self.composition is None):
            raise ValueError("Run 必须有单 Skill 或完整双 Skill 快照，不能同时存在。")
        return self


class ValidatedImage(RunModel):
    order: int = Field(ge=1)
    path: str = Field(min_length=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    byte_size: int = Field(gt=0)
    sha256: str | None = None


class ValidatedVideo(RunModel):
    path: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    duration_seconds: float = Field(gt=0)
    byte_size: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    has_audio: bool
    video_codec: str
    audio_codec: str | None = None


class ArtifactValidation(RunModel):
    artifact_digest: str = Field(min_length=64, max_length=64)
    card_count: int = Field(ge=0)
    total_image_bytes: int = Field(ge=0)
    images: list[ValidatedImage]
    video: ValidatedVideo | None = None


class RunExecutionResult(RunModel):
    run_id: str
    revision_id: str
    attempt_id: str
    status: str
    artifact_directory: str | None = None
    artifact_digest: str | None = None
    producer_thread_id: str | None = None
