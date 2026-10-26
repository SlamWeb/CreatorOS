from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    text,
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
        # SQLite 唯一约束把 NULL 视为互不相同；未分配栏目的同名去重由部分索引兜底。
        Index(
            "uq_series_unassigned_name",
            "name",
            unique=True,
            sqlite_where=text("creator_id IS NULL"),
        ),
        CheckConstraint("replenish_threshold > 0", name="replenish_threshold_positive"),
        CheckConstraint(
            "selection_policy IN ('approval', 'auto')", name="selection_policy_values"
        ),
        CheckConstraint(
            "publish_policy IN ('approval', 'auto')", name="publish_policy_values"
        ),
        # 栏目只能是旧单 Skill 或完整 mind+production 组合，禁止半套绑定。
        CheckConstraint(
            "(skill_name IS NOT NULL AND mind_skill_id IS NULL AND production_skill_id IS NULL) "
            "OR (skill_name IS NULL AND mind_skill_id IS NOT NULL AND production_skill_id IS NOT NULL)",
            name="skill_binding_shape",
        ),
        CheckConstraint("revision > 0", name="revision_positive"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    creator_id: Mapped[str | None] = mapped_column(
        ForeignKey("creators.id", ondelete="CASCADE"), index=True, nullable=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    audience: Mapped[str] = mapped_column(Text, nullable=False)
    skill_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    mind_skill_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    production_skill_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
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
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __mapper_args__ = {"version_id_col": revision}

    creator: Mapped[Creator | None] = relationship(back_populates="series")
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
    reference_id: Mapped[str | None] = mapped_column(String(64))
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


class TopicRemoval(Base):
    """Explicit user removal; independent of production lifecycle and evidence."""
    __tablename__ = "topic_removals"
    __table_args__ = (
        CheckConstraint("status IN ('deleted', 'archived', 'dismissed')", name="removal_status_values"),
    )

    topic_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    series_id: Mapped[str] = mapped_column(ForeignKey("series.id", ondelete="CASCADE"), index=True, nullable=False)
    creator_id: Mapped[str | None] = mapped_column(String(80))
    request_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    batch_id: Mapped[str | None] = mapped_column(String(32))
    candidate_id: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class MediaCollection(TimestampMixin, Base):
    """Account-owned references to verified historical images, never image copies."""
    __tablename__ = "media_collections"
    __table_args__ = (
        UniqueConstraint("creator_id", "request_id"),
        CheckConstraint("version > 0", name="version_positive"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    creator_id: Mapped[str] = mapped_column(
        ForeignKey("creators.id", ondelete="CASCADE"), index=True, nullable=False
    )
    topic_id: Mapped[str | None] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    create_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    run_ids_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    order_ids_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    excluded_ids_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    __mapper_args__ = {"version_id_col": version}


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
        CheckConstraint("version > 0", name="version_positive"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_text: Mapped[str] = mapped_column(Text, nullable=False)
    scope_series_id: Mapped[str | None] = mapped_column(String(80), index=True)
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
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __mapper_args__ = {"version_id_col": version}

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


class WriteReceipt(Base):
    """写请求幂等回执：同一 request_id 重复提交返回首次结果，不重复写入。

    只记录成功写入；失败不留回执，客户端可用同一 request_id 安全重试。
    """

    __tablename__ = "write_receipts"
    __table_args__ = (
        CheckConstraint(
            "operation IN ('create_series', 'update_composition', 'assign_series', 'queue_topics', 'delete_series', 'edit_topic', 'rename_series')",
            name="write_operation_values",
        ),
    )

    request_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    operation: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(80), index=True)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    response_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class ProductionTask(TimestampMixin, Base):
    """One independently produced piece of a real parent Topic."""
    __tablename__ = "production_tasks"
    __table_args__ = (CheckConstraint("position >= 0", name="position_nonnegative"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    topic_id: Mapped[str] = mapped_column(ForeignKey("topics.id", ondelete="RESTRICT"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    brief: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    collection_id: Mapped[str | None] = mapped_column(ForeignKey("media_collections.id", ondelete="RESTRICT"))
    create_request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


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
    task_id: Mapped[str | None] = mapped_column(ForeignKey("production_tasks.id"), index=True)
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

    __mapper_args__ = {"version_id_col": version}

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


class ProductionQueueItem(Base):
    """Explicit authorization to execute a Run revision, not another Run state machine."""
    __tablename__ = "production_queue_items"
    __table_args__ = (
        UniqueConstraint("run_id", "revision_id"),
        CheckConstraint("expected_version > 0", name="expected_version_positive"),
        CheckConstraint(
            "status IN ('pending','running','succeeded','failed','needs_attention','cancelled')",
            name="status_values",
        ),
        Index("ix_production_queue_pending", "status", "submitted_at", "id"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("content_runs.id", ondelete="CASCADE"), nullable=False)
    revision_id: Mapped[str] = mapped_column(ForeignKey("content_revisions.id", ondelete="CASCADE"), nullable=False)
    expected_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class TopicResearchQueueItem(Base):
    """Durable authorization for one research batch; candidate artifacts remain JSON."""
    __tablename__ = "topic_research_queue_items"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','researching','ready','failed','interrupted','stale')",
            name="status_values",
        ),
        Index("ix_topic_research_queue_pending", "status", "submitted_at", "id"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    series_id: Mapped[str] = mapped_column(ForeignKey("series.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="queued", nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class ManualPublication(Base):
    __tablename__ = "manual_publications"
    __table_args__ = (CheckConstraint("platform = 'xiaohongshu'", name="platform_values"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    content_run_id: Mapped[str] = mapped_column(
        ForeignKey("content_runs.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    artifact_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    post_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PublicationMetric(Base):
    __tablename__ = "publication_metrics"
    __table_args__ = (
        CheckConstraint("views IS NULL OR views >= 0", name="views_nonnegative"),
        CheckConstraint("likes IS NULL OR likes >= 0", name="likes_nonnegative"),
        CheckConstraint("favorites IS NULL OR favorites >= 0", name="favorites_nonnegative"),
        CheckConstraint("comments IS NULL OR comments >= 0", name="comments_nonnegative"),
        CheckConstraint("shares IS NULL OR shares >= 0", name="shares_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    publication_id: Mapped[str] = mapped_column(
        ForeignKey("manual_publications.id", ondelete="CASCADE"), index=True, nullable=False
    )
    request_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    views: Mapped[int | None] = mapped_column(Integer)
    likes: Mapped[int | None] = mapped_column(Integer)
    favorites: Mapped[int | None] = mapped_column(Integer)
    comments: Mapped[int | None] = mapped_column(Integer)
    shares: Mapped[int | None] = mapped_column(Integer)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PublicationAccount(Base):
    """A local creator is pinned to a stable platform identity, not a nickname."""
    __tablename__ = "publication_accounts"
    creator_id: Mapped[str] = mapped_column(ForeignKey("creators.id"), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    account_name: Mapped[str] = mapped_column(String(200), nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PublicationRequest(TimestampMixin, Base):
    __tablename__ = "publication_requests"
    __table_args__ = (
        UniqueConstraint("account_id", "content_digest"),
        CheckConstraint("status IN ('draft','sending','verifying','unknown','published')", name="status_values"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    content_run_id: Mapped[str] = mapped_column(ForeignKey("content_runs.id"), unique=True, nullable=False)
    creator_id: Mapped[str] = mapped_column(ForeignKey("creators.id"), nullable=False)
    revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    artifact_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[str] = mapped_column(String(120), nullable=False)
    account_name: Mapped[str] = mapped_column(String(200), nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    post_url: Mapped[str | None] = mapped_column(String(1000))
    last_error: Mapped[str | None] = mapped_column(Text)
    events_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class PublicationFeedbackSetting(Base):
    __tablename__ = "publication_feedback_settings"
    creator_id: Mapped[str] = mapped_column(ForeignKey("creators.id"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class PublicationFeedbackState(Base):
    __tablename__ = "publication_feedback_states"
    request_id: Mapped[str] = mapped_column(ForeignKey("publication_requests.id"), primary_key=True)
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)


class SkillCalibration(TimestampMixin, Base):
    """User-labelled evidence and an isolated candidate; never an automatic edit."""
    __tablename__ = "skill_calibrations"
    __table_args__ = (
        CheckConstraint("status IN ('draft','applying','applied')", name="status_values"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    series_id: Mapped[str] = mapped_column(ForeignKey("series.id"), index=True, nullable=False)
    creator_id: Mapped[str | None] = mapped_column(String(80))
    skill_id: Mapped[str] = mapped_column(String(120), nullable=False)
    base_skill_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    evidence_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft")
    apply_json: Mapped[dict | None] = mapped_column(JSON)
    applied_digest: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(Text)


class SkillIteration(TimestampMixin, Base):
    """Durable bounded calibration; never authority to activate a shared Skill."""
    __tablename__ = "skill_iterations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    creator_id: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    series_id: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    skill_id: Mapped[str] = mapped_column(String(120), nullable=False)
    topic_id: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False, default="queued")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    config_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    state_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
