"""Web and Agent share the same explicit extraction/registration boundary."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ConfigDict, Field, model_validator
from sqlalchemy.exc import SQLAlchemyError

from ..integrations.skill_extraction import DraftSkill, ExtractionError, MAX_IMAGE, MAX_REFERENCE_IMAGES
from ..storage.skill_display_categories import initialize_display_categories
from .schemas import WriteRequest
from .skill_display_categories import CategoryList, is_local_global_web, require_local_global_web


class Upload(WriteRequest):
    name: str = Field(min_length=1, max_length=255)
    data_base64: str = Field(min_length=1, max_length=((MAX_IMAGE + 2) // 3) * 4)


class Extract(WriteRequest):
    request_id: str = Field(min_length=1, max_length=128)
    upload_ids: list[str] = Field(default_factory=list, max_length=MAX_REFERENCE_IMAGES)
    source_text: str = Field(default="", max_length=20000)
    mode: Literal["pair", "mind", "visual", "single"] = "single"
    instruction: str = Field(default="", max_length=4000)
    categories: CategoryList = Field(default_factory=list)


class Author(WriteRequest):
    # Unlike normal form fields, authored prompt whitespace is user content.
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=False)
    request_id: str = Field(min_length=1, max_length=128)
    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)
    description: str = Field(min_length=1, max_length=1000)
    content: str = Field(min_length=1, max_length=20000)
    role: Literal["mind", "production", "legacy_end_to_end"] = "legacy_end_to_end"
    upload_ids: list[str] = Field(default_factory=list, max_length=MAX_REFERENCE_IMAGES)
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    categories: CategoryList = Field(default_factory=list)


class Merge(WriteRequest):
    request_id: str = Field(min_length=1, max_length=128)
    skill_ids: list[str] = Field(min_length=2, max_length=8)
    instruction: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def unique_skill_ids(self):
        if (len(set(self.skill_ids)) != len(self.skill_ids)
                or any(not item or len(item) > 120 for item in self.skill_ids)):
            raise ValueError("请选择不同的 Skill。")
        return self


class Save(WriteRequest):
    expected_digest: str = Field(min_length=64, max_length=64)


class Edit(Save):
    skills: list[DraftSkill] = Field(min_length=1, max_length=2)


class Revise(Save):
    request_id: str = Field(min_length=1, max_length=128)
    instruction: str = Field(min_length=1, max_length=4000)


class Trial(Save):
    request_id: str = Field(min_length=1, max_length=128)
    topic: str = Field(min_length=1, max_length=4000)


def extraction_routes(service, database=None):
    router = APIRouter(prefix="/api/skill-extractions")

    def call(action):
        try:
            return action()
        except ExtractionError as error:
            raise HTTPException(error.status, str(error)) from error

    def call_job(request, action):
        job = call(action)
        return service.with_category_intent(job) if is_local_global_web(request) else job

    def validate_category_intent(payload, request):
        if "categories" in payload.model_fields_set:
            require_local_global_web(request)
            if payload.categories and database is None:
                raise HTTPException(503, "Skill 分类存储不可用。")

    def initialize_categories(family_id, categories):
        try:
            initialize_display_categories(database, family_id, categories)
        except SQLAlchemyError as error:
            raise ExtractionError("暂未完成 Skill 分类保存，请重试同一草稿的入库操作。", 503) from error

    @router.post("/uploads", status_code=201)
    def upload(request: Upload):
        return call(lambda: service.upload(request.name, request.data_base64))

    @router.get("/uploads/{upload_id}")
    def image(upload_id: str):
        return FileResponse(call(lambda: service.image_path(upload_id)),
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @router.get("")
    def list_jobs(request: Request):
        return {"items": service.list(include_categories=is_local_global_web(request))}

    @router.post("", status_code=202)
    def extract(payload: Extract, request: Request):
        validate_category_intent(payload, request)
        return call_job(request, lambda: service.submit(**payload.model_dump()))

    @router.post("/merge", status_code=202)
    def merge(request: Merge):
        return call(lambda: service.submit_merge(**request.model_dump()))

    @router.post("/author", status_code=201)
    def author(payload: Author, request: Request):
        validate_category_intent(payload, request)
        return call_job(request, lambda: service.author(**payload.model_dump()))

    @router.get("/{job_id}")
    def get(job_id: str, request: Request):
        return call(lambda: service.get(job_id, include_categories=is_local_global_web(request)))

    @router.get("/{job_id}/events")
    def events(job_id: str, before_id: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
        return JSONResponse(call(lambda: service.events(job_id, before_id, limit)), headers={"Cache-Control": "no-store"})

    @router.get("/{job_id}/events/{event_id}")
    def event(job_id: str, event_id: int, stream_id: str = "."):
        return JSONResponse(call(lambda: service.event(job_id, event_id, stream_id)), headers={"Cache-Control": "no-store"})

    @router.get("/{job_id}/files")
    def file(job_id: str, role: str, path: str):
        return FileResponse(call(lambda: service.file(job_id, role, path)),
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @router.post("/{job_id}/draft")
    def edit(job_id: str, payload: Edit, request: Request):
        return call_job(request, lambda: service.edit(job_id, payload.expected_digest, [s.model_dump() for s in payload.skills]))

    @router.post("/{job_id}/revise", status_code=202)
    def revise(job_id: str, payload: Revise, request: Request):
        return call_job(request, lambda: service.revise(job_id, **payload.model_dump()))

    @router.post("/{job_id}/trials", status_code=202)
    def trial(job_id: str, payload: Trial, request: Request):
        return call_job(request, lambda: service.trial(job_id, **payload.model_dump()))

    @router.get("/{job_id}/trials/{trial_id}/cards/{order}")
    def trial_image(job_id: str, trial_id: str, order: int):
        return FileResponse(call(lambda: service.trial_image(job_id, trial_id, order)),
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @router.post("/{job_id}/save")
    def save(job_id: str, payload: Save, request: Request):
        initializer = initialize_categories if database is not None and is_local_global_web(request) else None
        return call_job(request, lambda: service.save(job_id, payload.expected_digest, initialize_categories=initializer))

    @router.post("/{job_id}/cancel")
    def cancel(job_id: str, payload: WriteRequest, request: Request):
        return call_job(request, lambda: service.cancel(job_id))

    return router
