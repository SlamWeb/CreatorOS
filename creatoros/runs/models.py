from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from creatoros.integrations.skill_pair import SkillPair


class RunModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)


class ContentRunInput(RunModel):
    creator_id: str = Field(min_length=1, pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
    series_id: str = Field(min_length=1, pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
    series_name: str = Field(min_length=1)
    series_description: str = ""
    audience: str = ""
    skill_name: str | None = Field(default=None, min_length=1)
    composition: SkillPair | None = None
    creator_name: str | None = None
    creator_platform: str | None = None
    series_revision: int | None = None
    topic_source: str | None = None
    topic_id: str = Field(min_length=1, pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
    topic_title: str = Field(min_length=1)
    topic_brief: str | None = None

    @model_validator(mode="after")
    def binding(self):
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


class ArtifactValidation(RunModel):
    artifact_digest: str = Field(min_length=64, max_length=64)
    card_count: int = Field(gt=0)
    total_image_bytes: int = Field(gt=0)
    images: list[ValidatedImage] = Field(min_length=1)


class RunExecutionResult(RunModel):
    run_id: str
    revision_id: str
    attempt_id: str
    status: str
    artifact_directory: str | None = None
    artifact_digest: str | None = None
    producer_thread_id: str | None = None
