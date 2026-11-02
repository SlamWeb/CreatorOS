"""Shared Web/Agent inputs; reference works never enter the shared Skill library."""
from hashlib import sha256
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import Field
from sqlalchemy import select, text

from creatoros.integrations.topic_references import TopicReferenceStore
from creatoros.operations.models import AddTopicsOperation, OperationPlan, TopicDraft
from creatoros.operations.service import PendingOperationError
from creatoros.storage import ContentRepository, ContentRun, ContentRevision, ContentAttempt, Series, WriteReceipt
from creatoros.runs import ContentRunError
from creatoros.runs.skill_selection import resolve_task_skill_ids, resolve_image_skills
from creatoros.runs.task_contracts import TaskExecutionSpec
from .schemas import WriteRequest, RunDetail
from .chat_attachments import MAX_ENCODED


class ReferenceImageRequest(WriteRequest):
    name: str = Field(min_length=1, max_length=255)
    data_base64: str = Field(min_length=1, max_length=MAX_ENCODED)


class ReferenceInputRequest(WriteRequest):
    image_ids: list[str] = Field(min_length=1, max_length=20)
    instructions: str = Field(default="", max_length=3000)
    reference_presentation: bool = False


class ReferenceProductionRequest(WriteRequest):
    reference_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    title: str = Field(min_length=1, max_length=240)
    request_id: str = Field(min_length=8, max_length=64)
    skill_ids: list[str] | None = Field(default=None, min_length=1, max_length=12)


def reference_routes(database, writes, runs, executor, queries):
    router = APIRouter(prefix="/api/series/{series_id}")
    store = TopicReferenceStore(database)

    def require_series(series_id, *, production=False):
        repository = ContentRepository(database)
        series = repository.get_series(series_id)
        if not series or not series.is_active:
            raise HTTPException(404, "栏目不存在或已停用。")
        creator = repository.get_creator(series.creator_id) if series.creator_id else None
        if (series.creator_id and (not creator or not creator.is_active)) or (production and not creator):
            raise HTTPException(409, "请先为栏目分配有效账号。")

    def checked(action):
        try:
            return action()
        except (ValueError, OSError) as error:
            raise HTTPException(422, "参考图片或要求不可用，请检查图片是否完整且属于当前栏目。") from error

    @router.post("/reference-images", status_code=201)
    def upload(series_id: str, payload: ReferenceImageRequest):
        require_series(series_id)
        return checked(lambda: store.upload(series_id, payload.name, payload.data_base64))

    @router.get("/reference-images/{image_id}")
    def image(series_id: str, image_id: str):
        require_series(series_id)
        path, mime = checked(lambda: store.image(series_id, image_id))
        return FileResponse(path, media_type=mime,
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @router.post("/reference-inputs", status_code=201)
    def freeze(series_id: str, payload: ReferenceInputRequest):
        require_series(series_id)
        ref = checked(lambda: store.freeze(series_id, **payload.model_dump()))
        return {"id": ref.id, "image_count": len(ref.images), "reference_presentation": ref.reference_presentation}

    @router.post("/reference-production", response_model=RunDetail, status_code=202)
    def produce(series_id: str, payload: ReferenceProductionRequest, request: Request):
        require_series(series_id, production=True)
        ref = checked(lambda: store.load(series_id, payload.reference_id))
        # Stable identity creates a durable Topic, not another topic-generation LLM call.
        topic_id = "reference-" + sha256(json.dumps([series_id, payload.request_id]).encode()).hexdigest()[:24]
        plan = OperationPlan(operations=[AddTopicsOperation(series_id=series_id, topics=[
            TopicDraft(topic_id=topic_id, title=payload.title, brief=ref.instructions,
                       source="manual", reference_id=ref.id)])])
        origin = request.headers.get("x-creatoros-origin", "web")
        try:
            # Match the existing production-task lock order and queue authority.
            # The dispatcher cannot observe the authorization until commit.
            with executor._lock:
                executor.start()
                if executor._readiness_error():
                    raise ContentRunError("生产队列暂不可用，请核对后重试。", code="executor_unavailable")
                with runs._write_lock, database.session() as session:
                    if database.engine.dialect.name == "sqlite":
                        session.execute(text("BEGIN IMMEDIATE"))
                    series = session.get(Series, series_id)
                    if series is None or not series.is_active or not series.creator_id or not series.creator or not series.creator.is_active:
                        raise ContentRunError("账号或栏目未启用。", code="scope_conflict", status_code=403)
                    receipt = session.get(WriteReceipt, payload.request_id)
                    selection = None
                    if receipt is not None:
                        saved = receipt.response_json
                        recorded = saved.get("reference_production")
                        if ((recorded.get("skill_ids") if recorded else None) != payload.skill_ids
                                or saved.get("creator_id", series.creator_id) != series.creator_id):
                            raise ContentRunError("请求标识已用于不同的 Skill 选择或账号。", code="idempotency_conflict")
                        existing = session.scalar(select(ContentRun).where(ContentRun.idempotency_key == f"content:{topic_id}"))
                        if recorded and (existing is None or recorded.get("run_id") != existing.id):
                            raise ContentRunError("已接收的制作记录不可用，请核对原任务。", code="idempotency_conflict")
                        if existing is not None:
                            if existing.input_snapshot_json.get("creator_id") != series.creator_id:
                                raise ContentRunError("冻结任务与当前账号不一致。", code="scope_conflict", status_code=403)
                            spec = existing.input_snapshot_json.get("execution_spec")
                            selection = TaskExecutionSpec.model_validate(spec) if spec else None
                    elif payload.skill_ids is not None:
                        selection = resolve_task_skill_ids(request.app.state.skill_library, series.creator_id,
                                                           payload.skill_ids, kind="image")
                    else:
                        resolve_image_skills(database, session, series, protocol=runs.production_protocol)
                    writes.pending_operations.execute_direct(
                        "按参考作品与用户要求直接制作，不生成候选。", plan, scope_series_id=series_id,
                        request_id=payload.request_id, origin=origin if origin in {"web", "agent", "cli"} else "web",
                        bound_session=session)
                    run = runs._create(topic_id, idempotency_key=f"content:{topic_id}", bound_session=session,
                        execution_spec=selection, origin_session_id=request.headers.get("x-creatoros-agent-session"))
                    if receipt is None:
                        receipt = session.get(WriteReceipt, payload.request_id)
                        receipt.response_json = {**receipt.response_json, "reference_production": {
                            "skill_ids": payload.skill_ids, "run_id": run.id}}
                    first = session.scalar(select(ContentRevision).where(ContentRevision.content_run_id == run.id,
                        ContentRevision.revision_number == 1))
                    attempted = session.scalar(select(ContentAttempt.id).where(ContentAttempt.revision_id == first.id).limit(1))
                    if run.status.value == "queued" and run.active_revision_number == 1 and not attempted:
                        executor.queue.enqueue(run.id, run.version, bound_session=session)
                executor._wake.set()
        except PendingOperationError as error:
            raise HTTPException(409, str(error)) from error
        return queries.get_run(run.id)

    return router
