"""Web and Agent share the same explicit extraction/registration boundary."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import Field

from ..integrations.skill_extraction import DraftSkill, ExtractionError, MAX_IMAGE
from .schemas import WriteRequest


class Upload(WriteRequest):
    name: str = Field(min_length=1, max_length=255)
    data_base64: str = Field(min_length=1, max_length=((MAX_IMAGE + 2) // 3) * 4)


class Extract(WriteRequest):
    request_id: str = Field(min_length=1, max_length=128)
    upload_ids: list[str] = Field(default_factory=list, max_length=6)
    source_text: str = Field(default="", max_length=20000)
    mode: Literal["pair", "mind", "visual", "single"] = "single"
    instruction: str = Field(default="", max_length=4000)


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


def extraction_routes(service):
    router = APIRouter(prefix="/api/skill-extractions")

    def call(action):
        try:
            return action()
        except ExtractionError as error:
            raise HTTPException(error.status, str(error)) from error

    @router.post("/uploads", status_code=201)
    def upload(request: Upload):
        return call(lambda: service.upload(request.name, request.data_base64))

    @router.get("/uploads/{upload_id}")
    def image(upload_id: str):
        return FileResponse(call(lambda: service.image_path(upload_id)),
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @router.get("")
    def list_jobs():
        return {"items": service.list()}

    @router.post("", status_code=202)
    def extract(request: Extract):
        return call(lambda: service.submit(**request.model_dump()))

    @router.get("/{job_id}")
    def get(job_id: str):
        return call(lambda: service.get(job_id))

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
    def edit(job_id: str, request: Edit):
        return call(lambda: service.edit(job_id, request.expected_digest, [s.model_dump() for s in request.skills]))

    @router.post("/{job_id}/revise", status_code=202)
    def revise(job_id: str, request: Revise):
        return call(lambda: service.revise(job_id, **request.model_dump()))

    @router.post("/{job_id}/trials", status_code=202)
    def trial(job_id: str, request: Trial):
        return call(lambda: service.trial(job_id, **request.model_dump()))

    @router.get("/{job_id}/trials/{trial_id}/cards/{order}")
    def trial_image(job_id: str, trial_id: str, order: int):
        return FileResponse(call(lambda: service.trial_image(job_id, trial_id, order)),
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @router.post("/{job_id}/save")
    def save(job_id: str, request: Save):
        return call(lambda: service.save(job_id, request.expected_digest))

    @router.post("/{job_id}/cancel")
    def cancel(job_id: str, request: WriteRequest):
        return call(lambda: service.cancel(job_id))

    return router
