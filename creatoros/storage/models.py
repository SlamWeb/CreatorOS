from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def enum_values(enum_class: type[Enum]) -> list[str]:
    return [str(item.value) for item in enum_class]


class CreatorPlatform(str, Enum):
    XIAOHONGSHU = "xiaohongshu"


class OperationPolicy(str, Enum):
    APPROVAL = "approval"
    AUTO = "auto"


class TopicSource(str, Enum):
    RESEARCH = "research"
    MANUAL = "manual"


class TopicStatus(str, Enum):
    QUEUED = "queued"
    PRODUCING = "producing"
    READY = "ready"
    PUBLISHED = "published"
    FAILED = "failed"
    SKIPPED = "skipped"


NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class Creator(TimestampMixin, Base):
    __tablename__ = "creators"
    __table_args__ = (
        CheckConstraint(
            "daily_content_limit IS NULL OR daily_content_limit > 0",
            name="daily_content_limit_positive",
        ),
        CheckConstraint("platform IN ('xiaohongshu')", name="platform_values"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    platform: Mapped[CreatorPlatform] = mapped_column(
        SAEnum(
            CreatorPlatform,
            name="creator_platform",
            native_enum=False,
            values_callable=enum_values,
        ),
        nullable=False,
    )
    account_handle: Mapped[str | None] = mapped_column(String(160))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai", nullable=False)
    daily_content_limit: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    series: Mapped[list["Series"]] = relationship(
        back_populates="creator",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Series(TimestampMixin, Base):
    __tablename__ = "series"
    __table_args__ = (
        UniqueConstraint("creator_id", "name"),
        CheckConstraint("replenish_threshold > 0", name="replenish_threshold_positive"),
        CheckConstraint(
            "selection_policy IN ('approval', 'auto')", name="selection_policy_values"
        ),
        CheckConstraint(
            "publish_policy IN ('approval', 'auto')", name="publish_policy_values"
        ),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    creator_id: Mapped[str] = mapped_column(
        ForeignKey("creators.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    audience: Mapped[str] = mapped_column(Text, nullable=False)
    skill_name: Mapped[str] = mapped_column(String(120), nullable=False)
    selection_policy: Mapped[OperationPolicy] = mapped_column(
        SAEnum(
            OperationPolicy,
            name="series_selection_policy",
            native_enum=False,
            values_callable=enum_values,
        ),
        default=OperationPolicy.APPROVAL,
        nullable=False,
    )
    publish_policy: Mapped[OperationPolicy] = mapped_column(
        SAEnum(
            OperationPolicy,
            name="series_publish_policy",
            native_enum=False,
            values_callable=enum_values,
        ),
        default=OperationPolicy.APPROVAL,
        nullable=False,
    )
    replenish_threshold: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    creator: Mapped[Creator] = relationship(back_populates="series")
    topics: Mapped[list["Topic"]] = relationship(
        back_populates="series",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Topic.position",
    )


class Topic(TimestampMixin, Base):
    __tablename__ = "topics"
    __table_args__ = (
        UniqueConstraint("series_id", "position"),
        CheckConstraint("position > 0", name="position_positive"),
        CheckConstraint("source IN ('research', 'manual')", name="source_values"),
        CheckConstraint(
            "status IN ('queued', 'producing', 'ready', 'published', 'failed', 'skipped')",
            name="status_values",
        ),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    series_id: Mapped[str] = mapped_column(
        ForeignKey("series.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    brief: Mapped[str | None] = mapped_column(Text)
    source: Mapped[TopicSource] = mapped_column(
        SAEnum(
            TopicSource,
            name="topic_source",
            native_enum=False,
            values_callable=enum_values,
        ),
        nullable=False,
    )
    status: Mapped[TopicStatus] = mapped_column(
        SAEnum(
            TopicStatus,
            name="topic_status",
            native_enum=False,
            values_callable=enum_values,
        ),
        default=TopicStatus.QUEUED,
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    series: Mapped[Series] = relationship(back_populates="topics")
