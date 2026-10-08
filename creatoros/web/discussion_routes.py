from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field


class DiscussionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    request_id: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    revision_id: str = Field(min_length=1, max_length=80)
    artifact_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    message: str = Field(min_length=1, max_length=10_000)


def discussion_routes(discussions):
    router = APIRouter(prefix="/api/runs")

    @router.get("/{run_id}/discussion")
    def get(run_id: str):
        return discussions.get(run_id)

    @router.post("/{run_id}/discussion", status_code=202)
    def submit(run_id: str, request: DiscussionRequest):
        return discussions.submit(run_id, **request.model_dump())

    return router
