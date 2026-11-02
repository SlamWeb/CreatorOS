"""Shared Web/Agent inputs; reference works never enter the shared Skill library."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import Field
from creatoros.integrations.topic_references import TopicReferenceStore
from creatoros.storage import ContentRepository
from creatoros.runs import ReferenceProductionInput
from .schemas import WriteRequest, RunDetail
from .chat_attachments import MAX_ENCODED


class ReferenceImageRequest(WriteRequest):
    name: str = Field(min_length=1, max_length=255)
    data_base64: str = Field(min_length=1, max_length=MAX_ENCODED)


class ReferenceInputRequest(WriteRequest):
    image_ids: list[str] = Field(min_length=1, max_length=20)
    instructions: str = Field(default="", max_length=3000)
    reference_presentation: bool = False


class ReferenceProductionRequest(ReferenceProductionInput):
    pass


def reference_routes(database, production, queries):
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
        return series

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
        series = require_series(series_id, production=True)
        run = production.submit(series_id, payload, creator_id=series.creator_id,
                                origin=request.headers.get("x-creatoros-origin", "web"),
                                origin_session_id=request.headers.get("x-creatoros-agent-session"))
        return queries.get_run(run.id)

    return router
