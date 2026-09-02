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
    JSON,
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


class PendingOperationStatus(str, Enum):
    AWAITING_APPROVAL = "awaiting_approval"
    NEEDS_CLARIFICATION = "needs_clarification"
    UNSUPPORTED = "unsupported"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STALE = "stale"


class OperationEventType(str, Enum):
    PROPOSED = "proposed"
    EDITED = "edited"
    CONFIRMED = "confirmed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STALE = "stale"


class ContentRunStatus(str, Enum):
    QUEUED = "queued"
    PRODUCING = "producing"
    VALIDATING = "validating"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    INTERRUPTED = "interrupted"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ContentAttemptStatus(str, Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    INTERRUPTED = "interrupted"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ContentRunEventType(str, Enum):
    CREATED = "created"
    STARTED = "started"
    PRODUCED = "produced"
    VALIDATED = "validated"
    APPROVED = "approved"
    REVISION_REQUESTED = "revision_requested"
    RESUMED = "resumed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"
    CANCELLED = "cancelled"


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
    content_runs: Mapped[list["ContentRun"]] = relationship(
        back_populates="topic",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class PendingOperation(TimestampMixin, Base):
    __tablename__ = "pending_operations"
    __table_args__ = (
        CheckConstraint(
            "decision_status IN ('ready', 'needs_clarification', 'unsupported')",
            name="decision_status_values",
        ),
        CheckConstraint(
            "status IN ('awaiting_approval', 'needs_clarification', 'unsupported', "
            "'succeeded', 'failed', 'cancelled', 'stale')",
            name="pending_operation_status_values",
        ),
        CheckConstraint("revision > 0", name="revision_positive"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_text: Mapped[str] = mapped_column(Text, nullable=False)
    decision_status: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[PendingOperationStatus] = mapped_column(
        SAEnum(
            PendingOperationStatus,
            name="pending_operation_status",
            native_enum=False,
            values_callable=enum_values,
        ),
        nullable=False,
    )
    plan_json: Mapped[dict | None] = mapped_column(JSON)
    preview_json: Mapped[dict | None] = mapped_column(JSON)
    confirmation_token: Mapped[str | None] = mapped_column(String(64))
    message: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    usage_json: Mapped[dict | None] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    events: Mapped[list["OperationEvent"]] = relationship(
        back_populates="pending_operation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="OperationEvent.id",
    )


class OperationEvent(Base):
    __tablename__ = "operation_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('proposed', 'edited', 'confirmed', 'succeeded', "
            "'failed', 'cancelled', 'stale')",
            name="event_type_values",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pending_operation_id: Mapped[str] = mapped_column(
        ForeignKey("pending_operations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    event_type: Mapped[OperationEventType] = mapped_column(
        SAEnum(
            OperationEventType,
            name="operation_event_type",
            native_enum=False,
            values_callable=enum_values,
        ),
        nullable=False,
    )
    payload_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    pending_operation: Mapped[PendingOperation] = relationship(back_populates="events")


class ContentRun(TimestampMixin, Base):
    __tablename__ = "content_runs"
    __table_args__ = (
        CheckConstraint("active_revision_number > 0", name="active_revision_positive"),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint(
            "status IN ('queued', 'producing', 'validating', 'awaiting_approval', "
            "'approved', 'interrupted', 'failed', 'cancelled')",
            name="content_run_status_values",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    topic_id: Mapped[str] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True, nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    status: Mapped[ContentRunStatus] = mapped_column(
        SAEnum(
            ContentRunStatus,
            name="content_run_status",
            native_enum=False,
            values_callable=enum_values,
        ),
        default=ContentRunStatus.QUEUED,
        nullable=False,
    )
    active_revision_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    approved_revision_id: Mapped[str | None] = mapped_column(String(36))
    approved_artifact_digest: Mapped[str | None] = mapped_column(String(64))
    input_snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    origin_session_id: Mapped[str | None] = mapped_column(String(120))
    context_snapshot_ref: Mapped[str | None] = mapped_column(String(500))
    producer_thread_id: Mapped[str | None] = mapped_column(String(120))
    lease_owner: Mapped[str | None] = mapped_column(String(120))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_stage: Mapped[str | None] = mapped_column(String(40))
    error_type: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    topic: Mapped[Topic] = relationship(back_populates="content_runs")
    revisions: Mapped[list["ContentRevision"]] = relationship(
        back_populates="content_run",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ContentRevision.revision_number",
    )
    events: Mapped[list["ContentRunEvent"]] = relationship(
        back_populates="content_run",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ContentRunEvent.id",
    )


class ContentRevision(TimestampMixin, Base):
    __tablename__ = "content_revisions"
    __table_args__ = (
        UniqueConstraint("content_run_id", "revision_number"),
        CheckConstraint("revision_number > 0", name="revision_number_positive"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    content_run_id: Mapped[str] = mapped_column(
        ForeignKey("content_runs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    instruction: Mapped[str | None] = mapped_column(Text)
    production_input_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    artifact_directory: Mapped[str | None] = mapped_column(String(1000))
    manifest_path: Mapped[str | None] = mapped_column(String(1000))
    artifact_digest: Mapped[str | None] = mapped_column(String(64))
    validation_json: Mapped[dict | None] = mapped_column(JSON)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    content_run: Mapped[ContentRun] = relationship(back_populates="revisions")
    attempts: Mapped[list["ContentAttempt"]] = relationship(
        back_populates="revision",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ContentAttempt.attempt_number",
    )


class ContentAttempt(Base):
    __tablename__ = "content_attempts"
    __table_args__ = (
        UniqueConstraint("revision_id", "attempt_number"),
        CheckConstraint("attempt_number > 0", name="attempt_number_positive"),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="duration_nonnegative"),
        CheckConstraint(
            "status IN ('running', 'succeeded', 'interrupted', 'failed', 'cancelled')",
            name="content_attempt_status_values",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    revision_id: Mapped[str] = mapped_column(
        ForeignKey("content_revisions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ContentAttemptStatus] = mapped_column(
        SAEnum(
            ContentAttemptStatus,
            name="content_attempt_status",
            native_enum=False,
            values_callable=enum_values,
        ),
        nullable=False,
    )
    producer_thread_id: Mapped[str | None] = mapped_column(String(120))
    output_directory: Mapped[str | None] = mapped_column(String(1000))
    usage_json: Mapped[dict | None] = mapped_column(JSON)
    trace_ref: Mapped[str | None] = mapped_column(String(1000))
    error_type: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    revision: Mapped[ContentRevision] = relationship(back_populates="attempts")


class ContentRunEvent(Base):
    __tablename__ = "content_run_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('created', 'started', 'produced', 'validated', 'approved', "
            "'revision_requested', 'resumed', 'interrupted', 'failed', 'cancelled')",
            name="content_run_event_type_values",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_run_id: Mapped[str] = mapped_column(
        ForeignKey("content_runs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    revision_id: Mapped[str | None] = mapped_column(String(36))
    attempt_id: Mapped[str | None] = mapped_column(String(36))
    event_type: Mapped[ContentRunEventType] = mapped_column(
        SAEnum(
            ContentRunEventType,
            name="content_run_event_type",
            native_enum=False,
            values_callable=enum_values,
        ),
        nullable=False,
    )
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str | None] = mapped_column(String(32))
    payload_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    content_run: Mapped[ContentRun] = relationship(back_populates="events")
