"""Human review boundary for immutable Skill versions."""
from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..integrations.producer_skills import SkillDigestConflict
from ..integrations.skill_extraction import ExtractionError


class CandidateRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    expected_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_id: str = Field(min_length=1, max_length=128)


class VersionTrialRequest(CandidateRequest):
    topic: str = Field(min_length=1, max_length=4000)
    series_id: str | None = Field(default=None, min_length=1, max_length=80)


class PublishVersionRequest(CandidateRequest):
    trial_job_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    trial_id: str = Field(pattern=r"^[a-f0-9]{64}$")


class SwitchVersionRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    skill_id: str = Field(min_length=1, max_length=120)
    expected_revision: int = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=128)
    trial_job_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    trial_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


def skill_version_routes(service):
    router = APIRouter(prefix="/api")

    def call(action):
        try:
            return JSONResponse(jsonable_encoder(action()), headers={"Cache-Control": "no-store"})
        except SkillDigestConflict as error:
            return JSONResponse(status_code=409, content={"error": {
                "code": "skill_digest_conflict", "message": str(error), "current_digest": error.current_digest}})
        except ExtractionError as error:
            raise HTTPException(error.status, str(error)) from error
        except PermissionError as error:
            raise HTTPException(403, str(error)) from error
        except (ValueError, OSError, KeyError) as error:
            raise HTTPException(422, str(error)) from error

    @router.get("/producer-skills/{skill_id}/versions")
    def history(skill_id: str):
        return call(lambda: service.history(skill_id))

    @router.post("/producer-skills/{skill_id}/versions")
    def create(skill_id: str, request: CandidateRequest):
        return call(lambda: service.catalog.create_skill_candidate(skill_id, **request.model_dump()))

    @router.post("/producer-skills/{skill_id}/version-trials")
    def trial(skill_id: str, request: VersionTrialRequest):
        return call(lambda: service.trial(skill_id, **request.model_dump()))

    @router.post("/producer-skills/{skill_id}/publish-version")
    def publish(skill_id: str, request: PublishVersionRequest):
        return call(lambda: service.publish(skill_id, **request.model_dump()))

    @router.post("/series/{series_id}/skill-version")
    def switch(series_id: str, request: SwitchVersionRequest):
        return call(lambda: service.switch(series_id, **request.model_dump()))

    return router
