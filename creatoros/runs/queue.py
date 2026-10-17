from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from creatoros.storage.models import ContentRun, ContentRunStatus, ProductionQueueItem, TopicRemoval
from .service import ContentRunError


class ProductionQueue:
    """Persist dispatch intent; all execution and artifact authority stays in RunService."""

    def __init__(self, service):
        self.service = service
        self.database = service.database

    def enqueue(self, run_id: str, expected_version: int) -> ProductionQueueItem:
        with self.service._write_lock, self.database.session() as session:
            run = session.get(ContentRun, run_id)
            if run is None:
                raise ContentRunError("运行不存在。", code="not_found", status_code=404)
            revision = self.service.get_active_revision(run_id)
            item = session.scalar(select(ProductionQueueItem).where(
                ProductionQueueItem.run_id == run_id, ProductionQueueItem.revision_id == revision.id))
            if run.input_snapshot_json.get("production_protocol") == "external-import":
                raise ContentRunError("外部导入作品不进入生产执行器。", code="external_import_readonly")
            if session.get(TopicRemoval, run.topic_id) is not None:
                raise ContentRunError("选题已移除，不能启动或恢复生产。", code="topic_removed")
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
            item.expected_version = expected_version
            item.status = "pending"
            item.submitted_at = datetime.now(timezone.utc)
            item.started_at = item.completed_at = item.last_error = None
            session.flush()
            return item

    def next_pending(self) -> ProductionQueueItem | None:
        with self.database.session() as session:
            return session.scalar(select(ProductionQueueItem).where(ProductionQueueItem.status == "pending")
                                  .order_by(ProductionQueueItem.submitted_at, ProductionQueueItem.id).limit(1))

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

    def finish(self, item_id: int, run_id: str):
        run = self.service.get(run_id)
        if run.status in {ContentRunStatus.AWAITING_APPROVAL, ContentRunStatus.APPROVED}:
            status = "succeeded"
        elif run.status is ContentRunStatus.CANCELLED:
            status = "cancelled"
        elif run.status is ContentRunStatus.FAILED:
            status = "failed"
        else:
            status = "needs_attention"
        self.transition(item_id, status, run.error_message)

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
            return (result[0] if result else None) if run_id is not None else result
