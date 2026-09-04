from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from functools import wraps
from threading import Event, RLock, Thread
from time import monotonic
from uuid import uuid4

from sqlalchemy import update

from creatoros.integrations.codex import CodexProducer, ProducedPack
from creatoros.storage import (
    ContentAttempt,
    ContentAttemptStatus,
    ContentRevision,
    ContentRun,
    ContentRunEventType,
    ContentRunStatus,
    Database,
    Topic,
    TopicStatus,
)

from .artifacts import validate_artifact
from .models import ContentRunInput, RunExecutionResult
from .repository import ContentRunRepository
from .ownership import LocalExecutionGuard


class ContentRunError(ValueError):
    """A requested ContentRun transition is invalid."""

    def __init__(self, message: str, *, code: str = "conflict", status_code: int = 409, run_id: str | None = None):
        super().__init__(message)
        self.code, self.status_code, self.run_id = code, status_code, run_id


class ContentRunExecutionError(RuntimeError):
    def __init__(self, run_id: str, message: str):
        super().__init__(message)
        self.run_id = run_id


class ContentRunLeaseError(ContentRunError):
    """The caller no longer owns the run and must not write a late result."""


def _serialized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._write_lock:
            return method(self, *args, **kwargs)

    return wrapped


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class ContentRunService:
    def __init__(
        self,
        database: Database,
        *,
        producer_factory: Callable[[], CodexProducer] = CodexProducer.from_defaults,
        output_root: Path | None = None,
        lease_seconds: float = 30.0,
    ):
        from creatoros.config import PROJECT_ROOT

        self.database = database
        self.repository = ContentRunRepository(database)
        self.producer_factory = producer_factory
        self.output_root = (output_root or PROJECT_ROOT / "outputs").resolve()
        if lease_seconds <= 0:
            raise ValueError("lease_seconds 必须大于 0。")
        self.lease_seconds = lease_seconds
        self._write_lock = RLock()
        self.guard = LocalExecutionGuard(database)

    @_serialized
    def create(
        self,
        topic_id: str,
        *,
        idempotency_key: str | None = None,
        origin_session_id: str | None = None,
        context_snapshot_ref: str | None = None,
    ) -> ContentRun:
        key = (idempotency_key or f"content:{topic_id}").strip()
        if not key:
            raise ContentRunError("idempotency_key 不能为空。")
        existing = self.repository.get_by_idempotency_key(key)
        if existing is not None:
            return existing

        with self.database.session() as session:
            topic = session.get(Topic, topic_id)
            if topic is None:
                raise ContentRunError(f"Topic 不存在：{topic_id}", status_code=404, code="not_found")
            series = topic.series
            if not series.is_active or not series.creator.is_active:
                raise ContentRunError("账号或栏目未启用。")
            if topic.status is not TopicStatus.QUEUED:
                raise ContentRunError("仅待生产选题可以创建 Run。")
            snapshot = ContentRunInput(
                creator_id=series.creator_id,
                series_id=series.id,
                series_name=series.name,
                series_description=series.description,
                audience=series.audience,
                skill_name=series.skill_name,
                topic_id=topic.id,
                topic_title=topic.title,
                topic_brief=topic.brief,
            )
            run_id = str(uuid4())
            revision_id = str(uuid4())
            repository = ContentRunRepository(self.database, session=session)
            content_run = ContentRun(
                id=run_id,
                topic_id=topic.id,
                idempotency_key=key,
                status=ContentRunStatus.QUEUED,
                input_snapshot_json=snapshot.model_dump(mode="json"),
                origin_session_id=origin_session_id,
                context_snapshot_ref=context_snapshot_ref,
            )
            repository.add_run(content_run)
            repository.add_revision(
                ContentRevision(
                    id=revision_id,
                    content_run_id=run_id,
                    revision_number=1,
                    production_input_json=snapshot.model_dump(mode="json"),
                )
            )
            repository.add_event(
                run_id,
                ContentRunEventType.CREATED,
                revision_id=revision_id,
                to_status=ContentRunStatus.QUEUED,
                payload={"idempotency_key": key},
            )
            return content_run

    def execute(self, run_id: str, *, owner_id: str | None = None) -> RunExecutionResult:
        with self.guard:
            owner = owner_id or f"direct:{uuid4()}"
            prepared = self.claim(run_id, owner_id=owner)
            return self.execute_claimed(prepared, owner_id=owner)

    @_serialized
    def claim(self, run_id: str, *, owner_id: str, expected_version: int | None = None) -> dict:
        self.guard.assert_clean()
        prepared = self._begin_attempt(run_id, owner_id=owner_id, expected_version=expected_version)
        try:
            self.guard.begin(owner_id=owner_id, run_id=run_id, attempt_id=prepared["attempt_id"])
        except BaseException:
            self.interrupt_owner(run_id, owner_id=owner_id, message="认领后未能登记执行者，生产未启动。")
            raise
        return prepared

    def execute_claimed(self, prepared: dict, *, owner_id: str, cancel_event: Event | None = None) -> RunExecutionResult:
        cancel_event = cancel_event or Event()
        finished = Event()

        def renew():
            while not finished.wait(min(5.0, self.lease_seconds / 3)):
                try:
                    self.heartbeat(prepared["run_id"], owner_id=owner_id)
                except Exception:
                    cancel_event.set()
                    self.interrupt_owner(prepared["run_id"], owner_id=owner_id, message="续租失败，已停止本次生产。")
                    return

        heartbeat = Thread(target=renew, daemon=True, name="content-run-heartbeat")
        try:
            heartbeat.start()
            return self._produce_claimed(prepared, owner_id=owner_id, cancel_event=cancel_event)
        except BaseException:
            # Covers infrastructure errors outside Producer's normal exception boundary.
            self.interrupt_owner(prepared["run_id"], owner_id=owner_id, message="执行过程已中断，可在核实后显式恢复。")
            raise
        finally:
            finished.set()
            if heartbeat.ident is not None:
                heartbeat.join(timeout=6)
            self.guard.finish(owner_id)

    def _produce_claimed(self, prepared: dict, *, owner_id: str, cancel_event: Event) -> RunExecutionResult:
        run_id, owner = prepared["run_id"], owner_id
        started = monotonic()
        try:
            if cancel_event.is_set():
                raise ContentRunLeaseError("执行器已停止。")
            producer = self.producer_factory()
            produced = producer.produce_to(
                directory=prepared["directory"],
                pack_id=f"{run_id}-r{prepared['revision_number']:03d}",
                creator_id=prepared["input"].creator_id,
                series_id=prepared["input"].series_id,
                topic_id=prepared["input"].topic_id,
                topic_title=prepared["input"].topic_title,
                topic_brief=prepared["input"].topic_brief,
                series_description=prepared["input"].series_description,
                audience=prepared["input"].audience,
                thread_id=prepared["thread_id"],
                revision_instruction=prepared["instruction"],
                on_thread_started=lambda thread_id: self._attach_thread(
                    run_id, prepared["attempt_id"], thread_id, owner
                ),
                cancel_event=cancel_event,
                on_process_started=lambda identity: self.guard.process_started(owner, identity),
                on_process_stopped=lambda: self.guard.process_stopped(owner),
            )
        except KeyboardInterrupt:
            self._mark_interrupted(run_id, prepared["attempt_id"], monotonic() - started, owner_id=owner)
            raise
        except Exception as error:
            try:
                self._mark_failed(
                    run_id,
                    prepared["attempt_id"],
                    stage="producing",
                    error=error,
                    elapsed=monotonic() - started,
                    owner_id=owner,
                )
            except ContentRunLeaseError:
                pass
            raise ContentRunExecutionError(run_id, str(error)) from error

        self._mark_produced(
            run_id, prepared["attempt_id"], produced, monotonic() - started, owner_id=owner
        )
        try:
            validation = self._validate_active(run_id, owner_id=owner)
        except Exception as error:
            try:
                self._mark_failed(
                    run_id,
                    prepared["attempt_id"],
                    stage="validating",
                    error=error,
                    elapsed=monotonic() - started,
                    owner_id=owner,
                )
            except ContentRunLeaseError:
                pass
            raise ContentRunExecutionError(run_id, str(error)) from error
        return RunExecutionResult(
            run_id=run_id,
            revision_id=prepared["revision_id"],
            attempt_id=prepared["attempt_id"],
            status=ContentRunStatus.AWAITING_APPROVAL.value,
            artifact_directory=str(prepared["directory"]),
            artifact_digest=validation.artifact_digest,
            producer_thread_id=produced.session.thread_id,
        )

    @_serialized
    def approve(
        self,
        run_id: str,
        *,
        revision_id: str,
        artifact_digest: str,
        expected_version: int,
    ) -> ContentRun:
        with self.database.session() as session:
            repository = ContentRunRepository(self.database, session=session)
            content_run = self._require(repository, run_id)
            if content_run.version != expected_version:
                raise ContentRunError("运行状态已变化，请重新查看后再批准。")
            if content_run.status is not ContentRunStatus.AWAITING_APPROVAL:
                raise ContentRunError(f"当前状态不能批准：{content_run.status.value}")
            revision = repository.get_revision_number(run_id, content_run.active_revision_number)
            if revision is None or revision.id != revision_id or not revision.artifact_directory:
                raise ContentRunError("批准的 Revision 不是当前待验收版本。")
            current = validate_artifact(revision.artifact_directory)
            if current.artifact_digest != artifact_digest or current.artifact_digest != revision.artifact_digest:
                raise ContentRunError("产物已变化，旧 digest 不能批准，请重新验收。")
            previous = content_run.status
            now = datetime.now(timezone.utc)
            content_run.status = ContentRunStatus.APPROVED
            content_run.approved_revision_id = revision.id
            content_run.approved_artifact_digest = current.artifact_digest
            content_run.completed_at = now
            revision.approved_at = now
            repository.add_event(
                run_id,
                ContentRunEventType.APPROVED,
                revision_id=revision.id,
                from_status=previous,
                to_status=content_run.status,
                payload={"artifact_digest": current.artifact_digest},
            )
            return content_run

    @_serialized
    def request_revision(
        self,
        run_id: str,
        instruction: str,
        *,
        expected_version: int,
    ) -> ContentRevision:
        if not instruction.strip():
            raise ContentRunError("返工要求不能为空。")
        with self.database.session() as session:
            repository = ContentRunRepository(self.database, session=session)
            content_run = self._require(repository, run_id)
            if content_run.version != expected_version:
                raise ContentRunError("运行状态已变化，请重新查看后再返工。")
            if content_run.status not in {
                ContentRunStatus.AWAITING_APPROVAL,
                ContentRunStatus.FAILED,
                ContentRunStatus.INTERRUPTED,
            }:
                raise ContentRunError(f"当前状态不能创建返工版本：{content_run.status.value}")
            previous = content_run.status
            number = content_run.active_revision_number + 1
            revision = ContentRevision(
                id=str(uuid4()),
                content_run_id=run_id,
                revision_number=number,
                instruction=instruction.strip(),
                production_input_json=content_run.input_snapshot_json,
            )
            repository.add_revision(revision)
            content_run.active_revision_number = number
            content_run.status = ContentRunStatus.QUEUED
            content_run.failure_stage = None
            content_run.error_type = None
            content_run.error_message = None
            content_run.retryable = False
            repository.add_event(
                run_id,
                ContentRunEventType.REVISION_REQUESTED,
                revision_id=revision.id,
                from_status=previous,
                to_status=content_run.status,
                payload={"instruction": revision.instruction, "revision_number": number},
            )
            return revision

    @_serialized
    def cancel(self, run_id: str, *, expected_version: int) -> ContentRun:
        with self.database.session() as session:
            repository = ContentRunRepository(self.database, session=session)
            content_run = self._require(repository, run_id)
            if content_run.version != expected_version:
                raise ContentRunError("运行状态已变化，请重新查看后再取消。")
            if content_run.status is ContentRunStatus.APPROVED:
                raise ContentRunError("已批准的运行不能取消。")
            if content_run.status in {ContentRunStatus.PRODUCING, ContentRunStatus.VALIDATING}:
                raise ContentRunError("运行中的生产不能取消，请等待完成或关闭执行器。")
            if content_run.status is ContentRunStatus.CANCELLED:
                return content_run
            previous = content_run.status
            content_run.status = ContentRunStatus.CANCELLED
            content_run.completed_at = datetime.now(timezone.utc)
            content_run.lease_owner = None
            content_run.lease_expires_at = None
            topic = session.get(Topic, content_run.topic_id)
            if topic is not None:
                topic.status = TopicStatus.SKIPPED
            repository.add_event(
                run_id,
                ContentRunEventType.CANCELLED,
                from_status=previous,
                to_status=content_run.status,
            )
            return content_run

    def recover_inflight(self) -> tuple[int, int, int]:
        with self.guard:
            self.guard.assert_clean()
            return self._recover_inflight()

    @_serialized
    def _recover_inflight(self) -> tuple[int, int, int]:
        interrupted = 0
        validated = 0
        failed = 0
        for content_run in self.repository.list_runs_with_status(ContentRunStatus.PRODUCING):
            self._interrupt_stale_run(content_run.id)
            interrupted += 1
        for content_run in self.repository.list_runs_with_status(ContentRunStatus.VALIDATING):
            try:
                self._validate_active(content_run.id)
                validated += 1
            except Exception as error:
                revision = self.repository.get_revision_number(
                    content_run.id, content_run.active_revision_number
                )
                attempts = self.repository.list_attempts(revision.id) if revision else ()
                if not attempts:
                    raise
                self._mark_failed(
                    content_run.id,
                    attempts[-1].id,
                    stage="validating",
                    error=error,
                    elapsed=0,
                )
                failed += 1
        return interrupted, validated, failed

    def get(self, run_id: str) -> ContentRun:
        content_run = self.repository.get_run(run_id)
        if content_run is None:
            raise ContentRunError(f"ContentRun 不存在：{run_id}", status_code=404, code="not_found")
        return content_run

    def list_runs(self, *, limit: int = 50) -> tuple[ContentRun, ...]:
        return self.repository.list_runs(limit=limit)

    def get_active_revision(self, run_id: str) -> ContentRevision:
        content_run = self.get(run_id)
        revision = self.repository.get_revision_number(run_id, content_run.active_revision_number)
        if revision is None:
            raise ContentRunError("当前 Revision 不存在。")
        return revision

    @_serialized
    def _begin_attempt(self, run_id: str, *, owner_id: str, expected_version: int | None = None) -> dict:
        with self.database.session() as session:
            repository = ContentRunRepository(self.database, session=session)
            content_run = self._require(repository, run_id)
            if expected_version is not None and content_run.version != expected_version:
                raise ContentRunError("运行状态已变化，请重新查看后再开始。", code="version_conflict")
            if content_run.status not in {
                ContentRunStatus.QUEUED,
                ContentRunStatus.INTERRUPTED,
                ContentRunStatus.FAILED,
            }:
                raise ContentRunError(f"当前状态不能开始生产：{content_run.status.value}")
            if content_run.status is ContentRunStatus.FAILED and not content_run.retryable:
                raise ContentRunError("该失败不可技术重试，请创建新 Revision。")
            now = datetime.now(timezone.utc)
            if (
                content_run.lease_owner
                and content_run.lease_owner != owner_id
                and content_run.lease_expires_at is not None
                and _utc(content_run.lease_expires_at) > now
            ):
                raise ContentRunLeaseError("该运行正在由另一个执行者生产。")
            revision = repository.get_revision_number(run_id, content_run.active_revision_number)
            if revision is None:
                raise ContentRunError("当前 Revision 不存在。")
            number = repository.next_attempt_number(revision.id)
            attempt_id = str(uuid4())
            input_data = ContentRunInput.model_validate(revision.production_input_json)
            directory = (
                self.output_root
                / input_data.creator_id
                / input_data.series_id
                / run_id
                / f"revision-{revision.revision_number:03d}"
                / f"attempt-{number:03d}"
            )
            previous = content_run.status
            content_run.status = ContentRunStatus.PRODUCING
            content_run.failure_stage = None
            content_run.error_type = None
            content_run.error_message = None
            content_run.retryable = False
            content_run.lease_owner = owner_id
            content_run.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            content_run.heartbeat_at = now
            topic = session.get(Topic, content_run.topic_id)
            if topic is not None:
                topic.status = TopicStatus.PRODUCING
            repository.add_attempt(
                ContentAttempt(
                    id=attempt_id,
                    revision_id=revision.id,
                    attempt_number=number,
                    status=ContentAttemptStatus.RUNNING,
                    output_directory=str(directory),
                )
            )
            event_type = (
                ContentRunEventType.STARTED
                if previous is ContentRunStatus.QUEUED
                else ContentRunEventType.RESUMED
            )
            repository.add_event(
                run_id,
                event_type,
                revision_id=revision.id,
                attempt_id=attempt_id,
                from_status=previous,
                to_status=content_run.status,
                payload={"attempt_number": number},
            )
            return {
                "run_id": run_id,
                "revision_id": revision.id,
                "revision_number": revision.revision_number,
                "attempt_id": attempt_id,
                "input": input_data,
                "instruction": revision.instruction,
                "thread_id": content_run.producer_thread_id,
                "directory": directory,
                "owner_id": owner_id,
            }

    @_serialized
    def _attach_thread(self, run_id: str, attempt_id: str, thread_id: str, owner_id: str) -> None:
        with self.database.session() as session:
            repository = ContentRunRepository(self.database, session=session)
            content_run = self._require(repository, run_id)
            attempt = repository.get_attempt(attempt_id)
            if attempt is None:
                raise ContentRunError("ContentAttempt 不存在。")
            self._assert_lease(content_run, owner_id)
            self._assert_attempt(repository, content_run, attempt)
            if content_run.producer_thread_id and content_run.producer_thread_id != thread_id:
                raise ContentRunError("同一 ContentRun 收到了不同的 Codex thread_id。")
            content_run.producer_thread_id = thread_id
            attempt.producer_thread_id = thread_id
            content_run.heartbeat_at = datetime.now(timezone.utc)
            attempt.heartbeat_at = content_run.heartbeat_at

    @_serialized
    def _mark_produced(
        self,
        run_id: str,
        attempt_id: str,
        produced: ProducedPack,
        elapsed: float,
        *,
        owner_id: str,
    ) -> None:
        with self.database.session() as session:
            repository = ContentRunRepository(self.database, session=session)
            content_run = self._require(repository, run_id)
            attempt = repository.get_attempt(attempt_id)
            revision = repository.get_revision_number(run_id, content_run.active_revision_number)
            if attempt is None or revision is None:
                raise ContentRunError("生产记录不完整。")
            self._assert_lease(content_run, owner_id)
            self._assert_attempt(repository, content_run, attempt)
            previous = content_run.status
            attempt.status = ContentAttemptStatus.SUCCEEDED
            attempt.producer_thread_id = produced.session.thread_id
            attempt.output_directory = str(produced.directory)
            trace = produced.directory / "codex_trace.jsonl"
            attempt.trace_ref = str(trace) if trace.is_file() else None
            attempt.usage_json = produced.session.usage.model_dump(mode="json")
            attempt.completed_at = datetime.now(timezone.utc)
            attempt.duration_ms = max(0, round(elapsed * 1000))
            revision.artifact_directory = str(produced.directory)
            revision.manifest_path = str(produced.directory / "social_content_pack.json")
            content_run.producer_thread_id = produced.session.thread_id
            content_run.status = ContentRunStatus.VALIDATING
            repository.add_event(
                run_id,
                ContentRunEventType.PRODUCED,
                revision_id=revision.id,
                attempt_id=attempt.id,
                from_status=previous,
                to_status=content_run.status,
                payload={"output_directory": str(produced.directory)},
            )

    @_serialized
    def _validate_active(self, run_id: str, *, owner_id: str | None = None):
        content_run = self.get(run_id)
        revision = self.repository.get_revision_number(run_id, content_run.active_revision_number)
        if revision is None or not revision.artifact_directory:
            raise ContentRunError("当前 Revision 没有可验收的产物目录。")
        validation = validate_artifact(revision.artifact_directory)
        with self.database.session() as session:
            repository = ContentRunRepository(self.database, session=session)
            content_run = self._require(repository, run_id)
            if content_run.status is not ContentRunStatus.VALIDATING:
                raise ContentRunError(f"当前状态不能验收：{content_run.status.value}")
            revision = repository.get_revision_number(run_id, content_run.active_revision_number)
            if revision is None:
                raise ContentRunError("当前 Revision 不存在。")
            if owner_id is not None:
                self._assert_lease(content_run, owner_id)
            previous = content_run.status
            revision.artifact_digest = validation.artifact_digest
            revision.validation_json = validation.model_dump(mode="json")
            revision.validated_at = datetime.now(timezone.utc)
            content_run.status = ContentRunStatus.AWAITING_APPROVAL
            content_run.lease_owner = None
            content_run.lease_expires_at = None
            topic = session.get(Topic, content_run.topic_id)
            if topic is not None:
                topic.status = TopicStatus.READY
            repository.add_event(
                run_id,
                ContentRunEventType.VALIDATED,
                revision_id=revision.id,
                from_status=previous,
                to_status=content_run.status,
                payload=validation.model_dump(mode="json"),
            )
        return validation

    @_serialized
    def _mark_interrupted(self, run_id: str, attempt_id: str, elapsed: float, *, owner_id: str | None = None) -> None:
        self._finish_error(
            run_id,
            attempt_id,
            status=ContentRunStatus.INTERRUPTED,
            attempt_status=ContentAttemptStatus.INTERRUPTED,
            event_type=ContentRunEventType.INTERRUPTED,
            stage="producing",
            error_type="user_interrupt",
            message="用户中断了前台生产。",
            retryable=True,
            elapsed=elapsed,
            owner_id=owner_id,
        )

    @_serialized
    def _mark_failed(
        self,
        run_id: str,
        attempt_id: str,
        *,
        stage: str,
        error: Exception,
        elapsed: float,
        owner_id: str | None = None,
    ) -> None:
        error_type = getattr(error, "error_type", "artifact_validation_failed" if stage == "validating" else "production_failed")
        retryable = stage == "producing" and error_type in {
            "codex_timeout",
            "codex_exec_failed",
            "codex_turn_failed",
        }
        self._finish_error(
            run_id,
            attempt_id,
            status=ContentRunStatus.FAILED,
            attempt_status=ContentAttemptStatus.FAILED,
            event_type=ContentRunEventType.FAILED,
            stage=stage,
            error_type=error_type,
            message=str(error),
            retryable=retryable,
            elapsed=elapsed,
            owner_id=owner_id,
        )

    @_serialized
    def _finish_error(
        self,
        run_id: str,
        attempt_id: str,
        *,
        status: ContentRunStatus,
        attempt_status: ContentAttemptStatus,
        event_type: ContentRunEventType,
        stage: str,
        error_type: str,
        message: str,
        retryable: bool,
        elapsed: float,
        owner_id: str | None = None,
    ) -> None:
        with self.database.session() as session:
            repository = ContentRunRepository(self.database, session=session)
            content_run = self._require(repository, run_id)
            attempt = repository.get_attempt(attempt_id)
            if attempt is None:
                raise ContentRunError("ContentAttempt 不存在。")
            if owner_id is not None:
                self._assert_lease(content_run, owner_id)
                self._assert_attempt(repository, content_run, attempt)
            previous = content_run.status
            now = datetime.now(timezone.utc)
            if attempt.status is ContentAttemptStatus.RUNNING:
                attempt.status = attempt_status
                attempt.error_type = error_type
                attempt.error_message = message
                attempt.retryable = retryable
                attempt.completed_at = now
                attempt.duration_ms = max(0, round(elapsed * 1000))
            content_run.status = status
            content_run.failure_stage = stage
            content_run.error_type = error_type
            content_run.error_message = message
            content_run.retryable = retryable
            content_run.lease_owner = None
            content_run.lease_expires_at = None
            trace = Path(attempt.output_directory) / "codex_trace.jsonl" if attempt.output_directory else None
            attempt.trace_ref = str(trace) if trace is not None and trace.is_file() else None
            topic = session.get(Topic, content_run.topic_id)
            if topic is not None:
                topic.status = (
                    TopicStatus.QUEUED
                    if status is ContentRunStatus.INTERRUPTED
                    else TopicStatus.FAILED
                )
            repository.add_event(
                run_id,
                event_type,
                revision_id=attempt.revision_id,
                attempt_id=attempt.id,
                from_status=previous,
                to_status=status,
                payload={"stage": stage, "error_type": error_type, "retryable": retryable},
            )

    def _interrupt_stale_run(self, run_id: str) -> None:
        content_run = self.get(run_id)
        revision = self.repository.get_revision_number(run_id, content_run.active_revision_number)
        if revision is None:
            raise ContentRunError("当前 Revision 不存在。")
        attempts = self.repository.list_attempts(revision.id)
        running = next((item for item in reversed(attempts) if item.status is ContentAttemptStatus.RUNNING), None)
        if running is None:
            raise ContentRunError("producing 状态缺少 running Attempt。")
        self._finish_error(
            run_id, running.id, status=ContentRunStatus.INTERRUPTED,
            attempt_status=ContentAttemptStatus.INTERRUPTED, event_type=ContentRunEventType.INTERRUPTED,
            stage="producing", error_type="recovery_interrupted", message="上次执行进程已退出，等待显式恢复。",
            retryable=True, elapsed=0,
        )

    @_serialized
    def heartbeat(self, run_id: str, *, owner_id: str) -> datetime:
        """Extend the lease only; it never reports percentage or business progress."""
        with self.database.session() as session:
            repository = ContentRunRepository(self.database, session=session)
            content_run = self._require(repository, run_id)
            self._assert_lease(content_run, owner_id)
            now = datetime.now(timezone.utc)
            refreshed = session.execute(
                update(ContentRun)
                .where(
                    ContentRun.id == run_id,
                    ContentRun.lease_owner == owner_id,
                    ContentRun.status == ContentRunStatus.PRODUCING,
                )
                .values(
                    heartbeat_at=now,
                    lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                )
            )
            if refreshed.rowcount != 1:
                raise ContentRunLeaseError("执行者已失去该运行的 lease，拒绝续租。")
            attempt = self._running_attempt(repository, content_run)
            if attempt is not None:
                attempt.heartbeat_at = now
            return now

    @_serialized
    def interrupt_owner(self, run_id: str, *, owner_id: str, message: str = "执行器关闭，中断了生产。") -> bool:
        with self.database.session() as session:
            repository = ContentRunRepository(self.database, session=session)
            content_run = self._require(repository, run_id)
            if content_run.status is not ContentRunStatus.PRODUCING:
                return False
            if content_run.lease_owner != owner_id:
                raise ContentRunLeaseError("执行者 owner 已变化，拒绝中断新任务。")
            attempt = self._running_attempt(repository, content_run)
            if attempt is None:
                raise ContentRunError("producing 状态缺少 running Attempt。")
            previous = content_run.status
            now = datetime.now(timezone.utc)
            attempt.status = ContentAttemptStatus.INTERRUPTED
            attempt.error_type = "executor_shutdown"
            attempt.error_message = message
            attempt.retryable = True
            attempt.completed_at = now
            content_run.status = ContentRunStatus.INTERRUPTED
            content_run.failure_stage = "producing"
            content_run.error_type = "executor_shutdown"
            content_run.error_message = message
            content_run.retryable = True
            content_run.lease_owner = None
            content_run.lease_expires_at = None
            topic = session.get(Topic, content_run.topic_id)
            if topic is not None:
                topic.status = TopicStatus.QUEUED
            repository.add_event(
                run_id,
                ContentRunEventType.INTERRUPTED,
                revision_id=attempt.revision_id,
                attempt_id=attempt.id,
                from_status=previous,
                to_status=content_run.status,
                payload={"stage": "producing", "error_type": "executor_shutdown", "retryable": True},
            )
            return True

    @staticmethod
    def _assert_attempt(repository, content_run, attempt):
        revision = repository.get_revision_number(content_run.id, content_run.active_revision_number)
        if revision is None or attempt.revision_id != revision.id:
            raise ContentRunLeaseError("当前 Revision/Attempt 已变化，拒绝晚到回写。")
        attempts = repository.list_attempts(revision.id)
        if not attempts or attempts[-1].id != attempt.id:
            raise ContentRunLeaseError("当前 Attempt 已变化，拒绝晚到回写。")

    @staticmethod
    def _running_attempt(repository: ContentRunRepository, content_run: ContentRun) -> ContentAttempt | None:
        revision = repository.get_revision_number(content_run.id, content_run.active_revision_number)
        if revision is None:
            return None
        attempts = repository.list_attempts(revision.id)
        return next((item for item in reversed(attempts) if item.status is ContentAttemptStatus.RUNNING), None)

    @staticmethod
    def _assert_lease(content_run: ContentRun, owner_id: str) -> None:
        if (
            content_run.lease_owner != owner_id
            or content_run.lease_expires_at is None
            or _utc(content_run.lease_expires_at) <= datetime.now(timezone.utc)
        ):
            raise ContentRunLeaseError("执行者已失去该运行的 lease，拒绝晚到结果。")


    @staticmethod
    def _require(repository: ContentRunRepository, run_id: str) -> ContentRun:
        content_run = repository.get_run(run_id)
        if content_run is None:
            raise ContentRunError(f"ContentRun 不存在：{run_id}", status_code=404, code="not_found")
        return content_run
