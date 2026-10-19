"""Local review UI boundary; Agent tools do not auto-apply calibration drafts."""
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from pydantic import Field

from ..integrations.skill_extraction import ExtractionError
from .schemas import WriteRequest


class ArtifactLabel(WriteRequest):
    run_id: str = Field(min_length=1, max_length=36)
    revision_id: str = Field(min_length=1, max_length=36)
    artifact_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    order: int | None = Field(default=None, ge=1)
    rating: Literal["good", "bad"]
    comment: str = Field(min_length=1, max_length=2000)


class ReferenceLabel(WriteRequest):
    upload_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    rating: Literal["good", "bad"]
    comment: str = Field(min_length=1, max_length=2000)


class CreateCalibration(WriteRequest):
    request_id: str = Field(min_length=1, max_length=128)
    series_id: str = Field(min_length=1, max_length=80)
    skill_id: str = Field(min_length=1, max_length=120)
    expected_skill_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifacts: list[ArtifactLabel] = Field(default_factory=list, max_length=40)
    references: list[ReferenceLabel] = Field(default_factory=list, max_length=20)
    instruction: str = Field(default="", max_length=4000)


class ApplyCalibration(WriteRequest):
    request_id: str = Field(min_length=1, max_length=128)
    expected_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_skill_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    trial_id: str = Field(pattern=r"^[a-f0-9]{64}$")


def calibration_routes(service):
    router = APIRouter(prefix="/api/skill-calibrations")

    def call(action):
        try:
            return JSONResponse(jsonable_encoder(action()), headers={"Cache-Control": "no-store"})
        except ExtractionError as error:
            raise HTTPException(error.status, str(error)) from error
        except (OSError, ValueError, KeyError) as error:
            raise HTTPException(409, "校准文件或版本无法核验，未覆盖共享 Skill；请刷新检查。") from error

    @router.post("", status_code=202)
    def create(body: CreateCalibration):
        result = call(lambda: service.create(**body.model_dump()))
        result.status_code = 202
        return result

    @router.get("")
    def list_calibrations(series_id: str | None = None):
        return call(lambda: {"items": service.list(series_id)})

    @router.get("/{identifier}")
    def get(identifier: str):
        return call(lambda: service.get(identifier))

    @router.post("/{identifier}/apply")
    def apply(identifier: str, body: ApplyCalibration):
        return call(lambda: service.apply(identifier, **body.model_dump()))

    @router.get("/{identifier}/evidence/{image_index}")
    def evidence_image(identifier: str, image_index: int):
        try:
            return FileResponse(service.evidence_image(identifier, image_index),
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
        except ExtractionError as error:
            raise HTTPException(error.status, str(error)) from error
        except (OSError, ValueError, KeyError) as error:
            raise HTTPException(409, "校准参考图无法核验。") from error

    return router
