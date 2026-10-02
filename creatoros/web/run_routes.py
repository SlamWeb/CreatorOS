from typing import Annotated
from io import BytesIO
from pathlib import Path as FilePath
from zipfile import ZipFile, ZIP_DEFLATED

from fastapi import APIRouter, Header, Path, Query, Request, Response
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse

from creatoros.runs import ContentRunError
from creatoros.runs.artifacts import validate_artifact
from creatoros.storage import ContentRun, ContentRunStatus
from .events import StudioEvents
from .schemas import (
    EventBatch, ManualPublicationRequest, PublicationMetricRequest,
    RunApproveRequest, RunDetail, RunRevisionRequest,
)


def review_routes(runs, queries, artifacts, publications) -> APIRouter:
    router = APIRouter(prefix="/api/runs")
    events = StudioEvents(runs.database)

    @router.get("/{run_id}/revisions/{revision_id}/cards/{order}")
    def image(run_id: str, revision_id: str, order: Annotated[int, Path(ge=1)],
              digest: Annotated[str, Query(pattern=r"^[a-f0-9]{64}$")],
              checksum: Annotated[str, Query(pattern=r"^[a-f0-9]{64}$")]):
        raw, mime = artifacts.image(run_id, revision_id, order, digest=digest, checksum=checksum)
        return Response(raw, media_type=mime, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @router.get("/{run_id}/partial-cards/{order}")
    def partial_image(run_id: str, order: Annotated[int, Path(ge=1)],
                      checksum: Annotated[str, Query(pattern=r"^[a-f0-9]{64}$")]):
        raw, mime = artifacts.partial_image(run_id, order, checksum=checksum)
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

    @router.post("/{run_id}/publication", response_model=RunDetail)
    def record_publication(run_id: str, payload: ManualPublicationRequest):
        publications.record(run_id, **payload.model_dump())
        return queries.get_run(run_id)

    @router.post("/{run_id}/publication/metrics", response_model=RunDetail, status_code=201)
    def add_publication_metrics(run_id: str, payload: PublicationMetricRequest):
        publications.add_metrics(run_id, **payload.model_dump())
        return queries.get_run(run_id)

    @router.get("/{run_id}/download")
    def download_approved(run_id: str):
        with runs.database.session() as session:
            run = session.get(ContentRun, run_id)
            if run is None:
                raise ContentRunError("Run 不存在。", code="not_found", status_code=404)
            if run.status is not ContentRunStatus.APPROVED or not run.approved_revision_id:
                raise ContentRunError("只有已批准的产物可以下载。")
            revision_id, digest = run.approved_revision_id, run.approved_artifact_digest
        try:
            root, recorded, data, _ = artifacts.locate(run_id, revision_id)
            if recorded != digest:
                raise ValueError("批准摘要与产物版本不一致。")
            pack = artifacts.pack(root, data)
            checked = validate_artifact(root, composition=data.composition,
                                        production_protocol=data.production_protocol)
            if checked.artifact_digest != digest:
                raise ValueError("产物已变化。")
            buffer = BytesIO()
            with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
                for card in pack.cards:
                    source = (root / card.image_path).resolve()
                    if not source.is_relative_to(root):
                        raise ValueError("图片路径越过产物目录。")
                    archive.writestr(f"images/{card.order:02d}{FilePath(card.image_path).suffix}", source.read_bytes())
                copy = pack.publish_copy
                archive.writestr("publish_copy.txt", f"{copy.title}\n\n{copy.body}\n\n{' '.join(copy.hashtags)}\n")
            return Response(buffer.getvalue(), media_type="application/zip", headers={
                "Content-Disposition": f'attachment; filename="creatoros-{run_id}.zip"',
                "Cache-Control": "no-store",
            })
        except (OSError, ValueError) as error:
            raise ContentRunError("批准产物缺失或已变化，请先核对图片。", code="artifact_changed") from error

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
