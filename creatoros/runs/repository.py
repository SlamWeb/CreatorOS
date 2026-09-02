from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from creatoros.storage import Database
from creatoros.storage.models import (
    ContentAttempt,
    ContentRevision,
    ContentRun,
    ContentRunEvent,
    ContentRunEventType,
    ContentRunStatus,
)


class ContentRunRepository:
    def __init__(self, database: Database, *, session: Session | None = None):
        self.database = database
        self._bound_session = session

    @contextmanager
    def _session(self) -> Iterator[Session]:
        if self._bound_session is not None:
            yield self._bound_session
            return
        with self.database.session() as session:
            yield session

    @contextmanager
    def transaction(self) -> Iterator["ContentRunRepository"]:
        if self._bound_session is not None:
            raise RuntimeError("不能在已绑定事务的 Repository 中再次开启事务。")
        with self.database.session() as session:
            yield ContentRunRepository(self.database, session=session)

    def add_run(self, content_run: ContentRun) -> ContentRun:
        with self._session() as session:
            session.add(content_run)
            session.flush()
            return content_run

    def get_run(self, run_id: str) -> ContentRun | None:
        with self._session() as session:
            return session.get(ContentRun, run_id)

    def get_by_idempotency_key(self, key: str) -> ContentRun | None:
        with self._session() as session:
            return session.scalar(select(ContentRun).where(ContentRun.idempotency_key == key))

    def list_runs(self, *, limit: int = 50) -> tuple[ContentRun, ...]:
        with self._session() as session:
            return tuple(
                session.scalars(
                    select(ContentRun)
                    .order_by(ContentRun.updated_at.desc(), ContentRun.id)
                    .limit(limit)
                )
            )

    def list_runs_with_status(self, status: ContentRunStatus) -> tuple[ContentRun, ...]:
        with self._session() as session:
            return tuple(
                session.scalars(select(ContentRun).where(ContentRun.status == status))
            )

    def add_revision(self, revision: ContentRevision) -> ContentRevision:
        with self._session() as session:
            session.add(revision)
            session.flush()
            return revision

    def get_revision(self, revision_id: str) -> ContentRevision | None:
        with self._session() as session:
            return session.get(ContentRevision, revision_id)

    def get_revision_number(self, run_id: str, number: int) -> ContentRevision | None:
        with self._session() as session:
            return session.scalar(
                select(ContentRevision).where(
                    ContentRevision.content_run_id == run_id,
                    ContentRevision.revision_number == number,
                )
            )

    def list_revisions(self, run_id: str) -> tuple[ContentRevision, ...]:
        with self._session() as session:
            return tuple(
                session.scalars(
                    select(ContentRevision)
                    .where(ContentRevision.content_run_id == run_id)
                    .order_by(ContentRevision.revision_number)
                )
            )

    def next_attempt_number(self, revision_id: str) -> int:
        with self._session() as session:
            current = session.scalar(
                select(func.max(ContentAttempt.attempt_number)).where(
                    ContentAttempt.revision_id == revision_id
                )
            )
            return (current or 0) + 1

    def add_attempt(self, attempt: ContentAttempt) -> ContentAttempt:
        with self._session() as session:
            session.add(attempt)
            session.flush()
            return attempt

    def get_attempt(self, attempt_id: str) -> ContentAttempt | None:
        with self._session() as session:
            return session.get(ContentAttempt, attempt_id)

    def list_attempts(self, revision_id: str) -> tuple[ContentAttempt, ...]:
        with self._session() as session:
            return tuple(
                session.scalars(
                    select(ContentAttempt)
                    .where(ContentAttempt.revision_id == revision_id)
                    .order_by(ContentAttempt.attempt_number)
                )
            )

    def add_event(
        self,
        run_id: str,
        event_type: ContentRunEventType,
        *,
        revision_id: str | None = None,
        attempt_id: str | None = None,
        from_status: ContentRunStatus | None = None,
        to_status: ContentRunStatus | None = None,
        payload: dict | None = None,
    ) -> ContentRunEvent:
        with self._session() as session:
            event = ContentRunEvent(
                content_run_id=run_id,
                revision_id=revision_id,
                attempt_id=attempt_id,
                event_type=event_type,
                from_status=from_status.value if from_status else None,
                to_status=to_status.value if to_status else None,
                payload_json=payload,
            )
            session.add(event)
            session.flush()
            return event

    def list_events(self, run_id: str) -> tuple[ContentRunEvent, ...]:
        with self._session() as session:
            return tuple(
                session.scalars(
                    select(ContentRunEvent)
                    .where(ContentRunEvent.content_run_id == run_id)
                    .order_by(ContentRunEvent.id)
                )
            )
