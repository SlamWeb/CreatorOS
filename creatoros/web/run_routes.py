from typing import Annotated

from fastapi import APIRouter, Header, Path, Query, Request, Response
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse

from creatoros.runs import ContentRunError
from .events import StudioEvents
from .schemas import EventBatch, RunApproveRequest, RunDetail, RunRevisionRequest


def review_routes(runs, queries, artifacts) -> APIRouter:
    router = APIRouter(prefix="/api/runs")
    events = StudioEvents(runs.database)

    @router.get("/{run_id}/revisions/{revision_id}/cards/{order}")
    def image(run_id: str, revision_id: str, order: Annotated[int, Path(ge=1)],
              digest: Annotated[str, Query(pattern=r"^[a-f0-9]{64}$")],
              checksum: Annotated[str, Query(pattern=r"^[a-f0-9]{64}$")]):
        raw, mime = artifacts.image(run_id, revision_id, order, digest=digest, checksum=checksum)
        return Response(raw, media_type=mime, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @router.post("/{run_id}/approve", response_model=RunDetail)
    def approve(run_id: str, payload: RunApproveRequest):
        try:
            artifacts.locate(run_id, payload.revision_id)
            runs.approve(run_id, **payload.model_dump())
        except ContentRunError:
            raise
        except (OSError, ValueError) as error:
            raise ContentRunError("产物缺失、损坏或已变化，请重新检查或返工。", code="artifact_changed") from error
        return queries.get_run(run_id)

    @router.post("/{run_id}/revisions", response_model=RunDetail, status_code=201)
    def revise(run_id: str, payload: RunRevisionRequest):
        runs.request_revision(run_id, payload.instruction, expected_version=payload.expected_version)
        return queries.get_run(run_id)

    @router.get("/{run_id}/events", response_model=EventBatch)
    def list_events(run_id: str, after_id: Annotated[int, Query(ge=0)] = 0,
                    limit: Annotated[int, Query(ge=1, le=100)] = 100):
        snapshot = events.snapshot(run_id)
        _check_cursor(after_id, snapshot)
        return events.batch(run_id, after_id, limit)

    @router.get("/{run_id}/events/stream")
    async def stream(request: Request, run_id: str, after_id: Annotated[int, Query(ge=0)] = 0,
                     last_event_id: Annotated[int | None, Header(ge=0, alias="Last-Event-ID")] = None):
        snapshot = await run_in_threadpool(events.snapshot, run_id)
        cursor = last_event_id if last_event_id is not None else after_id
        _check_cursor(cursor, snapshot)
        return StreamingResponse(events.stream(request, run_id, cursor, snapshot), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return router


def _check_cursor(cursor: int, snapshot: dict):
    if cursor > snapshot["latest_event_id"]:
        raise ContentRunError("事件游标超出当前记录，请重新打开运行详情。", code="invalid_cursor")
