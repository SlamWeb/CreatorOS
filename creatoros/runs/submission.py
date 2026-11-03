"""Atomic authorization of initial production alongside caller-owned records."""
from contextlib import contextmanager, nullcontext
from pathlib import Path
from threading import Lock, RLock

from sqlalchemy import select, text

from creatoros.storage import ContentAttempt, ContentRevision
from creatoros.storage.models import ProductionQueueItem
from creatoros.integrations.producer_skills import _SKILL_EDIT_LOCK
from creatoros.integrations.skill_authority import SkillAuthorizationConflict, verify_skill_authority
from .errors import ContentRunError
from .repository import ContentRunRepository


_REQUEST_LOCKS = {}
_REQUEST_LOCKS_GUARD = Lock()


class ProductionSubmission:
    """Coordinate the executor, Run writer and one caller-owned DB transaction.

    Callers retain their own scope, request and receipt rules. File snapshots
    made during Run creation are outside the SQLite transaction.
    """

    def __init__(self, database, runs, executor):
        self.database, self.runs, self.executor = database, runs, executor

    @contextmanager
    def request(self, request_id):
        """Single-flight preparation for this request in this Python process."""
        url = self.database.engine.url
        database_id = (str(Path(url.database).resolve()) if url.get_backend_name() == "sqlite" and url.database
                       and url.database != ":memory:" else str(url))
        key = (database_id, request_id)
        with _REQUEST_LOCKS_GUARD:
            entry = _REQUEST_LOCKS.setdefault(key, [RLock(), 0])
            entry[1] += 1
        try:
            with entry[0]:
                yield
        finally:
            with _REQUEST_LOCKS_GUARD:
                entry[1] -= 1
                if entry[1] == 0:
                    del _REQUEST_LOCKS[key]

    def assert_ready(self, unavailable_message=None):
        with self.executor._lock:
            self.executor.start()
            blocked = self.executor._readiness_error()
            if blocked:
                raise ContentRunError(unavailable_message or blocked, code="executor_unavailable")

    @contextmanager
    def transaction(self, *, sqlite_immediate=False, unavailable_message=None, verify_prepared=None,
                    prepare_authority=None):
        # This order also excludes the dispatcher until the commit is visible.
        with self.executor._lock:
            self.executor.start()
            blocked = self.executor._readiness_error()
            if blocked:
                raise ContentRunError(unavailable_message or blocked, code="executor_unavailable")
            with self.runs._write_lock:
                with (_SKILL_EDIT_LOCK if verify_prepared is not None or prepare_authority is not None else nullcontext()):
                    if verify_prepared is not None:
                        try:
                            verify_prepared()
                        except (ValueError, OSError) as error:
                            raise ContentRunError(str(error), code="invalid_skill_snapshot") from error
                    authority = prepare_authority() if prepare_authority is not None else None
                    with self.database.session() as session:
                        if sqlite_immediate and self.database.engine.dialect.name == "sqlite":
                            session.execute(text("BEGIN IMMEDIATE"))
                        yield _SubmissionTransaction(session, self.runs, self.executor, authority)
            # The session context has committed successfully before waking.
            self.executor._wake.set()


class _SubmissionTransaction:
    def __init__(self, session, runs, executor, authority=None):
        self.session, self._runs, self._executor, self._authority = session, runs, executor, authority

    def require_authority(self):
        """Call after checking for an already committed request receipt."""
        if self._authority is not None:
            try:
                verify_skill_authority(self.session, self._authority)
            except SkillAuthorizationConflict as error:
                raise ContentRunError(str(error), code="scope_conflict", status_code=403) from error

    def create_task_run(self, topic_id, *, task_id, skill_overrides=None,
                        origin_session_id=None, context_snapshot_ref=None, prepared=None):
        """Create a new task Run and authorize its first revision exactly once."""
        run = self._runs._create(topic_id, task_id=task_id, bound_session=self.session,
            skill_overrides=skill_overrides, origin_session_id=origin_session_id,
            context_snapshot_ref=context_snapshot_ref, prepared=prepared)
        revision = ContentRunRepository(self._runs.database, session=self.session).get_revision_number(run.id, 1)
        self.session.add(ProductionQueueItem(run_id=run.id, revision_id=revision.id,
                                             expected_version=run.version, status="pending"))
        return run

    def create_reference_run(self, topic_id, *, execution_spec=None, origin_session_id=None, prepared=None):
        """Create or replay a reference Run without reauthorizing an unknown result."""
        run = self._runs._create(topic_id, idempotency_key=f"content:{topic_id}", bound_session=self.session,
                                 execution_spec=execution_spec, origin_session_id=origin_session_id, prepared=prepared)
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
