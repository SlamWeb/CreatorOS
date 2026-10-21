"""Persistent bounded Skill improvement tasks; no library activation endpoint."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from creatoros.integrations.skill_extraction import ExtractionError


class CreateSkillIterationRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    series_id: str = Field(min_length=1, max_length=120)
    skill_id: str = Field(min_length=1, max_length=160)
    topic_id: str = Field(min_length=1, max_length=120)
    instructions: str = Field(default="", max_length=4000)
    attachment_ids: list[str] = Field(default_factory=list, max_length=6)
    reference_paths: list[str] | None = Field(default=None, max_length=20)
    max_rounds: int = Field(default=3, ge=1, le=3)
    request_id: str = Field(min_length=1, max_length=128)


class SkillIterationActionRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    action: Literal["pause", "resume", "continue"]
    expected_revision: int = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=128)


def skill_iteration_routes(service, chat):
    router = APIRouter(prefix="/api/skill-iterations")

    def call(action, status=200):
        try:
            return JSONResponse(jsonable_encoder(action()), status_code=status, headers={"Cache-Control": "no-store"})
        except ExtractionError as error:
            raise HTTPException(error.status, str(error)) from error
        except PermissionError as error:
            raise HTTPException(403, str(error)) from error
        except (ValueError, KeyError, OSError) as error:
            raise HTTPException(422, str(error)) from error

    def session(request):
        sid = request.headers.get("x-creatoros-agent-session")
        if sid:
            chat.get(sid)
        return sid

    @router.get("")
    def listing(request: Request, creator_id: str | None = None, series_id: str | None = None,
                limit: int = Query(default=20, ge=1, le=100)):
        sid = session(request)
        doc = chat.get(sid) if sid else None
        if doc and doc.get("scope_kind") == "creator":
            creator_id = doc["creator_id"]
        return call(lambda: service.list(creator_id=creator_id, series_id=series_id, limit=limit))

    @router.post("", status_code=202)
    def create(payload: CreateSkillIterationRequest, request: Request):
        sid = session(request)
        # Attachments can only be resolved through a real bound conversation.
        if payload.attachment_ids and sid is None:
            raise HTTPException(422, "参考图需要从当前对话提交。")
        return call(lambda: service.create(**payload.model_dump(), origin_session_id=sid), status=202)

    @router.get("/{iteration_id}")
    def get(iteration_id: str):
        return call(lambda: service.get(iteration_id))

    @router.post("/{iteration_id}/actions")
    def action(iteration_id: str, payload: SkillIterationActionRequest):
        return call(lambda: service.action(iteration_id, **payload.model_dump()))

    @router.get("/{iteration_id}/files/{key}")
    def file(iteration_id: str, key: str):
        try:
            path = service.file(iteration_id, key)
            return FileResponse(path, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
        except ExtractionError as error:
            raise HTTPException(error.status, str(error)) from error
        except (ValueError, OSError, KeyError, PermissionError) as error:
            raise HTTPException(404, "校准文件不存在或不可读取。") from error

    return router
