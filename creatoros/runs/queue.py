from __future__ import annotations

from datetime import datetime, timezone
from contextlib import nullcontext

from sqlalchemy import select, or_, and_, text

from creatoros.storage.models import ContentAttempt, ContentRun, ContentRevision, ContentRunStatus, ProductionQueueItem, ProductionQueueHistory, TopicRemoval
from .service import ContentRunError


class ProductionQueue:
    """Persist dispatch intent; all execution and artifact authority stays in RunService."""

    def __init__(self, service):
        self.service = service
        self.database = service.database

    def enqueue(self, run_id: str, expected_version: int, *, bound_session=None,
                initial_only: bool = False, creator_id: str | None = None) -> ProductionQueueItem:
        with self.service._write_lock, (nullcontext(bound_session) if bound_session is not None else self.database.session()) as session:
            # Serialize independent SQLite submitters before reading and changing the receipt.
            # A bound session is already owned by the submission transaction.
            if bound_session is None and self.database.engine.dialect.name == 'sqlite':
                session.execute(text('BEGIN IMMEDIATE'))
            run = session.get(ContentRun, run_id)
            if run is None:
                raise ContentRunError("运行不存在。", code="not_found", status_code=404)
            snapshot = run.input_snapshot_json
            if not isinstance(snapshot, dict) or any(not isinstance(snapshot.get(field), str) or not snapshot[field]
                                                     for field in ('creator_id', 'series_id', 'topic_id')):
                raise ContentRunError("历史任务输入不完整，请先核对原记录。", code="legacy_snapshot_needs_check")
            self.service._assert_no_active_iteration(session, run.topic_id)
            revision = session.scalar(select(ContentRevision).where(ContentRevision.content_run_id == run_id,
                                      ContentRevision.revision_number == run.active_revision_number))
            if revision is None:
                raise ContentRunError("当前内容版本不存在。", code="not_found", status_code=404)
            item = session.scalar(select(ProductionQueueItem).where(
                ProductionQueueItem.run_id == run_id, ProductionQueueItem.revision_id == revision.id))
            if run.input_snapshot_json.get("production_protocol") == "external-import":
                raise ContentRunError("外部导入作品不进入生产执行器。", code="external_import_readonly")
            if session.get(TopicRemoval, run.topic_id) is not None:
                raise ContentRunError("选题已移除，不能启动或恢复生产。", code="topic_removed")
            # Explicit recovery also must not reauthorize an old account's Run.
            # Do this before clearing or archiving any prior queue error.
            self.service._assert_existing_scope(session, run.topic_id, run)
            if initial_only:
                if not isinstance(run.idempotency_key, str) or not run.idempotency_key.strip():
                    raise ContentRunError("运行标识不可用，请核对原任务。", code="invalid_request", status_code=422)
                if creator_id is not None and run.input_snapshot_json.get('creator_id') != creator_id:
                    raise ContentRunError("任务不属于当前账号。", code="scope_conflict", status_code=403)
                # Any prior queue row is the original authorization, including
                # a crash reconciled to needs_attention before an Attempt exists.
                if item is not None:
                    if expected_version not in {item.expected_version, run.version}:
                        raise ContentRunError("运行状态已变化，请刷新后重试。", code="version_conflict")
                    return item
                attempted = session.scalar(select(ContentAttempt.id).where(
                    ContentAttempt.revision_id == revision.id).limit(1))
                if run.status is not ContentRunStatus.QUEUED or attempted:
                    raise ContentRunError("无法确认首次授权，请核对原任务。", code="initial_submission_unavailable")
            if run.status is ContentRunStatus.CANCELLED:
                raise ContentRunError("已取消的运行不能开始生产。")
            # Response-loss retries must not create another Attempt or reset FIFO order.
            if item is not None and item.status in {"pending", "running"}:
                if expected_version not in {item.expected_version, run.version}:
                    raise ContentRunError("运行状态已变化，请刷新后重试。", code="version_conflict")
                return item
            if run.version != expected_version:
                raise ContentRunError("运行状态已变化，请刷新后重试。", code="version_conflict")
            if run.status not in {ContentRunStatus.QUEUED, ContentRunStatus.INTERRUPTED, ContentRunStatus.FAILED}:
                raise ContentRunError(f"当前状态不能开始生产：{run.status.value}")
            if run.status is ContentRunStatus.FAILED and not run.retryable:
                raise ContentRunError("该失败不可技术重试，请创建新 Revision。")
            if item is None:
                item = ProductionQueueItem(run_id=run_id, revision_id=revision.id)
                session.add(item)
            if item.id is not None and item.status in {"failed", "needs_attention"}:
                session.add(ProductionQueueHistory(
                    queue_item_id=item.id, expected_version=item.expected_version,
                    status=item.status, submitted_at=item.submitted_at, started_at=item.started_at,
                    completed_at=item.completed_at, last_error=item.last_error,
                ))
            item.expected_version = expected_version
            item.status = "pending"
            item.submitted_at = datetime.now(timezone.utc)
            item.started_at = item.completed_at = item.last_error = None
            session.flush()
            return item

    def next_pending(self, *, excluded_topics: set[str] | None = None,
                     excluded_tasks: set[str] | None = None) -> ProductionQueueItem | None:
        with self.database.session() as session:
            query = select(ProductionQueueItem).join(ContentRun, ProductionQueueItem.run_id == ContentRun.id).where(
                ProductionQueueItem.status == "pending")
            legacy = [ContentRun.task_id.is_(None)]
            tasks = [ContentRun.task_id.is_not(None)]
            if excluded_topics:
                legacy.append(ContentRun.topic_id.not_in(excluded_topics))
            if excluded_tasks:
                tasks.append(ContentRun.task_id.not_in(excluded_tasks))
            query = query.where(or_(and_(*legacy), and_(*tasks)))
            return session.scalar(query.order_by(ProductionQueueItem.submitted_at, ProductionQueueItem.id).limit(1))

    def transition(self, item_id: int, status: str, error: str | None = None):
        with self.database.session() as session:
            item = session.get(ProductionQueueItem, item_id)
            if item is None:
                return
            item.status, item.last_error = status, error
            now = datetime.now(timezone.utc)
            if status == "running":
                item.started_at = now
            else:
                item.completed_at = now

    def finish(self, item_id: int, run_id: str, *, expected_attempt_id: str | None = None,
               expected_queue_version: int | None = None):
        with self.database.session() as session:
            if self.database.engine.dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            item = session.get(ProductionQueueItem, item_id)
            run = session.get(ContentRun, run_id)
            if item is None or run is None or item.run_id != run_id:
                return
            if expected_attempt_id is not None:
                revision = session.scalar(select(ContentRevision).where(
                    ContentRevision.content_run_id == run_id, ContentRevision.revision_number == run.active_revision_number))
                latest = (session.scalar(select(ContentAttempt).where(ContentAttempt.revision_id == revision.id)
                          .order_by(ContentAttempt.attempt_number.desc()).limit(1)) if revision else None)
                if (item.status != "running" or item.expected_version != expected_queue_version
                        or revision is None or item.revision_id != revision.id
                        or latest is None or latest.id != expected_attempt_id
                        or run.status in {ContentRunStatus.PRODUCING, ContentRunStatus.VALIDATING}):
                    return
            if run.status in {ContentRunStatus.AWAITING_APPROVAL, ContentRunStatus.APPROVED}:
                status = "succeeded"
            elif run.status is ContentRunStatus.CANCELLED:
                status = "cancelled"
            elif run.status is ContentRunStatus.FAILED:
                status = "failed"
            else:
                status = "needs_attention"
            item.status, item.last_error = status, run.error_message
            item.completed_at = datetime.now(timezone.utc)

    def recover(self):
        # Even a crash between dispatch marker and claim must not implicitly retry.
        with self.database.session() as session:
            items = list(session.scalars(select(ProductionQueueItem).where(ProductionQueueItem.status == "running")))
        for item in items:
            self.finish(item.id, item.run_id)

    def cancel_pending(self, run_id: str):
        with self.database.session() as session:
            for item in session.scalars(select(ProductionQueueItem).where(
                    ProductionQueueItem.run_id == run_id, ProductionQueueItem.status == "pending")):
                item.status = "cancelled"
                item.completed_at = datetime.now(timezone.utc)

    def pause_pending(self, reason: str | None):
        # Retain authorization/FIFO: a pause is not a failed or consumed task.
        with self.database.session() as session:
            for item in session.scalars(select(ProductionQueueItem).where(ProductionQueueItem.status == "pending")):
                item.last_error = reason

    def is_submitted(self, run_id: str) -> bool:
        # Status polling must not materialize receipt history or the entire FIFO.
        revision = self.service.get_active_revision(run_id)
        with self.database.session() as session:
            status = session.scalar(select(ProductionQueueItem.status).where(
                ProductionQueueItem.run_id == run_id, ProductionQueueItem.revision_id == revision.id))
            return status in {"pending", "running"}

    def snapshot(self, run_id: str | None = None):
        with self.database.session() as session:
            pending = list(session.scalars(select(ProductionQueueItem.id)
                .where(ProductionQueueItem.status == "pending")
                .order_by(ProductionQueueItem.submitted_at, ProductionQueueItem.id)))
            query = select(ProductionQueueItem)
            if run_id is not None:
                revision = self.service.get_active_revision(run_id)
                query = query.where(ProductionQueueItem.run_id == run_id, ProductionQueueItem.revision_id == revision.id)
            else:
                query = query.where(ProductionQueueItem.status.in_(["pending", "running", "needs_attention"]))
            items = list(session.scalars(query.order_by(ProductionQueueItem.submitted_at, ProductionQueueItem.id)))
            def project(item):
                return {"id": item.id, "run_id": item.run_id, "revision_id": item.revision_id,
                        "status": item.status, "position": pending.index(item.id) + 1 if item.id in pending else None,
                        "submitted_at": item.submitted_at.isoformat(),
                        "started_at": item.started_at.isoformat() if item.started_at else None,
                        "completed_at": item.completed_at.isoformat() if item.completed_at else None,
                        "last_error": item.last_error,
                        "blocked_reason": item.last_error if item.status == "pending" else None}
            result = [project(item) for item in items]
            if run_id is not None and result:
                history = session.scalars(select(ProductionQueueHistory).where(
                    ProductionQueueHistory.queue_item_id == items[0].id).order_by(ProductionQueueHistory.id))
                result[0]["history"] = [{"status": row.status, "expected_version": row.expected_version,
                    "submitted_at": row.submitted_at.isoformat(),
                    "started_at": row.started_at.isoformat() if row.started_at else None,
                    "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                    "last_error": row.last_error, "archived_at": row.archived_at.isoformat()}
                    for row in history]
            return (result[0] if result else None) if run_id is not None else result
