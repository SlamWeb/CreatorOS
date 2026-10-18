"""Shared Web/Agent inputs; reference works never enter the shared Skill library."""
from hashlib import sha256
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import Field

from creatoros.integrations.topic_references import TopicReferenceStore
from creatoros.operations.models import AddTopicsOperation, OperationPlan, TopicDraft
from creatoros.operations.service import PendingOperationError
from creatoros.storage import ContentRepository
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
        if production:
            from creatoros.integrations.producer_skills import ProducerSkillCatalog, skills_root_for
            from creatoros.integrations.skill_pair import snapshot_pair
            catalog = ProducerSkillCatalog(skills_root_for(database))
            try:
                if series.skill_name:
                    catalog.resolve(series.skill_name)
                else:
                    snapshot_pair(catalog, series.mind_skill_id, series.production_skill_id, native=True)
            except (ValueError, OSError) as error:
                raise HTTPException(422, "栏目Skill暂不可用于生产，请先检查配置。") from error

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
            with runs._write_lock:
                writes.pending_operations.execute_direct(
                    "按参考作品与用户要求直接制作，不生成候选。", plan, scope_series_id=series_id,
                    request_id=payload.request_id, origin=origin if origin in {"web", "agent", "cli"} else "web")
                run = runs.create(topic_id, origin_session_id=request.headers.get("x-creatoros-agent-session"))
                detail = queries.get_run(run.id)
                first = detail.revisions[0]
                if run.status.value == "queued" and len(detail.revisions) == 1 and not first.attempts:
                    executor.submit(run.id, expected_version=run.version)
        except PendingOperationError as error:
            raise HTTPException(409, str(error)) from error
        return queries.get_run(run.id)

    return router
