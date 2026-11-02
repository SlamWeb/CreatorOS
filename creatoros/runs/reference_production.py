"""Atomic reference-to-production application service shared by all callers."""
from hashlib import sha256
import json

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select, text

from creatoros.integrations.topic_references import TopicReferenceStore
from creatoros.operations.models import AddTopicsOperation, OperationPlan, TopicDraft
from creatoros.operations.service import PendingOperationError
from creatoros.storage import ContentRun, ContentRevision, ContentAttempt, Series, WriteReceipt
from .errors import ContentRunError
from .skill_selection import resolve_task_skill_ids, resolve_image_skills
from .task_contracts import TaskExecutionSpec


class ReferenceProductionInput(BaseModel):
    """Reference intent only; caller identity and audit context are supplied separately."""
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)
    reference_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    title: str = Field(min_length=1, max_length=240)
    request_id: str = Field(min_length=8, max_length=64)
    skill_ids: list[str] | None = Field(default=None, min_length=1, max_length=12)


class ReferenceProductionService:
    def __init__(self, database, runs, executor, operations, *, library, references=None):
        self.database, self.runs, self.executor = database, runs, executor
        self.operations, self.library = operations, library
        self.references = references or TopicReferenceStore(database)

    def submit(self, series_id: str, request: ReferenceProductionInput | dict, *, creator_id: str,
               origin: str = "web", origin_session_id: str | None = None) -> ContentRun:
        """Freeze inputs and authorize one initial Run in one transaction.

        The caller provides its already established account identity. Ownership
        is checked again inside the write transaction, independently of HTTP.
        Replay uses the accepted frozen Skill selection, never current bytes.
        """
        try:
            request = ReferenceProductionInput.model_validate(request)
        except ValidationError as error:
            raise ContentRunError("请求参数不符合接口契约。", code="invalid_request", status_code=422) from error
        with self.database.session() as session:
            self._owned_series(session, series_id, creator_id)
        try:
            reference = self.references.load(series_id, request.reference_id)
        except (ValueError, OSError) as error:
            raise ContentRunError("参考图片或要求不可用，请检查图片是否完整且属于当前栏目。",
                                  code="invalid_request", status_code=422) from error
        topic_id = "reference-" + sha256(json.dumps([series_id, request.request_id]).encode()).hexdigest()[:24]
        plan = OperationPlan(operations=[AddTopicsOperation(series_id=series_id, topics=[
            TopicDraft(topic_id=topic_id, title=request.title, brief=reference.instructions,
                       source="manual", reference_id=reference.id)])])
        try:
            # Same lock order as ProductionTaskService. The dispatcher cannot
            # observe a Topic, Run or queue authorization before commit.
            with self.executor._lock:
                self.executor.start()
                if self.executor._readiness_error():
                    raise ContentRunError("生产队列暂不可用，请核对后重试。", code="executor_unavailable")
                with self.runs._write_lock, self.database.session() as session:
                    if self.database.engine.dialect.name == "sqlite":
                        session.execute(text("BEGIN IMMEDIATE"))
                    series = self._owned_series(session, series_id, creator_id)
                    receipt = session.get(WriteReceipt, request.request_id)
                    selection = self._selection(session, series, topic_id, request, receipt)
                    self.operations.execute_direct(
                        "按参考作品与用户要求直接制作，不生成候选。", plan, scope_series_id=series_id,
                        request_id=request.request_id, origin=origin if origin in {"web", "agent", "cli"} else "web",
                        bound_session=session)
                    run = self.runs._create(topic_id, idempotency_key=f"content:{topic_id}", bound_session=session,
                                            execution_spec=selection, origin_session_id=origin_session_id)
                    if receipt is None:
                        receipt = session.get(WriteReceipt, request.request_id)
                        receipt.response_json = {**receipt.response_json, "reference_production": {
                            "skill_ids": request.skill_ids, "run_id": run.id}}
                    self._enqueue_unstarted(session, run)
                self.executor._wake.set()
        except PendingOperationError as error:
            raise ContentRunError(str(error), code="conflict") from error
        return run

    @staticmethod
    def _owned_series(session, series_id, creator_id):
        series = session.get(Series, series_id)
        if (series is None or not series.is_active or not series.creator_id or not series.creator
                or not series.creator.is_active or not creator_id or series.creator_id != creator_id):
            raise ContentRunError("账号或栏目未启用或不属于当前账号。", code="scope_conflict", status_code=403)
        return series

    def _selection(self, session, series, topic_id, request, receipt):
        if receipt is None:
            if request.skill_ids is not None:
                return resolve_task_skill_ids(self.library, series.creator_id, request.skill_ids, kind="image")
            resolve_image_skills(self.database, session, series, protocol=self.runs.production_protocol)
            return None
        saved = receipt.response_json
        recorded = saved.get("reference_production")
        if ((recorded.get("skill_ids") if recorded else None) != request.skill_ids
                or saved.get("creator_id", series.creator_id) != series.creator_id):
            raise ContentRunError("请求标识已用于不同的 Skill 选择或账号。", code="idempotency_conflict")
        existing = session.scalar(select(ContentRun).where(ContentRun.idempotency_key == f"content:{topic_id}"))
        if recorded and (existing is None or recorded.get("run_id") != existing.id):
            raise ContentRunError("已接收的制作记录不可用，请核对原任务。", code="idempotency_conflict")
        if existing is not None:
            if existing.input_snapshot_json.get("creator_id") != series.creator_id:
                raise ContentRunError("冻结任务与当前账号不一致。", code="scope_conflict", status_code=403)
            spec = existing.input_snapshot_json.get("execution_spec")
            return TaskExecutionSpec.model_validate(spec) if spec else None
        return None

    def _enqueue_unstarted(self, session, run):
        first = session.scalar(select(ContentRevision).where(ContentRevision.content_run_id == run.id,
                               ContentRevision.revision_number == 1))
        attempted = session.scalar(select(ContentAttempt.id).where(ContentAttempt.revision_id == first.id).limit(1))
        if run.status.value == "queued" and run.active_revision_number == 1 and not attempted:
            self.executor.queue.enqueue(run.id, run.version, bound_session=session)
