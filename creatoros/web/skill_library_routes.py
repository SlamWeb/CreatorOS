"""Explicit account library and series pool HTTP boundary."""
from typing import Literal
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from creatoros.integrations.skill_library import SkillLibraryConflict


class LibraryAddRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    skill_id: str = Field(min_length=1, max_length=120)
    request_id: str = Field(min_length=8, max_length=64)


class DefaultSkill(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    skill_id: str = Field(min_length=1, max_length=120)
    role: Literal["primary", "supporting"]


class StageDefaults(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    image: list[DefaultSkill] = Field(max_length=20)
    video: list[DefaultSkill] = Field(max_length=20)


class SeriesSkillsRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    expected_version: int = Field(ge=0)
    skill_ids: list[str] = Field(max_length=100)
    defaults: StageDefaults


def create_skill_library_router(service):
    router = APIRouter(prefix="/api")

    def respond(action):
        try:
            return JSONResponse(action(), headers={"Cache-Control": "no-store"})
        except SkillLibraryConflict as error:
            return JSONResponse(status_code=409, content={"error": {
                "code": "skill_library_conflict", "message": str(error)}})
        except PermissionError as error:
            raise HTTPException(403, str(error)) from error
        except LookupError as error:
            raise HTTPException(404, str(error)) from error
        except (ValueError, OSError) as error:
            raise HTTPException(422, str(error)) from error

    @router.get("/creators/{creator_id}/skills")
    def get_creator_skills(creator_id: str):
        return respond(lambda: service.get_creator(creator_id))

    @router.post("/creators/{creator_id}/skills")
    def add_creator_skill(creator_id: str, payload: LibraryAddRequest):
        return respond(lambda: service.add(creator_id, payload.skill_id, payload.request_id))

    @router.delete("/creators/{creator_id}/skills/{skill_id}")
    def remove_creator_skill(creator_id: str, skill_id: str, expected_version: int = Query(ge=0)):
        return respond(lambda: service.remove(creator_id, skill_id, expected_version))

    @router.get("/series/{series_id}/skills")
    def get_series_skills(series_id: str):
        return respond(lambda: service.get_series(series_id))

    @router.put("/series/{series_id}/skills")
    def set_series_skills(series_id: str, payload: SeriesSkillsRequest):
        return respond(lambda: service.set_series(series_id, payload.expected_version,
                                                  payload.skill_ids, payload.defaults.model_dump()))

    return router
