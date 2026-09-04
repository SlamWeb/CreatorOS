from __future__ import annotations

import re
from datetime import timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from creatoros.storage import (
    ContentAttempt,
    ContentRevision,
    ContentRun,
    ContentRunStatus,
    Creator,
    Database,
    PendingOperation,
    PendingOperationStatus,
    Series,
    Topic,
)

from .schemas import (
    AttemptView,
    CreatorView,
    OverviewCounts,
    OverviewView,
    PageInfo,
    PageResponse,
    PendingOperationView,
    RevisionView,
    RunDetail,
    RunSummary,
    SeriesView,
    TopicView,
)


_ACTIONABLE_OPERATION_STATUSES = {
    PendingOperationStatus.AWAITING_APPROVAL,
    PendingOperationStatus.NEEDS_CLARIFICATION,
    PendingOperationStatus.UNSUPPORTED,
    PendingOperationStatus.STALE,
}
_ATTENTION_RUN_STATUSES = {
    ContentRunStatus.INTERRUPTED,
    ContentRunStatus.FAILED,
}
_ACTIVE_RUN_STATUSES = {ContentRunStatus.PRODUCING, ContentRunStatus.VALIDATING}


def _error_message(value: str | None) -> str | None:
    if not value:
        return None
    # Error text is useful in the inspector, but an API response must not reveal
    # the workstation's absolute paths or an unbounded subprocess dump.
    sanitized = re.sub(r"(?i)(?:[A-Z]:\\|/)[^\s'\"]+", "<local-path>", value)
    return sanitized[:500]


def _timestamp(value):
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


