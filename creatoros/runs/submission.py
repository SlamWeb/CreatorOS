"""Atomic authorization of initial production alongside caller-owned records."""
from contextlib import contextmanager

from sqlalchemy import select, text

from creatoros.storage import ContentAttempt, ContentRevision
from creatoros.storage.models import ProductionQueueItem
from .errors import ContentRunError
from .repository import ContentRunRepository


class ProductionSubmission:
    """Coordinate the executor, Run writer and one caller-owned DB transaction.

    Callers retain their own scope, request and receipt rules. File snapshots
    made during Run creation are outside the SQLite transaction.
    """

    def __init__(self, database, runs, executor):
        self.database, self.runs, self.executor = database, runs, executor

    @contextmanager
    def transaction(self, *, sqlite_immediate=False, unavailable_message=None):
        # This order also excludes the dispatcher until the commit is visible.
        with self.executor._lock:
            self.executor.start()
            blocked = self.executor._readiness_error()
            if blocked:
                raise ContentRunError(unavailable_message or blocked, code="executor_unavailable")
            with self.runs._write_lock, self.database.session() as session:
                if sqlite_immediate and self.database.engine.dialect.name == "sqlite":
                    session.execute(text("BEGIN IMMEDIATE"))
                yield _SubmissionTransaction(session, self.runs, self.executor)
            # The session context has committed successfully before waking.
            self.executor._wake.set()


class _SubmissionTransaction:
    def __init__(self, session, runs, executor):
        self.session, self._runs, self._executor = session, runs, executor

    def create_task_run(self, topic_id, *, task_id, skill_overrides=None,
                        origin_session_id=None, context_snapshot_ref=None):
        """Create a new task Run and authorize its first revision exactly once."""
        run = self._runs._create(topic_id, task_id=task_id, bound_session=self.session,
            skill_overrides=skill_overrides, origin_session_id=origin_session_id,
            context_snapshot_ref=context_snapshot_ref)
        revision = ContentRunRepository(self._runs.database, session=self.session).get_revision_number(run.id, 1)
        self.session.add(ProductionQueueItem(run_id=run.id, revision_id=revision.id,
                                             expected_version=run.version, status="pending"))
        return run

    def create_reference_run(self, topic_id, *, execution_spec=None, origin_session_id=None):
        """Create or replay a reference Run without reauthorizing an unknown result."""
        run = self._runs._create(topic_id, idempotency_key=f"content:{topic_id}", bound_session=self.session,
                                 execution_spec=execution_spec, origin_session_id=origin_session_id)
        first = self.session.scalar(select(ContentRevision).where(ContentRevision.content_run_id == run.id,
                                    ContentRevision.revision_number == 1))
        attempted = self.session.scalar(select(ContentAttempt.id).where(ContentAttempt.revision_id == first.id).limit(1))
        if run.status.value == "queued" and run.active_revision_number == 1 and not attempted:
            item = self.session.scalar(select(ProductionQueueItem).where(
                ProductionQueueItem.run_id == run.id, ProductionQueueItem.revision_id == first.id))
            # Running can have crashed before an Attempt. Once reconciled to a
            # terminal state, only explicit recovery may reauthorize this Run.
            if item is None or item.status in {"pending", "running"}:
                self._executor.queue.enqueue(run.id, run.version, bound_session=self.session)
        return run
