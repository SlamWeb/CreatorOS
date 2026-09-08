"""Skill installation and explicit compare-and-set column binding."""
from fastapi import APIRouter, HTTPException
from pydantic import Field
from sqlalchemy import update

from creatoros.storage import Series
from .schemas import WriteRequest


class InstallSkillRequest(WriteRequest):
    github_url: str = Field(min_length=1, max_length=1000)
    retry: bool = False


class BindSkillRequest(WriteRequest):
    skill_id: str = Field(min_length=1, max_length=120)
    expected_skill_name: str = Field(min_length=1, max_length=120)


def skill_routes(database, service):
    router = APIRouter(prefix="/api")

    @router.get("/producer-skills")
    def list_skills():
        return {"items": service.catalog.list()}

    @router.post("/producer-skills/install", status_code=202)
    def install_skill(request: InstallSkillRequest):
        return service.submit(request.github_url, retry=request.retry)

    @router.get("/producer-skills/jobs/{job_id}")
    def get_job(job_id: str):
        return service.get(job_id)

    @router.post("/series/{series_id}/skill")
    def bind_skill(series_id: str, request: BindSkillRequest):
        service.catalog.resolve(request.skill_id)
        with database.session() as session:
            result = session.execute(update(Series).where(
                Series.id == series_id, Series.skill_name == request.expected_skill_name,
            ).values(skill_name=request.skill_id))
            if result.rowcount != 1:
                raise HTTPException(409, "栏目不存在或绑定已经改变，请刷新并重新确认。")
        return {"series_id": series_id, "skill_name": request.skill_id,
                "message": "已绑定；只影响新建 Run，已有任务保留原 Skill。"}

    return router
