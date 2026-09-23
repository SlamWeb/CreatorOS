"""Research is asynchronous; selecting candidates only creates an approval preview."""
from fastapi import APIRouter, HTTPException, Query, Request
from typing import Literal
from pydantic import Field

from creatoros.integrations.topic_research import CandidateSelection
from creatoros.operations.service import PendingOperationError
from .schemas import WriteRequest
from .topic_library import topic_library


class ResearchRequest(WriteRequest):
    count: int = Field(default=10, ge=1, le=30)
    instructions: str = Field(default="", max_length=3000)


class SelectionRequest(WriteRequest):
    selections: list[CandidateSelection] = Field(min_length=1, max_length=30)


class QueueSelectionRequest(SelectionRequest):
    request_id: str = Field(min_length=8, max_length=64)


def research_routes(service, queries):
    router = APIRouter(prefix="/api")

    @router.get("/series/{series_id}/topic-library")
    def library(series_id: str, state: Literal["all", "pending", "queued"] = "all",
                offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=100)):
        result = topic_library(service, queries, series_id, state, offset, limit)
        if result is None:
            raise HTTPException(status_code=404, detail="Series 不存在。")
        return result

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

    @router.post("/topic-research/{batch_id}/queue", status_code=201)
    def queue(batch_id: str, request: QueueSelectionRequest, raw_request: Request):
        origin = raw_request.headers.get("x-creatoros-origin", "web")
        origin = origin if origin in {"web", "agent", "cli"} else "web"
        try:
            pending, deduplicated = service.queue(
                batch_id, request.selections, request_id=request.request_id, origin=origin)
        except PendingOperationError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {"request_id": request.request_id, "deduplicated": deduplicated,
                "operation_id": pending.id, "series_id": pending.scope_series_id,
                "message": "已直接入队并记录审计。"}

    return router
