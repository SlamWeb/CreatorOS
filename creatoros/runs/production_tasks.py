"""Explicit independent pieces of one Topic, using the existing Run queue."""
from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from pydantic import Field
from sqlalchemy import func, select

from creatoros.storage import ContentRun, MediaCollection, ProductionTask, Topic, TopicRemoval, WriteReceipt
from creatoros.storage.models import ProductionQueueItem
from .models import RunModel, SkillOverrides
from .service import ContentRunError
from .repository import ContentRunRepository


class ProductionTaskInput(RunModel):
    title: str = Field(min_length=1, max_length=240)
    brief: str | None = Field(default=None, min_length=1, max_length=10000)
    skill_overrides: SkillOverrides | None = None


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


class ProductionTaskService:
    def __init__(self, database, runs, executor):
        self.database, self.runs, self.executor = database, runs, executor

    def create(self, topic_id, *, request_id, tasks, origin_session_id=None, context_snapshot_ref=None):
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 64:
            raise ContentRunError("制作请求标识必须为1～64个字符。", code="invalid_request", status_code=422)
        if not isinstance(tasks, list) or not 1 <= len(tasks) <= 100:
            raise ContentRunError("每批请提交1～100个制作任务。", code="invalid_request", status_code=422)
        try:
            inputs = [ProductionTaskInput.model_validate(item) for item in tasks]
        except ValueError as error:
            raise ContentRunError(str(error), code="invalid_request", status_code=422) from error
        request_id = request_id.strip()
        digest = _digest(dict(topic_id=topic_id, tasks=[item.model_dump(mode="json") for item in inputs]))
        # Keep dispatcher and writes out until every queue authorization commits.
        with self.executor._lock:
            self.executor.start()
            blocked = self.executor._readiness_error()
            if blocked:
                raise ContentRunError(blocked, code="executor_unavailable")
            with self.runs._write_lock, self.database.session() as session:
                topic = session.get(Topic, topic_id)
                if topic is None or session.get(TopicRemoval, topic_id) is not None:
                    raise ContentRunError("选题不存在或已移除。", code="not_found", status_code=404)
                series = topic.series
                if (not series.is_active or not series.creator_id or not series.creator or not series.creator.is_active):
                    raise ContentRunError("账号或栏目未启用。")
                receipt = session.get(WriteReceipt, request_id)
                if receipt is not None:
                    saved = receipt.response_json
                    if (receipt.operation != "queue_topics" or receipt.resource_id != topic_id
                            or saved.get("kind") != "production_tasks" or saved.get("request_digest") != digest
                            or saved.get("creator_id") != series.creator_id):
                        raise ContentRunError("请求标识已用于不同的制作要求。", code="idempotency_conflict")
                    identifiers = saved["task_ids"]
                else:
                    self.runs._assert_no_active_iteration(session, topic_id)
                    collection_id = session.scalar(select(ProductionTask.collection_id).where(
                        ProductionTask.topic_id == topic_id, ProductionTask.collection_id.is_not(None)).limit(1))
                    if collection_id is not None:
                        collection = session.get(MediaCollection, collection_id)
                        if collection is None or collection.creator_id != series.creator_id or collection.topic_id != topic_id:
                            raise ContentRunError("制作素材集的归属与当前选题不一致。", code="scope_conflict", status_code=403)
                    if collection_id is None:
                        collection_id = str(uuid4())
                        session.add(MediaCollection(id=collection_id, creator_id=series.creator_id, topic_id=topic_id,
                            name=topic.title[:120], request_id=f"production-tasks:{topic_id}",
                            create_digest=_digest(dict(kind="production_tasks", topic_id=topic_id)),
                            run_ids_json=[], order_ids_json=[], excluded_ids_json=[]))
                        session.flush()
                    position = session.scalar(select(func.max(ProductionTask.position)).where(
                        ProductionTask.topic_id == topic_id))
                    position = 0 if position is None else position + 1
                    identifiers = []
                    for index, item in enumerate(inputs):
                        task = ProductionTask(id=str(uuid4()), topic_id=topic_id, title=item.title, brief=item.brief,
                            position=position + index, collection_id=collection_id, create_request_id=request_id)
                        session.add(task)
                        session.flush()
                        run = self.runs._create(topic_id, task_id=task.id, bound_session=session,
                            skill_overrides=item.skill_overrides, origin_session_id=origin_session_id,
                            context_snapshot_ref=context_snapshot_ref)
                        revision = ContentRunRepository(self.database, session=session).get_revision_number(run.id, 1)
                        session.add(ProductionQueueItem(run_id=run.id, revision_id=revision.id,
                            expected_version=run.version, status="pending"))
                        identifiers.append(task.id)
                    session.add(WriteReceipt(request_id=request_id, operation="queue_topics", resource_id=topic_id,
                        origin="web", response_json=dict(kind="production_tasks", request_digest=digest,
                            creator_id=series.creator_id, task_ids=identifiers)))
                    session.flush()
            self.executor._wake.set()
        return self._list(topic_id, identifiers)

    def list(self, topic_id):
        return self._list(topic_id)

    def _list(self, topic_id, identifiers=None):
        with self.database.session() as session:
            topic = session.get(Topic, topic_id)
            if topic is None or session.get(TopicRemoval, topic_id) is not None:
                raise ContentRunError("选题不存在或已移除。", code="not_found", status_code=404)
            query = select(ProductionTask).where(ProductionTask.topic_id == topic_id)
            if identifiers is not None:
                query = query.where(ProductionTask.id.in_(identifiers))
            tasks = list(session.scalars(query.order_by(ProductionTask.position, ProductionTask.id)))
            latest = {}
            if tasks:
                for run in session.scalars(select(ContentRun).where(ContentRun.task_id.in_([task.id for task in tasks]))
                        .order_by(ContentRun.created_at.desc(), ContentRun.id.desc())):
                    latest.setdefault(run.task_id, run)
            items = []
            for task in tasks:
                run = latest.get(task.id)
                snapshot = run.input_snapshot_json if run else {}
                if run is not None and any(snapshot.get(key) != value for key, value in (
                        ("creator_id", topic.series.creator_id), ("series_id", topic.series_id), ("topic_id", topic.id))):
                    raise ContentRunError("制作任务的冻结归属与当前账号不一致。", code="scope_conflict", status_code=403)
                pair = snapshot.get("composition") or {}
                summary = snapshot.get("task_skill_summary") or snapshot.get("skill_name") or " + ".join(
                    str(pair.get(role, {}).get("id", "")) for role in ("mind", "production"))
                items.append(dict(id=task.id, topic_id=task.topic_id, title=task.title, brief=task.brief,
                    position=task.position, run_id=run.id if run else None, run_version=run.version if run else None,
                    status=run.status.value if run else "queued", skill_overrides=snapshot.get("skill_overrides"),
                    skill_summary=summary))
            return dict(topic_id=topic_id, creator_id=topic.series.creator_id, items=items, collection_id=tasks[0].collection_id if tasks else None)