class StudioQueryService:
    """Builds explicit read models; no query can start or recover a run."""

    def __init__(self, database: Database, *, artifacts=None):
        self.database = database
        self.artifacts = artifacts

    def health_database(self) -> None:
        with self.database.session() as session:
            session.scalar(select(1))

    def list_creators(self, *, offset: int, limit: int) -> PageResponse[CreatorView]:
        with self.database.session() as session:
            total = int(session.scalar(select(func.count()).select_from(Creator)) or 0)
            creators = list(
                session.scalars(
                    select(Creator)
                    .order_by(Creator.created_at, Creator.id)
                    .offset(offset)
                    .limit(limit)
                )
            )
            series = list(session.scalars(select(Series).order_by(Series.created_at, Series.id)))
            topics = list(session.scalars(select(Topic).order_by(Topic.position, Topic.id)))
            runs = list(session.scalars(select(ContentRun).order_by(ContentRun.updated_at.desc())))
            return PageResponse(
                items=self._creator_views(creators, series, topics, runs),
                page=PageInfo(offset=offset, limit=limit, total=total),
            )

    def get_creator(self, creator_id: str) -> CreatorView | None:
        with self.database.session() as session:
            creator = session.get(Creator, creator_id)
            if creator is None:
                return None
            series = list(
                session.scalars(
                    select(Series)
                    .where(Series.creator_id == creator_id)
                    .order_by(Series.created_at, Series.id)
                )
            )
            topics = list(
                session.scalars(
                    select(Topic)
                    .where(Topic.series_id.in_([item.id for item in series]))
                    .order_by(Topic.position, Topic.id)
                )
            ) if series else []
            runs = list(session.scalars(select(ContentRun).order_by(ContentRun.updated_at.desc())))
            return self._creator_views([creator], series, topics, runs)[0]

    def get_series(self, series_id: str) -> SeriesView | None:
        with self.database.session() as session:
            series = session.get(Series, series_id)
            if series is None:
                return None
            topics = list(session.scalars(select(Topic).where(Topic.series_id == series_id)))
            runs = list(
                session.scalars(
                    select(ContentRun)
                    .where(ContentRun.topic_id.in_([item.id for item in topics]))
                    .order_by(ContentRun.updated_at.desc())
                )
            ) if topics else []
            return self._series_view(series, topics, runs)

    def list_topics(
        self, series_id: str, *, offset: int, limit: int
    ) -> PageResponse[TopicView] | None:
        with self.database.session() as session:
            if session.get(Series, series_id) is None:
                return None
            total = int(
                session.scalar(
                    select(func.count()).select_from(Topic).where(Topic.series_id == series_id)
                )
                or 0
            )
            topics = list(
                session.scalars(
                    select(Topic)
                    .where(Topic.series_id == series_id)
                    .order_by(Topic.position, Topic.created_at, Topic.id)
                    .offset(offset)
                    .limit(limit)
                )
            )
            topic_ids = [item.id for item in topics]
            runs = list(
                session.scalars(
                    select(ContentRun)
                    .where(ContentRun.topic_id.in_(topic_ids))
                    .order_by(ContentRun.updated_at.desc())
                )
            ) if topic_ids else []
            return PageResponse(
                items=[self._topic_view(topic, runs) for topic in topics],
                page=PageInfo(offset=offset, limit=limit, total=total),
            )

    def list_runs(
        self,
        *,
        offset: int,
        limit: int,
        status: ContentRunStatus | None = None,
        creator_id: str | None = None,
    ) -> PageResponse[RunSummary]:
        with self.database.session() as session:
            statement = select(ContentRun).join(Topic).join(Series).join(Creator)
            count_statement = select(func.count(ContentRun.id)).select_from(ContentRun).join(Topic).join(Series).join(Creator)
            if status is not None:
                statement = statement.where(ContentRun.status == status)
                count_statement = count_statement.where(ContentRun.status == status)
            if creator_id is not None:
                statement = statement.where(Series.creator_id == creator_id)
                count_statement = count_statement.where(Series.creator_id == creator_id)
            total = int(session.scalar(count_statement) or 0)
            rows = list(
                session.execute(
                    statement.order_by(ContentRun.updated_at.desc(), ContentRun.id).offset(offset).limit(limit)
                ).scalars()
            )
            creator_by_id = {item.id: item for item in session.scalars(select(Creator))}
            series_by_id = {item.id: item for item in session.scalars(select(Series))}
            topics_by_id = {item.id: item for item in session.scalars(select(Topic))}
            return PageResponse(
                items=[self._run_summary(run, creator_by_id, series_by_id, topics_by_id) for run in rows],
                page=PageInfo(offset=offset, limit=limit, total=total),
            )

    def get_run(self, run_id: str) -> RunDetail | None:
        with self.database.session() as session:
            run = session.get(ContentRun, run_id)
            if run is None:
                return None
            creator = session.get(Creator, run.input_snapshot_json.get("creator_id", ""))
            series = session.get(Series, run.input_snapshot_json.get("series_id", ""))
            topic = session.get(Topic, run.topic_id)
            summary = self._run_summary(
                run,
                {creator.id: creator} if creator else {},
                {series.id: series} if series else {},
                {topic.id: topic} if topic else {},
            )
            revisions = list(
                session.scalars(
                    select(ContentRevision)
                    .where(ContentRevision.content_run_id == run_id)
                    .order_by(ContentRevision.revision_number)
                )
            )
            attempts = list(
                session.scalars(
                    select(ContentAttempt).where(
                        ContentAttempt.revision_id.in_([item.id for item in revisions])
                    )
                )
            ) if revisions else []
            attempts_by_revision: dict[str, list[ContentAttempt]] = {}
            for attempt in attempts:
                attempts_by_revision.setdefault(attempt.revision_id, []).append(attempt)
            detail = RunDetail(
                **summary.model_dump(),
                input_snapshot=dict(run.input_snapshot_json),
                producer_thread_id=run.producer_thread_id,
                revisions=[
                    self._revision_view(revision, attempts_by_revision.get(revision.id, []))
                    for revision in revisions
                ],
                events_url=f"/api/runs/{run_id}/events",
            )
        if self.artifacts is not None:
            for revision in detail.revisions:
                if revision.artifact_available or revision.artifact_digest:
                    for key, value in self.artifacts.projection(run_id, revision.id).items():
                        setattr(revision, key, value)
        return detail

    def list_operations(self, *, offset: int, limit: int) -> PageResponse[PendingOperationView]:
        with self.database.session() as session:
            statement = (
                select(PendingOperation)
                .where(PendingOperation.status.in_(_ACTIONABLE_OPERATION_STATUSES))
                .order_by(PendingOperation.updated_at.desc(), PendingOperation.id)
            )
            total = int(
                session.scalar(
                    select(func.count()).select_from(PendingOperation).where(
                        PendingOperation.status.in_(_ACTIONABLE_OPERATION_STATUSES)
                    )
                )
                or 0
            )
            items = list(session.scalars(statement.offset(offset).limit(limit)))
            return PageResponse(
                items=[self._operation_view(item, include_token=False) for item in items],
                page=PageInfo(offset=offset, limit=limit, total=total),
            )

    def get_operation(self, operation_id: str) -> PendingOperationView | None:
        with self.database.session() as session:
            operation = session.get(PendingOperation, operation_id)
            if operation is None:
                return None
            return self._operation_view(operation, include_token=True)

    def overview(self) -> OverviewView:
        with self.database.session() as session:
            creators = list(session.scalars(select(Creator).order_by(Creator.created_at, Creator.id)))
            series = list(session.scalars(select(Series).order_by(Series.created_at, Series.id)))
            topics = list(session.scalars(select(Topic).order_by(Topic.position, Topic.id)))
            runs = list(session.scalars(select(ContentRun).order_by(ContentRun.updated_at.desc(), ContentRun.id)))
            operations = list(
                session.scalars(
                    select(PendingOperation)
                    .where(PendingOperation.status.in_(_ACTIONABLE_OPERATION_STATUSES))
                    .order_by(PendingOperation.updated_at.desc(), PendingOperation.id)
                )
            )
            creator_views = self._creator_views(creators, series, topics, runs)
            creator_by_id = {item.id: item for item in creators}
            series_by_id = {item.id: item for item in series}
            topics_by_id = {item.id: item for item in topics}
            run_views = [self._run_summary(run, creator_by_id, series_by_id, topics_by_id) for run in runs]
            counts = OverviewCounts(
                creator_count=len(creators),
                active_creator_count=sum(item.is_active for item in creators),
                series_count=len(series),
                active_series_count=sum(item.is_active for item in series),
                producing_count=sum(run.status in _ACTIVE_RUN_STATUSES for run in runs),
                awaiting_approval_count=sum(run.status is ContentRunStatus.AWAITING_APPROVAL for run in runs),
            )
            return OverviewView(
                counts=counts,
                creators=creator_views,
                needs_attention=[item for item in run_views if item.status in {status.value for status in _ATTENTION_RUN_STATUSES}],
                producing=[item for item in run_views if item.status in {status.value for status in _ACTIVE_RUN_STATUSES}],
                awaiting_approval=[item for item in run_views if item.status == ContentRunStatus.AWAITING_APPROVAL.value],
                pending_operations=[self._operation_view(item, include_token=False) for item in operations],
            )

    def _creator_views(self, creators, series, topics, runs) -> list[CreatorView]:
        topics_by_series: dict[str, list[Topic]] = {}
        for topic in topics:
            topics_by_series.setdefault(topic.series_id, []).append(topic)
        runs_by_topic: dict[str, list[ContentRun]] = {}
        for run in runs:
            runs_by_topic.setdefault(run.topic_id, []).append(run)
        series_by_creator: dict[str, list[Series]] = {}
        for item in series:
            series_by_creator.setdefault(item.creator_id, []).append(item)
        return [
            CreatorView(
                id=creator.id,
                display_name=creator.display_name,
                platform=creator.platform.value,
                account_handle=creator.account_handle,
                timezone=creator.timezone,
                daily_content_limit=creator.daily_content_limit,
                is_active=creator.is_active,
                series=[
                    self._series_view(item, topics_by_series.get(item.id, []), [run for topic in topics_by_series.get(item.id, []) for run in runs_by_topic.get(topic.id, [])])
                    for item in series_by_creator.get(creator.id, [])
                ],
            )
            for creator in creators
        ]

    @staticmethod
    def _series_view(series: Series, topics: list[Topic], runs: list[ContentRun]) -> SeriesView:
        latest_status = max(runs, key=lambda run: run.updated_at).status.value if runs else None
        available = sum("start" in StudioQueryService._topic_view(topic, runs).available_actions for topic in topics)
        return SeriesView(
            id=series.id,
            creator_id=series.creator_id,
            name=series.name,
            description=series.description,
            audience=series.audience,
            skill_name=series.skill_name,
            is_active=series.is_active,
            topic_count=len(topics),
            available_topic_count=available,
            latest_run_status=latest_status,
        )

    @staticmethod
    def _topic_view(topic: Topic, runs: list[ContentRun]) -> TopicView:
        related = sorted((run for run in runs if run.topic_id == topic.id), key=lambda item: item.updated_at, reverse=True)
        run = related[0] if related else None
        if run is None:
            actions = ["start"] if topic.status.value == "queued" else ["view"]
        elif run.status is ContentRunStatus.QUEUED:
            actions = ["start", "view"]
        elif run.status in {ContentRunStatus.INTERRUPTED, ContentRunStatus.FAILED} and (run.status is ContentRunStatus.INTERRUPTED or run.retryable):
            actions = ["resume", "view"]
        else:
            actions = ["view"]
        return TopicView(
            id=topic.id,
            series_id=topic.series_id,
            title=topic.title,
            brief=topic.brief,
            source=topic.source.value,
            status=topic.status.value,
            position=topic.position,
            existing_run_id=run.id if run else None,
            existing_run_status=run.status.value if run else None,
            existing_run_version=run.version if run else None,
            available_actions=actions,
        )

    def _run_summary(self, run, creators, series_by_id, topics_by_id) -> RunSummary:
        snapshot = run.input_snapshot_json or {}
        creator = creators.get(snapshot.get("creator_id"))
        series = series_by_id.get(snapshot.get("series_id"))
        topic = topics_by_id.get(run.topic_id)
        cover_url, card_count = None, None
        if self.artifacts is not None:
            revision = next((item for item in run.revisions if item.revision_number == run.active_revision_number), None)
            validation = revision.validation_json if revision else None
            if validation and validation.get("images"):
                info = validation["images"][0]
                card_count = validation.get("card_count")
                if info.get("sha256") and revision.artifact_digest:
                    cover_url = f"/api/runs/{run.id}/revisions/{revision.id}/cards/{info['order']}?digest={revision.artifact_digest}&checksum={info['sha256']}"
        return RunSummary(
            id=run.id,
            creator_id=snapshot.get("creator_id", series.creator_id if series else ""),
            creator_name=creator.display_name if creator else snapshot.get("creator_id", "unknown"),
            series_id=snapshot.get("series_id", series.id if series else ""),
            series_name=series.name if series else snapshot.get("series_name", "unknown"),
            topic_id=run.topic_id,
            topic_title=snapshot.get("topic_title", topic.title if topic else run.topic_id),
            status=run.status.value,
            version=run.version,
            active_revision_number=run.active_revision_number,
            updated_at=_timestamp(run.updated_at),
            completed_at=_timestamp(run.completed_at),
            heartbeat_at=_timestamp(run.heartbeat_at),
            lease_expires_at=_timestamp(run.lease_expires_at),
            retryable=run.retryable,
            error_stage=run.failure_stage,
            error_type=run.error_type,
            error_message=_error_message(run.error_message),
            allowed_actions=StudioQueryService._run_actions(run),
            cover_url=cover_url,
            card_count=card_count,
        )

    @staticmethod
    def _run_actions(run: ContentRun) -> list[str]:
        if run.status is ContentRunStatus.QUEUED:
            return ["execute", "cancel"]
        if run.status is ContentRunStatus.INTERRUPTED or (run.status is ContentRunStatus.FAILED and run.retryable):
            return ["resume", "revise", "cancel"]
        if run.status is ContentRunStatus.AWAITING_APPROVAL:
            return ["approve", "revise", "cancel"]
        if run.status is ContentRunStatus.FAILED:
            return ["revise", "cancel"]
        return ["view"]

    @staticmethod
    def _revision_view(revision, attempts: list[ContentAttempt]) -> RevisionView:
        artifact_dir = revision.artifact_directory
        manifest = Path(artifact_dir) / "social_content_pack.json" if artifact_dir else None
        return RevisionView(
            id=revision.id,
            revision_number=revision.revision_number,
            instruction=revision.instruction,
            artifact_available=bool(manifest and manifest.is_file()),
            artifact_digest=revision.artifact_digest,
            validation=revision.validation_json,
            validated_at=_timestamp(revision.validated_at),
            approved_at=_timestamp(revision.approved_at),
            attempts=[StudioQueryService._attempt_view(item) for item in sorted(attempts, key=lambda item: item.attempt_number)],
        )

    @staticmethod
    def _attempt_view(attempt: ContentAttempt) -> AttemptView:
        return AttemptView(
            id=attempt.id,
            attempt_number=attempt.attempt_number,
            status=attempt.status.value,
            producer_thread_id=attempt.producer_thread_id,
            has_output=bool(attempt.output_directory),
            usage=attempt.usage_json,
            trace_available=bool(attempt.trace_ref),
            error_type=attempt.error_type,
            error_message=_error_message(attempt.error_message),
            started_at=_timestamp(attempt.started_at),
            heartbeat_at=_timestamp(attempt.heartbeat_at),
            completed_at=_timestamp(attempt.completed_at),
            duration_ms=attempt.duration_ms,
        )

    @staticmethod
    def _operation_view(operation: PendingOperation, *, include_token: bool) -> PendingOperationView:
        return PendingOperationView(
            id=operation.id,
            status=operation.status.value,
            decision_status=operation.decision_status,
            revision=operation.revision,
            version=getattr(operation, "version", 1),
            request_text=operation.request_text,
            preview=operation.preview_json,
            message=operation.message,
            confirmation_token=operation.confirmation_token if include_token else None,
            usage=operation.usage_json,
            updated_at=operation.updated_at,
        )
