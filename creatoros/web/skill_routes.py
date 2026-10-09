"""Skill installation and explicit compare-and-set column binding."""
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import update
from typing import Literal

from creatoros.storage import Series
from creatoros.integrations.producer_skills import SkillDigestConflict
from .schemas import WriteRequest


class InstallSkillRequest(WriteRequest):
    github_url: str = Field(min_length=1, max_length=1000)
    retry: bool = False
    role: Literal["mind", "production", "legacy_end_to_end"] | None = None


class BindSkillRequest(WriteRequest):
    skill_id: str = Field(min_length=1, max_length=4096)
    expected_skill_name: str = Field(min_length=1, max_length=120)


class UpdateSkillFileRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    path: str = Field(min_length=1, max_length=512)
    content: str = Field(max_length=512 * 1024)
    expected_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


def skill_routes(database, service):
    router = APIRouter(prefix="/api")

    @router.get("/producer-skills")
    def list_skills():
        return {"items": service.catalog.list()}

    @router.get("/producer-skills/{skill_id}/content")
    def get_skill_content(skill_id: str, offset: int = Query(default=0, ge=0),
                          limit: int = Query(default=2000, ge=1, le=4000)):
        try:
            return service.catalog.read_skill_page(skill_id, offset=offset, limit=limit)
        except (ValueError, OSError, UnicodeError, KeyError) as error:
            raise HTTPException(status_code=404, detail="Skill 正文不可用或未登记。") from error

    @router.get("/producer-skills/{skill_id}/files")
    def list_skill_files(skill_id: str):
        try:
            return JSONResponse(service.catalog.list_skill_files(skill_id),
                                headers={"Cache-Control": "no-store"})
        except (ValueError, OSError, UnicodeError, KeyError) as error:
            raise HTTPException(status_code=404, detail="Skill 文件不可用或未登记。") from error

    @router.get("/producer-skills/{skill_id}/files/content")
    def get_skill_file(skill_id: str, path: str = Query(min_length=1, max_length=512)):
        try:
            result = service.catalog.read_skill_file(skill_id, path)
        except TypeError as error:
            raise HTTPException(status_code=415, detail=str(error)) from error
        except OverflowError as error:
            raise HTTPException(status_code=413, detail=str(error)) from error
        except (ValueError, OSError, UnicodeError, KeyError) as error:
            raise HTTPException(status_code=404, detail="Skill 文件不可用或路径无效。") from error
        if result["kind"] == "image":
            return Response(content=result["content"], media_type=result["media_type"],
                            headers={"Cache-Control": "no-store",
                                     "X-Skill-Digest": result["digest"],
                                     "X-Skill-Editable": str(result["editable"]).lower()})
        return JSONResponse({"path": result["path"], "kind": result["kind"],
                             "digest": result["digest"], "editable": result["editable"],
                             "content": result["content"]},
                            headers={"Cache-Control": "no-store"})

    @router.get("/producer-skills/{skill_id}/files/text")
    def get_skill_text_file(skill_id: str, path: str = Query(min_length=1, max_length=512)):
        try:
            result = service.catalog.read_skill_file(skill_id, path, text_only=True)
        except TypeError as error:
            return JSONResponse(status_code=415, content={"error": {
                "code": "skill_text_only", "message": str(error),
            }}, headers={"Cache-Control": "no-store"})
        except OverflowError as error:
            raise HTTPException(status_code=413, detail=str(error)) from error
        except (ValueError, OSError, UnicodeError, KeyError) as error:
            raise HTTPException(status_code=404, detail="Skill 文件不可用或路径无效。") from error
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.put("/producer-skills/{skill_id}/files/content")
    def update_skill_file(skill_id: str, request: UpdateSkillFileRequest):
        try:
            return JSONResponse(service.catalog.update_skill_file(
                skill_id, request.path, request.content, request.expected_digest,
            ), headers={"Cache-Control": "no-store"})
        except SkillDigestConflict as error:
            return JSONResponse(status_code=409, content={"error": {
                "code": "skill_digest_conflict", "message": str(error),
                "current_digest": error.current_digest,
            }})
        except OverflowError as error:
            raise HTTPException(status_code=413, detail=str(error)) from error
        except TypeError as error:
            raise HTTPException(status_code=415, detail=str(error)) from error
        except UnicodeError as error:
            raise HTTPException(status_code=422, detail="Skill 内容必须是有效 UTF-8 文本。") from error
        except (ValueError, OSError, KeyError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.post("/producer-skills/install", status_code=202)
    def install_skill(request: InstallSkillRequest):
        return service.submit(request.github_url, retry=request.retry, role=request.role)

    @router.get("/producer-skills/jobs/{job_id}")
    def get_job(job_id: str):
        return service.get(job_id)

    @router.post("/series/{series_id}/skill")
    def bind_skill(series_id: str, request: BindSkillRequest):
        service.catalog.resolve(request.skill_id)
        binding = (request.skill_id if request.skill_id == service.catalog.BUILTIN_ID
                   else service.catalog.describe(request.skill_id)["id"])
        with database.session() as session:
            result = session.execute(update(Series).where(
                Series.id == series_id, Series.skill_name == request.expected_skill_name,
            ).values(skill_name=binding))
            if result.rowcount != 1:
                raise HTTPException(409, "栏目不存在或绑定已经改变，请刷新并重新确认。")
        return {"series_id": series_id, "skill_name": binding,
                "message": "已绑定；只影响新建 Run，已有任务保留原 Skill。"}

    return router
