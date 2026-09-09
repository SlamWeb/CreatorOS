"""Research is asynchronous; selecting candidates only creates an approval preview."""
from fastapi import APIRouter
from pydantic import Field

from creatoros.integrations.topic_research import CandidateSelection
from .schemas import WriteRequest


class ResearchRequest(WriteRequest):
    count: int = Field(default=10, ge=1, le=30)
    instructions: str = Field(default="", max_length=3000)


class SelectionRequest(WriteRequest):
    selections: list[CandidateSelection] = Field(min_length=1, max_length=30)


def research_routes(service, queries):
    router = APIRouter(prefix="/api")

    @router.post("/series/{series_id}/topic-research", status_code=202)
    def research(series_id: str, request: ResearchRequest):
        return service.submit(series_id, request.count, request.instructions)

    @router.get("/series/{series_id}/topic-research")
    def batches(series_id: str):
        return {"items": service.list(series_id)}

    @router.get("/topic-research/{batch_id}")
    def batch(batch_id: str):
        return service.get(batch_id)

    @router.post("/topic-research/{batch_id}/preview")
    def select(batch_id: str, request: SelectionRequest):
        pending = service.prepare(batch_id, request.selections)
        return queries.get_operation(pending.id)

    return router
