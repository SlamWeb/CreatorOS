from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MANIFEST_FILENAME = "social_content_pack.json"
NonEmptyText = Annotated[str, Field(min_length=1)]
CardKind = Literal["cover", "content", "summary", "sources", "cta"]


class ContentModel(BaseModel):
    """Strict contract for one Codex-produced social content package."""

    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)


class SourceRef(ContentModel):
    source_id: NonEmptyText
    title: NonEmptyText
    url: str | None = None
    note: str | None = None


class CarouselCard(ContentModel):
    order: int = Field(ge=1)
    kind: CardKind
    section: str | None = None
    headline: NonEmptyText
    body: str | None = None
    highlights: list[NonEmptyText] = Field(default_factory=list)
    visual_brief: str | None = None
    image_path: NonEmptyText

    @field_validator("image_path")
    @classmethod
    def validate_relative_image_path(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        path = PurePosixPath(normalized)
        if (
            path.is_absolute()
            or PureWindowsPath(value).is_absolute()
            or ".." in path.parts
            or "://" in value
        ):
            raise ValueError("image_path 必须是内容目录内的安全相对路径。")
        return path.as_posix()


class PublicationCopy(ContentModel):
    title: NonEmptyText
    body: NonEmptyText
    hashtags: list[NonEmptyText] = Field(default_factory=list)


class SocialContentPack(ContentModel):
    schema_version: Literal[1] = 1
    pack_id: NonEmptyText
    creator_id: NonEmptyText
    series_id: NonEmptyText
    topic_id: NonEmptyText
    topic_title: NonEmptyText
    skill_name: NonEmptyText
    generated_at: NonEmptyText
    platform: Literal["xiaohongshu"] = "xiaohongshu"
    content_summary: NonEmptyText
    cards: list[CarouselCard] = Field(min_length=1)
    publish_copy: PublicationCopy
    sources: list[SourceRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_order_and_identity(self) -> "SocialContentPack":
        orders = [card.order for card in self.cards]
        if orders != list(range(1, len(self.cards) + 1)):
            raise ValueError("cards 必须按列表顺序从 1 连续编号。")
        image_paths = [card.image_path for card in self.cards]
        if len(image_paths) != len(set(image_paths)):
            raise ValueError("每张卡片必须引用不同的 image_path。")
        source_ids = [source.source_id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source_id 不能重复。")
        return self

    @classmethod
    def load(cls, directory: str | Path) -> "SocialContentPack":
        root = Path(directory)
        manifest_path = root / MANIFEST_FILENAME
        pack = cls.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        missing = [card.image_path for card in pack.cards if not (root / card.image_path).is_file()]
        if missing:
            raise FileNotFoundError(f"SocialContentPack 缺少图片文件：{', '.join(missing)}")
        return pack
