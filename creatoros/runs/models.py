from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RunModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)


class ContentRunInput(RunModel):
    creator_id: str = Field(min_length=1, pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
    series_id: str = Field(min_length=1, pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
    series_name: str = Field(min_length=1)
    skill_name: str = Field(min_length=1)
    topic_id: str = Field(min_length=1, pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
    topic_title: str = Field(min_length=1)
    topic_brief: str | None = None


class ValidatedImage(RunModel):
    order: int = Field(ge=1)
    path: str = Field(min_length=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    byte_size: int = Field(gt=0)


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
