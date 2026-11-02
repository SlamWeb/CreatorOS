"""Common persisted production values and deterministic acceptance results."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ProductionModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)


class CodexUsage(ProductionModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    input_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_output_tokens: int = Field(default=0, ge=0)


class ValidatedImage(ProductionModel):
    order: int = Field(ge=1)
    path: str = Field(min_length=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    byte_size: int = Field(gt=0)
    sha256: str | None = None


class ValidatedVideo(ProductionModel):
    path: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    duration_seconds: float = Field(gt=0)
    byte_size: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    has_audio: bool
    video_codec: str
    audio_codec: str | None = None


class ArtifactValidation(ProductionModel):
    artifact_digest: str = Field(min_length=64, max_length=64)
    card_count: int = Field(ge=0)
    total_image_bytes: int = Field(ge=0)
    images: list[ValidatedImage]
    video: ValidatedVideo | None = None
