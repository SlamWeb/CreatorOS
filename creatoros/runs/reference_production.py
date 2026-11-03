"""Atomic reference-to-production application service shared by all callers."""

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm.attributes import set_committed_value

from creatoros.integrations.topic_references import TopicReferenceStore
from creatoros.integrations.production_identity import reference_topic_id
from creatoros.operations.models import AddTopicsOperation, OperationPlan, TopicDraft
from creatoros.operations.service import PendingOperationError
from creatoros.storage import ContentRun, Series, Topic, TopicSource, TopicStatus, WriteReceipt
from .errors import ContentRunError
from .skill_selection import resolve_task_skill_ids, resolve_image_skills
from .submission import ProductionSubmission
from .task_contracts import TaskExecutionSpec
from .prepared_submission import run_skill_ids, verify_prepared_run
from creatoros.integrations.skill_authority import prepare_skill_authority


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
        self.submissions = ProductionSubmission(database, runs, executor)
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
        with self.submissions.request(request.request_id):
            with self.database.session() as session:
                self._owned_series(session, series_id, creator_id)
            try:
                reference = self.references.load(series_id, request.reference_id)
            except (ValueError, OSError) as error:
                raise ContentRunError("参考图片或要求不可用，请检查图片是否完整且属于当前栏目。",
                                      code="invalid_request", status_code=422) from error
            topic_id = reference_topic_id(series_id, request.request_id)
            plan = OperationPlan(operations=[AddTopicsOperation(series_id=series_id, topics=[
                TopicDraft(topic_id=topic_id, title=request.title, brief=reference.instructions,
                           source="manual", reference_id=reference.id)])])
            self.submissions.assert_ready("生产队列暂不可用，请核对后重试。")
            prepared, selected = None, None
            with self.database.session() as session:
                series = self._owned_series(session, series_id, creator_id)
                receipt = session.get(WriteReceipt, request.request_id)
                if receipt is None:
                    selected = self._selection(session, series, topic_id, request, None)
                    topic = Topic(id=topic_id, series_id=series_id, title=request.title,
                                  brief=reference.instructions, source=TopicSource.MANUAL,
                                  reference_id=reference.id, status=TopicStatus.QUEUED, position=1)
                    set_committed_value(topic, "series", series)
                    prepared = self.runs._create(topic_id, idempotency_key=f"content:{topic_id}",
                        execution_spec=selected, origin_session_id=origin_session_id,
                        bound_session=session, transient_topic=topic, prepare_only=True)
            verify = ((lambda: verify_prepared_run(self.runs, prepared, catalog=self.library.catalog))
                      if prepared is not None else None)
            authority = ((lambda: prepare_skill_authority(self.database, self.library, creator_id,
                                                          run_skill_ids(prepared))) if prepared is not None else None)
            try:
                with self.submissions.transaction(sqlite_immediate=True,
                        unavailable_message="生产队列暂不可用，请核对后重试。", verify_prepared=verify,
                        prepare_authority=authority) as submission:
                    session = submission.session
                    series = self._owned_series(session, series_id, creator_id)
                    receipt = session.get(WriteReceipt, request.request_id)
                    selection = self._selection(session, series, topic_id, request, receipt) if receipt else selected
                    if receipt is None and prepared is None:
                        raise ContentRunError("请求状态已变化，请核对原任务。", code="idempotency_conflict")
                    if receipt is None:
                        submission.require_authority()
                    self.operations.execute_direct(
                        "按参考作品与用户要求直接制作，不生成候选。", plan, scope_series_id=series_id,
                        request_id=request.request_id, origin=origin if origin in {"web", "agent", "cli"} else "web",
                        bound_session=session)
                    run = submission.create_reference_run(topic_id, execution_spec=selection,
                        origin_session_id=origin_session_id, prepared=prepared if receipt is None else None)
                    if receipt is None:
                        receipt = session.get(WriteReceipt, request.request_id)
                        receipt.response_json = {**receipt.response_json, "reference_production": {
                            "skill_ids": request.skill_ids, "run_id": run.id}}
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
            resolve_image_skills(self.database, session, series, protocol=self.runs.production_protocol,
                                 library=self.library)
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
