"""Topic edit identity, capabilities and successful write receipts.

The HTTP caller holds the production/discussion locks. Editing a Topic never
rewrites any Run, revision, attempt, artifact or historical input snapshot.
"""
from __future__ import annotations

import hashlib
import json
from datetime import timezone

from sqlalchemy import select

from creatoros.storage import ContentRun, ContentRunStatus, Series, SkillIteration, Topic, TopicRemoval, WriteReceipt
from creatoros.storage.models import ProductionQueueItem, ProductionTask, TopicStatus
from .schemas import TopicView


class TopicMutationError(ValueError):
    def __init__(self, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


def edit_digest(topic: Topic) -> str:
    updated = topic.updated_at
    if updated is not None and updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    body = {"id": topic.id, "series_id": topic.series_id, "title": topic.title,
            "brief": topic.brief, "reference_id": topic.reference_id,
            "source": topic.source.value, "status": topic.status.value,
            "position": topic.position, "updated_at": updated.isoformat() if updated else None}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def block_reasons(session, topics, runs, discussions=None) -> dict[str, str]:
    """Batch capability check shared by read projection and actual writes."""
    ids = {topic.id for topic in topics}
    if not ids:
        return {}
    series = {item.id: item for item in session.scalars(select(Series).where(
        Series.id.in_({topic.series_id for topic in topics})))}
    removed = set(session.scalars(select(TopicRemoval.topic_id).where(TopicRemoval.topic_id.in_(ids))))
    queued = set(session.scalars(select(ContentRun.topic_id).join(
        ProductionQueueItem, ProductionQueueItem.run_id == ContentRun.id).where(
        ContentRun.topic_id.in_(ids), ProductionQueueItem.status.in_({"pending", "running"}))))
    queued.update(session.scalars(select(ProductionTask.topic_id).where(
        ProductionTask.topic_id.in_(ids), ProductionTask.dispatch_state == "waiting")))
    iterating = set()
    for iteration in session.scalars(select(SkillIteration).where(
            SkillIteration.topic_id.in_(ids),
            SkillIteration.status.in_({"queued", "running", "waiting_production", "paused"}))):
        rounds = (iteration.state_json or {}).get("rounds", [])
        trial = rounds[-1] if rounds else {}
        if (iteration.status != "paused" or trial.get("trial_job_id") and trial.get("status") not in
                {"completed", "failed", "interrupted", "cancelled"}):
            iterating.add(iteration.topic_id)
    active = {run.topic_id for run in runs if run.status in {
        ContentRunStatus.PRODUCING, ContentRunStatus.VALIDATING}}
    related_runs = {run.id: run.topic_id for run in runs}
    discussing = {related_runs[item["run_id"]] for item in discussions._records()
                  if item.get("run_id") in related_runs and item.get("status") in {"queued", "running"}
                  } if discussions is not None else set()
    result = {}
    for topic in topics:
        if topic.id in removed:
            result[topic.id] = "选题已经移出内容库，不能修改或再次移除。"
        elif topic.series_id not in series or not series[topic.series_id].is_active:
            result[topic.id] = "栏目已移出工作区，不能修改或移除选题。"
        elif topic.id in iterating:
            result[topic.id] = "RSI 仍在使用这个选题，暂停也不会取消在途试产；请完成或核对原任务后再修改或移除。"
        elif topic.status is TopicStatus.PRODUCING or topic.id in active:
            result[topic.id] = "生产或文件验收仍在进行，请等待完成后再修改或移除。"
        elif topic.id in queued:
            result[topic.id] = "该选题已有待执行或正在执行的生产任务，请等待完成后再修改或移除。"
        elif topic.id in discussing:
            result[topic.id] = "该内容仍有讨论任务进行中，请等待结束后再修改或移除。"
    return result


def topic_view(topic: Topic, runs, *, management_actions=None) -> TopicView:
    all_related = sorted((run for run in runs if run.topic_id == topic.id),
                     key=lambda item: (item.created_at, item.id), reverse=True)
    related = [run for run in all_related if run.task_id is None]
    task_runs = {}
    from sqlalchemy.orm import object_session
    from creatoros.storage import ProductionTask
    session = object_session(topic)
    hidden = set()
    ids = {run.task_id for run in all_related if run.task_id}
    if session is not None and ids:
        hidden = set(session.scalars(select(ProductionTask.id).where(
            ProductionTask.id.in_(ids), ProductionTask.archived_at.is_not(None))))
    for child in all_related:
        if child.task_id and child.task_id not in hidden:
            task_runs.setdefault(child.task_id, child)
    collection_id = None
    if task_runs:
        if session is not None:
            task = session.get(ProductionTask, next(iter(task_runs)))
            collection_id = task.collection_id if task else None
    run = related[0] if related else None
    if run is None:
        actions = ["start"] if topic.status.value == "queued" and not task_runs else ["view"]
    elif run.status is ContentRunStatus.QUEUED:
        actions = ["start", "view"]
    elif run.status in {ContentRunStatus.INTERRUPTED, ContentRunStatus.FAILED} and (
            run.status is ContentRunStatus.INTERRUPTED or run.retryable):
        actions = ["resume", "view"]
    else:
        actions = ["view"]
    return TopicView(id=topic.id, series_id=topic.series_id, title=topic.title, brief=topic.brief,
        source=topic.source.value, status=topic.status.value, position=topic.position,
        existing_run_id=run.id if run else None, existing_run_status=run.status.value if run else None,
        existing_run_version=run.version if run else None, available_actions=actions,
        edit_digest=edit_digest(topic), management_actions=management_actions or ["get"],
        production_task_count=len(task_runs), production_collection_id=collection_id,
        production_task_active_count=sum(child.status in {
            ContentRunStatus.QUEUED, ContentRunStatus.PRODUCING, ContentRunStatus.VALIDATING
        } for child in task_runs.values()))


def read_edit_receipt(session, topic_id: str, request_id: str):
    receipt = session.get(WriteReceipt, request_id)
    if receipt is None:
        return None
    if receipt.operation != "edit_topic" or receipt.resource_id != topic_id:
        raise TopicMutationError("请求标识已用于其他操作或选题，请重新读取后提交。")
    return {**receipt.response_json, "deduplicated": True}


def edit_topic(database, topic_id: str, *, changes: dict, expected_digest=None,
               request_id=None, origin="web", discussions=None):
    if (expected_digest is None) != (request_id is None):
        raise TopicMutationError("修改摘要和请求标识必须同时提供。", status_code=422)
    if not changes or not set(changes) <= {"title", "brief"}:
        raise TopicMutationError("没有需要修改的内容，或包含不允许修改的字段。")
    cleaned = {}
    for key, value in changes.items():
        if key == "title":
            if not isinstance(value, str) or not value.strip():
                raise TopicMutationError("标题不能为空。")
            cleaned[key] = value.strip()
        else:
            if value is not None and not isinstance(value, str):
                raise TopicMutationError("选题内容必须是文字或空值。", status_code=422)
            cleaned[key] = (value.strip() or None) if isinstance(value, str) else None
    payload = {"expected_digest": expected_digest, "changes": cleaned}
    with database.session() as session:
        if request_id:
            replay = read_edit_receipt(session, topic_id, request_id)
            if replay is not None:
                if replay.get("request_payload") != payload:
                    raise TopicMutationError("请求标识已用于不同的修改内容，请重新读取后提交。")
                return replay
        topic = session.get(Topic, topic_id)
        if topic is None:
            raise TopicMutationError("选题不存在。", status_code=404)
        runs = list(session.scalars(select(ContentRun).where(ContentRun.topic_id == topic_id)))
        reason = block_reasons(session, [topic], runs, discussions).get(topic_id)
        if reason:
            raise TopicMutationError(reason)
        if expected_digest is not None and expected_digest != edit_digest(topic):
            raise TopicMutationError("选题已发生变化，请重新读取后再修改。")
        changed = [key for key, value in cleaned.items() if getattr(topic, key) != value]
        for key, value in cleaned.items():
            setattr(topic, key, value)
        session.flush()
        result = {"ok": True, "topic": topic_view(topic, runs,
            management_actions=["get", "update", "remove"]).model_dump(mode="json"),
            "deduplicated": False, "changed_fields": changed, "effect": "existing_runs_unchanged"}
        if request_id:
            result.update(request_id=request_id, creator_id=session.get(Series, topic.series_id).creator_id,
                          request_payload=payload)
            session.add(WriteReceipt(request_id=request_id, operation="edit_topic", resource_id=topic_id,
                                     origin=origin, response_json=result))
            session.flush()
        return result
