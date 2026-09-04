from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from creatoros.operations.models import OperationPlan


class ApiModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class SeriesView(ApiModel):
    id: str
    creator_id: str
    name: str
    description: str
    audience: str
    skill_name: str
    is_active: bool
    topic_count: int = Field(ge=0)
    available_topic_count: int = Field(ge=0)
    latest_run_status: str | None = None


class CreatorView(ApiModel):
    id: str
    display_name: str
    platform: str
    account_handle: str | None = None
    timezone: str
    daily_content_limit: int | None = Field(default=None, gt=0)
    is_active: bool
    series: list[SeriesView]


class TopicView(ApiModel):
    id: str
    series_id: str
    title: str
    brief: str | None = None
    source: str
    status: str
    position: int = Field(gt=0)
    existing_run_id: str | None = None
    existing_run_status: str | None = None
    existing_run_version: int | None = None
    available_actions: list[str]


class PageInfo(ApiModel):
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


T = TypeVar("T")


class PageResponse(ApiModel, Generic[T]):
    items: list[T]
    page: PageInfo


class AttemptView(ApiModel):
    id: str
    attempt_number: int = Field(gt=0)
    status: str
    producer_thread_id: str | None = None
    has_output: bool
    usage: dict[str, Any] | None = None
    trace_available: bool
    error_type: str | None = None
    error_message: str | None = None
    started_at: datetime
    heartbeat_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)


class CardView(ApiModel):
    order: int
    headline: str
    url: str
    width: int
    height: int


class PublishCopyView(ApiModel):
    title: str
    body: str
    hashtags: list[str]


class SourceView(ApiModel):
    title: str
    url: str | None = None


class RunEventView(ApiModel):
    id: int
    run_id: str
    revision_id: str | None
    attempt_id: str | None
    event_type: str
    from_status: str | None
    to_status: str | None
    created_at: datetime


class EventBatch(ApiModel):
    items: list[RunEventView]
    next_after_id: int


class RevisionView(ApiModel):
    id: str
    revision_number: int = Field(gt=0)
    instruction: str | None = None
    artifact_available: bool
    artifact_digest: str | None = None
    validation: dict[str, Any] | None = None
    validated_at: datetime | None = None
    approved_at: datetime | None = None
    attempts: list[AttemptView]
    artifact_error: str | None = None
    content_summary: str | None = None
    cards: list[CardView] = Field(default_factory=list)
    publish_copy: PublishCopyView | None = None
    sources: list[SourceView] = Field(default_factory=list)
    review_digest: str | None = None


class RunSummary(ApiModel):
    id: str
    creator_id: str
    creator_name: str
    series_id: str
    series_name: str
    topic_id: str
    topic_title: str
    status: str
    version: int = Field(gt=0)
    active_revision_number: int = Field(gt=0)
    updated_at: datetime
    completed_at: datetime | None = None
    heartbeat_at: datetime | None = None
    lease_expires_at: datetime | None = None
    retryable: bool
    error_stage: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    allowed_actions: list[str]
    cover_url: str | None = None
    card_count: int | None = None


class RunDetail(RunSummary):
    input_snapshot: dict[str, Any]
    producer_thread_id: str | None = None
    revisions: list[RevisionView]
    events_url: str


class PreviewTopicView(ApiModel):
    topic_id: str
    title: str
    brief: str | None = None


class PreviewChangeView(ApiModel):
    action: Literal["add_topics", "reorder_topics"]
    series_id: str
    creator_name: str | None = None
    series_name: str | None = None
    before_order: list[str]
    after_order: list[str]
    before_topics: list[PreviewTopicView] = Field(default_factory=list)
    after_topics: list[PreviewTopicView] = Field(default_factory=list)


class OperationPreviewView(ApiModel):
    changes: list[PreviewChangeView]


class PendingOperationView(ApiModel):
    id: str
    status: str
    decision_status: str
    revision: int = Field(gt=0)
    version: int = Field(gt=0)
    request_text: str
    scope_series_id: str | None = None
    preview: OperationPreviewView | None = None
    message: str | None = None
    confirmation_token: str | None = None
    usage: dict[str, Any] | None = None
    updated_at: datetime


class OverviewCounts(ApiModel):
    creator_count: int = Field(ge=0)
    active_creator_count: int = Field(ge=0)
    series_count: int = Field(ge=0)
    active_series_count: int = Field(ge=0)
    producing_count: int = Field(ge=0)
    awaiting_approval_count: int = Field(ge=0)


class OverviewView(ApiModel):
    counts: OverviewCounts
    creators: list[CreatorView]
    needs_attention: list[RunSummary]
    producing: list[RunSummary]
    awaiting_approval: list[RunSummary]
    pending_operations: list[PendingOperationView]


class HealthView(ApiModel):
    status: str
    database: str
    codex_available: bool
    writable_routes_enabled: bool
    operation_parser_configured: bool = False


class ErrorView(ApiModel):
    code: str
    message: str
    run_id: str | None = None


class ErrorResponse(ApiModel):
    error: ErrorView


class WriteRequest(ApiModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)


class RunStartRequest(WriteRequest):
    topic_id: str = Field(min_length=1, max_length=80)


class RunExecuteRequest(WriteRequest):
    expected_version: int = Field(gt=0)


class RunCancelRequest(WriteRequest):
    expected_version: int = Field(gt=0)


class RunApproveRequest(RunCancelRequest):
    revision_id: str = Field(min_length=1, max_length=80)
    artifact_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class RunRevisionRequest(RunCancelRequest):
    instruction: str = Field(min_length=1, max_length=10_000)


class CreatorCreateRequest(WriteRequest):
    display_name: str = Field(min_length=1, max_length=120)
    account_handle: str | None = Field(default=None, max_length=160)
    daily_content_limit: int | None = Field(default=None, gt=0)


class SeriesCreateRequest(WriteRequest):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=10_000)
    audience: str = Field(default="", max_length=4_000)


class OperationPreviewRequest(WriteRequest):
    request_text: str = Field(min_length=1, max_length=5_000)
    plan: OperationPlan
    series_id: str | None = Field(default=None, min_length=1, max_length=80)


class OperationProposeRequest(WriteRequest):
    request_text: str = Field(min_length=1, max_length=5_000)
    series_id: str | None = Field(default=None, min_length=1, max_length=80)


class OperationVersionRequest(WriteRequest):
    expected_version: int = Field(ge=1)
    expected_revision: int = Field(ge=1)


class OperationConfirmRequest(OperationVersionRequest):
    confirmation_token: str = Field(min_length=32, max_length=128)


class OperationEditRequest(OperationVersionRequest):
    instruction: str = Field(min_length=1, max_length=5_000)
