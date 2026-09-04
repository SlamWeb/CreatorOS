from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import select
from sqlalchemy.orm import Session

from creatoros.storage import (
    Database,
    OperationEvent,
    OperationEventType,
    PendingOperation,
    PendingOperationStatus,
)


ACTIONABLE_STATUSES = (
    PendingOperationStatus.AWAITING_APPROVAL,
    PendingOperationStatus.NEEDS_CLARIFICATION,
    PendingOperationStatus.STALE,
    PendingOperationStatus.UNSUPPORTED,
)


class PendingOperationRepository:
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
    def transaction(self) -> Iterator["PendingOperationRepository"]:
        if self._bound_session is not None:
            raise RuntimeError("不能在已绑定事务的 Repository 中再次开启事务。")
        with self.database.session() as session:
            yield PendingOperationRepository(self.database, session=session)

    def create(self, pending_operation: PendingOperation) -> PendingOperation:
        with self._session() as session:
            session.add(pending_operation)
            session.flush()
            return pending_operation

    def get(self, operation_id: str) -> PendingOperation | None:
        with self._session() as session:
            return session.get(PendingOperation, operation_id)

    def list_actionable(self) -> tuple[PendingOperation, ...]:
        with self._session() as session:
            return tuple(
                session.scalars(
                    select(PendingOperation)
                    .where(PendingOperation.status.in_(ACTIONABLE_STATUSES))
                    .order_by(PendingOperation.updated_at.desc(), PendingOperation.id)
                )
            )

    def add_event(
        self,
        operation_id: str,
        event_type: OperationEventType,
        payload: dict | None = None,
    ) -> OperationEvent:
        with self._session() as session:
            event = OperationEvent(
                pending_operation_id=operation_id,
                event_type=event_type,
                payload_json=payload,
            )
            session.add(event)
            session.flush()
            return event

    def list_events(self, operation_id: str) -> tuple[OperationEvent, ...]:
        with self._session() as session:
            return tuple(
                session.scalars(
                    select(OperationEvent)
                    .where(OperationEvent.pending_operation_id == operation_id)
                    .order_by(OperationEvent.id)
                )
            )
