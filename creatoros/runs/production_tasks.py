"""Explicit independent pieces of one Topic, using the existing Run queue."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from dataclasses import replace
from uuid import uuid4
from typing import Literal

from pydantic import Field, model_validator
from sqlalchemy import func, select

from creatoros.storage import ContentRun, MediaCollection, ProductionTask, Topic, TopicRemoval, WriteReceipt
from creatoros.storage.models import ManagedMaterial
from .models import RunModel, SkillOverrides
from .service import ContentRunError
from .submission import ProductionSubmission
from .prepared_submission import PreparedTask, PreparedTasks, production_scope, task_skill_ids, verify_prepared_tasks
from creatoros.integrations.skill_authority import prepare_skill_authority
from creatoros.integrations.skill_library import SkillLibraryService


class SelectedTaskSkill(RunModel):
    skill_id: str = Field(min_length=1, max_length=120)
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    role: Literal["primary", "supporting"]


class VideoTaskInputs(RunModel):
    collection_id: str = Field(min_length=1)
    export_id: str | None = None
    bgm_material_id: str | None = Field(default=None, min_length=1)
    bgm_reference: str | None = Field(default=None, min_length=1, max_length=10000)

    @model_validator(mode="after")
    def one_bgm(self):
        if bool(self.bgm_material_id) == bool(self.bgm_reference):
            raise ValueError("请选择音频文件，或提供 BGM 分享链接。")
        return self


class ProductionTaskInput(RunModel):
    title: str = Field(min_length=1, max_length=240)
    brief: str | None = Field(default=None, min_length=1, max_length=10000)
    skill_overrides: SkillOverrides | None = None
    kind: Literal["image", "video"] = "image"
    skills: list[SelectedTaskSkill] = Field(default_factory=list, max_length=12)
    skill_ids: list[str] | None = Field(default=None, min_length=1, max_length=12)
    inputs: VideoTaskInputs | None = None
    depends_on: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def stage_inputs(self):
        if self.skill_ids is not None:
            if self.skills or self.skill_overrides:
                raise ValueError("请只使用一种 Skill 选择方式。")
            if any(not value.strip() or len(value) > 120 for value in self.skill_ids) or len(set(self.skill_ids)) != len(self.skill_ids):
                raise ValueError("Skill 选择不能为空或重复。")
        if self.kind == "image" and (self.inputs or self.depends_on):
            raise ValueError("图片任务不接收剪辑素材或阶段依赖。")
        if self.kind == "video" and (not self.inputs or self.skill_overrides):
            raise ValueError("视频任务需要素材输入和独立 Skill 选择。")
        return self


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


class ProductionTaskService:
    def __init__(self, database, runs, executor, *, library=None, collections=None, materials=None):
        self.database, self.runs, self.executor = database, runs, executor
        self.submissions = ProductionSubmission(database, runs, executor)
        from .staged_tasks import StagedTasks
        self.stages = StagedTasks(self, library, collections, materials)
        executor.advance_tasks = self.stages.advance

    @staticmethod
    def _assert_task_owner(task, topic):
        frozen = task.definition_json or {}
        if frozen and (frozen.get("creator_id") != topic.series.creator_id
                       or frozen.get("series_id") != topic.series_id):
            raise ContentRunError("制作任务的冻结归属与当前账号不一致。", code="scope_conflict", status_code=403)

    def _existing_task_ids(self, topic_id, request_id, request_digest):
        with self.database.session() as session:
            topic = session.get(Topic, topic_id)
            if topic is None or session.get(TopicRemoval, topic_id) is not None:
                raise ContentRunError("选题不存在或已移除。", code="not_found", status_code=404)
            series = topic.series
            if not series.is_active or not series.creator_id or not series.creator or not series.creator.is_active:
                raise ContentRunError("账号或栏目未启用。")
            receipt = session.get(WriteReceipt, request_id)
            if receipt is None:
                return None
            saved = receipt.response_json
            if (receipt.operation != "queue_topics" or receipt.resource_id != topic_id
                    or saved.get("kind") != "production_tasks" or saved.get("request_digest") != request_digest
                    or saved.get("creator_id") != series.creator_id):
                raise ContentRunError("请求标识已用于不同的制作要求。", code="idempotency_conflict")
            return list(saved["task_ids"])

    @staticmethod
    def _input_scope(session, topic, item, collection_version=None):
        if item.kind != "video":
            return None
        inputs = item.inputs
        collection = session.get(MediaCollection, inputs.collection_id)
        if (collection is None or collection.creator_id != topic.series.creator_id
                or collection.topic_id not in (None, topic.id)):
            raise ContentRunError("输入素材集不存在或不属于当前账号/选题。", code="scope_conflict", status_code=403)
        if collection_version is not None and collection.version != collection_version:
            raise ContentRunError("输入素材集已变化，请重新核对选片。", code="scope_conflict", status_code=409)
        material = session.get(ManagedMaterial, inputs.bgm_material_id) if inputs.bgm_material_id else None
        if inputs.bgm_material_id and (material is None or material.creator_id != topic.series.creator_id):
            raise ContentRunError("音频素材不存在或不属于当前账号。", code="scope_conflict", status_code=403)
        return {"collection": [collection.id, collection.creator_id, collection.topic_id, collection_version],
                "material": ([material.id, material.creator_id, material.kind, material.sha256, material.size_bytes]
                             if material else None)}

    def _prepare_tasks(self, topic_id, request_id, request_digest, inputs, origin_session_id, context_snapshot_ref):
        """Resolve and freeze all local inputs before opening the writer transaction."""
        with self.database.session() as session:
            topic = session.get(Topic, topic_id)
            if topic is None or session.get(TopicRemoval, topic_id) is not None:
                raise ContentRunError("选题不存在或已移除。", code="not_found", status_code=404)
            series = topic.series
            if not series.is_active or not series.creator_id or not series.creator or not series.creator.is_active:
                raise ContentRunError("账号或栏目未启用。")
            receipt = session.get(WriteReceipt, request_id)
            if receipt is not None:
                saved = receipt.response_json
                if (receipt.operation != "queue_topics" or receipt.resource_id != topic_id
                        or saved.get("kind") != "production_tasks" or saved.get("request_digest") != request_digest
                        or saved.get("creator_id") != series.creator_id):
                    raise ContentRunError("请求标识已用于不同的制作要求。", code="idempotency_conflict")
                return None
            self.runs._assert_no_active_iteration(session, topic_id)
            prior_collection = session.scalar(select(ProductionTask.collection_id).where(
                ProductionTask.topic_id == topic_id, ProductionTask.collection_id.is_not(None)).limit(1))
            collection = session.get(MediaCollection, prior_collection) if prior_collection else None
            if prior_collection and (collection is None or collection.creator_id != series.creator_id
                                     or collection.topic_id != topic_id):
                raise ContentRunError("制作素材集的归属与当前选题不一致。", code="scope_conflict", status_code=403)
            if collection is None:
                collection = MediaCollection(id=str(uuid4()), creator_id=series.creator_id, topic_id=topic_id,
                    name=topic.title[:120], request_id=f"production-tasks:{topic_id}",
                    create_digest=_digest(dict(kind="production_tasks", topic_id=topic_id)),
                    run_ids_json=[], order_ids_json=[], excluded_ids_json=[])
            position = session.scalar(select(func.max(ProductionTask.position)).where(ProductionTask.topic_id == topic_id))
            position = 0 if position is None else position + 1
            planned = []
            for index, item in enumerate(inputs):
                task = ProductionTask(id=str(uuid4()), topic_id=topic_id, title=item.title, brief=item.brief,
                    position=position + index, collection_id=collection.id, create_request_id=request_id)
                try:
                    staged = self.stages.prepare(session, task, topic, item, origin_session_id, context_snapshot_ref)
                    dispatch_inputs = None
                    run = None
                    if staged and not task.dependency_json:
                        dispatch_inputs = self.stages.prepare_dispatch_inputs(task)
                        definition, spec, _, _ = dispatch_inputs
                        run = self.runs._create(topic_id, task_id=task.id, bound_session=session, execution_spec=spec,
                            origin_session_id=definition.get("origin_session_id"),
                            context_snapshot_ref=definition.get("context_snapshot_ref"), prepare_only=True,
                            transient_task=task, transient_collection=collection)
                    elif not staged:
                        run = self.runs._create(topic_id, task_id=task.id, bound_session=session,
                            skill_overrides=item.skill_overrides, origin_session_id=origin_session_id,
                            context_snapshot_ref=context_snapshot_ref, prepare_only=True,
                            transient_task=task, transient_collection=collection, selection_library=self.stages.library)
                    session.expire_all()
                    version = dispatch_inputs[2] if dispatch_inputs is not None else None
                    input_scope = self._input_scope(session, topic, item, version) if staged else None
                    planned.append(PreparedTask(task=task, staged=staged, dispatch_inputs=dispatch_inputs,
                                                run=run, input_scope=input_scope))
                except ContentRunError:
                    raise
                except ValueError as error:
                    raise ContentRunError(str(error), code="invalid_task", status_code=422) from error
            return PreparedTasks(scope=production_scope(session, topic, series), previous_collection_id=prior_collection,
                                 collection=None if prior_collection else collection, position=position, items=tuple(planned))

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
        # Keep old request hashes unchanged; old callers have no skill_ids key.
        canonical_tasks = [item.model_dump(mode="json", exclude={"skill_ids"} if item.skill_ids is None else set()) for item in inputs]
        for item in canonical_tasks:
            if item.get("inputs") is not None and item["inputs"].get("bgm_reference") is None:
                item["inputs"].pop("bgm_reference", None)  # Keep old file-only replay hashes stable.
        digest = _digest(dict(topic_id=topic_id, tasks=canonical_tasks))
        existing = self._existing_task_ids(topic_id, request_id, digest)
        if existing is not None:
            return self._list(topic_id, existing)
        with self.submissions.request(request_id):
            existing = self._existing_task_ids(topic_id, request_id, digest)
            if existing is not None:
                return self._list(topic_id, existing)
            self.submissions.assert_ready()
            prepared = self._prepare_tasks(topic_id, request_id, digest, inputs, origin_session_id, context_snapshot_ref)
            verify = (lambda: verify_prepared_tasks(self.runs, self.stages, prepared)) if prepared else None
            library = self.stages.library or self.runs.skill_library or SkillLibraryService(self.database, self.stages.catalog)
            authority = (lambda: prepare_skill_authority(self.database, library,
                prepared.scope['series'][1], task_skill_ids(prepared))) if prepared else None
            with self.executor._lock:
                existing = self._existing_task_ids(topic_id, request_id, digest)
                if existing is not None:
                    return self._list(topic_id, existing)
                with self.submissions.transaction(sqlite_immediate=prepared is not None,
                        verify_prepared=verify, prepare_authority=authority) as submission:
                    session = submission.session
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
                        submission.require_authority()
                        if prepared is None or production_scope(session, topic, series) != prepared.scope:
                            raise ContentRunError("账号、栏目或选题已变化，请核对后重新提交。", code="scope_conflict", status_code=409)
                        self.runs._assert_no_active_iteration(session, topic_id)
                        current_collection = session.scalar(select(ProductionTask.collection_id).where(
                            ProductionTask.topic_id == topic_id, ProductionTask.collection_id.is_not(None)).limit(1))
                        current_position = session.scalar(select(func.max(ProductionTask.position)).where(
                            ProductionTask.topic_id == topic_id))
                        current_position = 0 if current_position is None else current_position + 1
                        if prepared.previous_collection_id is not None and current_collection != prepared.previous_collection_id:
                            raise ContentRunError("制作素材集已变化，请核对后重新提交。", code="conflict", status_code=409)
                        if current_collection is not None:
                            collection = session.get(MediaCollection, current_collection)
                            if collection is None or collection.creator_id != series.creator_id or collection.topic_id != topic_id:
                                raise ContentRunError("制作素材集的归属与当前选题不一致。", code="scope_conflict", status_code=403)
                        elif prepared.collection is not None:
                            session.add(prepared.collection)
                            session.flush()
                            current_collection = prepared.collection.id
                        else:
                            raise ContentRunError("制作素材集已变化，请核对后重新提交。", code="conflict", status_code=409)
                        identifiers = []
                        for index, (item, plan) in enumerate(zip(inputs, prepared.items, strict=True)):
                            task = plan.task
                            if plan.staged:
                                version = plan.input_scope["collection"][3] if plan.input_scope else None
                                if self._input_scope(session, topic, item, version) != plan.input_scope:
                                    raise ContentRunError("输入素材归属已变化，请重新核对。", code="scope_conflict", status_code=409)
                                if plan.dispatch_inputs is not None and plan.dispatch_inputs[3] is not None:
                                    for run_id, revision_id, artifact_digest in plan.dispatch_inputs[3]:
                                        actual = self.stages.collections.image_binding_in_session(
                                            session, series.creator_id, item.inputs.collection_id, run_id, revision_id)
                                        if actual != artifact_digest:
                                            raise ContentRunError("导出来源版本已变化，请重新核对。", code="scope_conflict", status_code=409)
                                if task.dependency_json:
                                    current_dependencies = self.stages.dependencies(session, topic_id,
                                        [entry["task_id"] for entry in task.dependency_json])
                                    if current_dependencies != task.dependency_json:
                                        raise ContentRunError("等待任务版本已变化，请重新核对。", code="scope_conflict", status_code=409)
                            task.position = current_position + index
                            task.collection_id = current_collection
                            run = plan.run
                            if run is not None:
                                scope = dict(run.scope)
                                scope["task"] = [task.id, task.topic_id, task.title, task.brief, task.collection_id]
                                run = replace(run, scope=scope)
                            session.add(task)
                            session.flush()
                            if plan.staged:
                                if not task.dependency_json:
                                    self.stages.dispatch(session, task, topic, prepared_inputs=plan.dispatch_inputs,
                                                         prepared_run=run)
                            else:
                                submission.create_task_run(topic_id, task_id=task.id,
                                    skill_overrides=item.skill_overrides, origin_session_id=origin_session_id,
                                    context_snapshot_ref=context_snapshot_ref, prepared=run)
                            identifiers.append(task.id)
                        session.add(WriteReceipt(request_id=request_id, operation="queue_topics", resource_id=topic_id,
                            origin="web", response_json=dict(kind="production_tasks", request_digest=digest,
                                creator_id=series.creator_id, task_ids=identifiers)))
                        session.flush()
            try:
                return self._list(topic_id, identifiers)
            except Exception as error:
                raise ContentRunError("任务已提交或结果待核对，请查询同一请求回执，勿重新提交。",
                                      code="studio_outcome_unknown", status_code=409) from error

    def list(self, topic_id):
        return self._list(topic_id)

    def receipt(self, topic_id, request_id, *, creator_id=None):
        """Read one submission without dispatching or treating absence as failure."""
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 64:
            raise ContentRunError("请求标识无效。", code="invalid_request", status_code=422)
        with self.database.session() as session:
            topic = session.get(Topic, topic_id)
            if topic is None or session.get(TopicRemoval, topic_id) is not None:
                raise ContentRunError("选题不存在或已移除。", code="not_found", status_code=404)
            series = topic.series
            if (not series.is_active or not series.creator_id or not series.creator or not series.creator.is_active
                    or (creator_id is not None and creator_id != series.creator_id)):
                raise ContentRunError("账号或栏目未启用。", code="scope_conflict", status_code=403)
            saved = session.get(WriteReceipt, request_id)
            if saved is None:
                return {"topic_id": topic_id, "request_id": request_id, "status": "unknown",
                        "message": "尚未查到回执，结果仍不确定；未重新提交。"}
            data = saved.response_json
            if (saved.operation != "queue_topics" or saved.resource_id != topic_id or not isinstance(data, dict)
                    or data.get("kind") != "production_tasks" or data.get("creator_id") != series.creator_id):
                raise ContentRunError("当前账号不能查看此制作回执。", code="scope_conflict", status_code=403)
            identifiers = list(data["task_ids"])
        return {**self._list(topic_id, identifiers), "request_id": request_id, "status": "accepted"}

    def control(self, topic_id, task_id, action):
        with self.executor._lock, self.runs._write_lock, self.database.session() as session:
            task = session.get(ProductionTask, task_id)
            if task is None or task.topic_id != topic_id:
                raise ContentRunError("制作任务不存在。", code="not_found", status_code=404)
            topic = session.get(Topic, topic_id)
            if topic is None or session.get(TopicRemoval, topic_id):
                raise ContentRunError("选题已移除。", code="not_found", status_code=404)
            self._assert_task_owner(task, topic)
            if action == "remove":
                if task.archived_at is None:
                    run = session.scalar(select(ContentRun).where(ContentRun.task_id == task.id)
                        .order_by(ContentRun.created_at.desc(), ContentRun.id.desc()).limit(1))
                    if (run is not None and run.status.value in {"queued", "producing", "validating"}) or (run is None and task.dispatch_state == "waiting"):
                        raise ContentRunError("任务仍在运行或等待，请先停止再移除。")
                    if run is not None and run.error_type == "codex_interrupt_unconfirmed":
                        raise ContentRunError("Codex 停止状态未确认，请先核对线程；任务记录保留。")
                    task.archived_at = datetime.now(timezone.utc)
            elif task.archived_at is not None:
                raise ContentRunError("任务已移除，历史记录仍保留。")
            elif task.dispatch_state == "dispatched":
                raise ContentRunError("已进入生产，请在内容页操作该任务。")
            elif action == "cancel":
                task.dispatch_state = "cancelled"
            elif action == "resume":
                topic = session.get(Topic, topic_id)
                if not topic or session.get(TopicRemoval, topic_id):
                    raise ContentRunError("选题已移除。", code="not_found", status_code=404)
                try:
                    task.dependency_json = self.stages.dependencies(session, topic_id,
                        [dependency["task_id"] for dependency in task.dependency_json])
                except ValueError as error:
                    raise ContentRunError(str(error), code="invalid_task", status_code=422) from error
                task.dispatch_state, task.last_error = "waiting", None
            elif action != "remove":
                raise ValueError("Unknown stage action")
        self.executor._wake.set()
        return self._list(topic_id) if action == "remove" else self._list(topic_id, [task_id])

    def _list(self, topic_id, identifiers=None):
        with self.database.session() as session:
            topic = session.get(Topic, topic_id)
            if topic is None or session.get(TopicRemoval, topic_id) is not None:
                raise ContentRunError("选题不存在或已移除。", code="not_found", status_code=404)
            query = select(ProductionTask).where(ProductionTask.topic_id == topic_id)
            if identifiers is not None:
                query = query.where(ProductionTask.id.in_(identifiers))
            else:
                query = query.where(ProductionTask.archived_at.is_(None))
            tasks = list(session.scalars(query.order_by(ProductionTask.position, ProductionTask.id)))
            latest = {}
            if tasks:
                for run in session.scalars(select(ContentRun).where(ContentRun.task_id.in_([task.id for task in tasks]))
                        .order_by(ContentRun.created_at.desc(), ContentRun.id.desc())):
                    latest.setdefault(run.task_id, run)
            items = []
            for task in tasks:
                self._assert_task_owner(task, topic)
                run = latest.get(task.id)
                snapshot = run.input_snapshot_json if run else {}
                if run is not None and any(snapshot.get(key) != value for key, value in (
                        ("creator_id", topic.series.creator_id), ("series_id", topic.series_id), ("topic_id", topic.id))):
                    raise ContentRunError("制作任务的冻结归属与当前账号不一致。", code="scope_conflict", status_code=403)
                pair = snapshot.get("composition") or {}
                summary = snapshot.get("task_skill_summary") or snapshot.get("skill_name") or " + ".join(
                    str(pair.get(role, {}).get("id", "")) for role in ("mind", "production"))
                spec = snapshot.get("execution_spec") or task.definition_json.get("execution_spec") or {}
                if spec and not snapshot.get("task_skill_summary"):
                    summary = " + ".join(item["skill_id"] for item in spec["skills"])
                items.append(dict(id=task.id, topic_id=task.topic_id, title=task.title, brief=task.brief,
                    position=task.position, run_id=run.id if run else None, run_version=run.version if run else None,
                    status=run.status.value if run else task.dispatch_state,
                    kind=task.kind, dispatch_state=task.dispatch_state, last_error=task.last_error,
                    depends_on=[item["task_id"] for item in task.dependency_json],
                    skill_overrides=snapshot.get("skill_overrides"),
                    skill_summary=summary))
            return dict(topic_id=topic_id, series_id=topic.series_id, creator_id=topic.series.creator_id,
                        items=items, collection_id=tasks[0].collection_id if tasks else None)
