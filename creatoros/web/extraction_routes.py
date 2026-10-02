"""Web and Agent share the same explicit extraction/registration boundary."""
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import Field

from ..integrations.skill_extraction import ExtractionError, MAX_IMAGE
from .schemas import WriteRequest


class Upload(WriteRequest):
    name: str = Field(min_length=1, max_length=255)
    data_base64: str = Field(min_length=1, max_length=((MAX_IMAGE + 2) // 3) * 4)


class Extract(WriteRequest):
    request_id: str = Field(min_length=1, max_length=128)
    upload_ids: list[str] = Field(min_length=1, max_length=6)
    mode: Literal["pair", "mind", "visual", "single"] = "pair"
    instruction: str = Field(default="", max_length=4000)


class Save(WriteRequest):
    expected_digest: str = Field(min_length=64, max_length=64)


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

    @router.post("/{job_id}/save")
    def save(job_id: str, request: Save):
        return call(lambda: service.save(job_id, request.expected_digest))

    @router.post("/{job_id}/cancel")
    def cancel(job_id: str, request: WriteRequest):
        return call(lambda: service.cancel(job_id))

    return router
