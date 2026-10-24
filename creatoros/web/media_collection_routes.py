"""Web-only collection management; these endpoints never submit production."""
from fastapi import APIRouter, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import Field

from .schemas import WriteRequest


class CreateCollection(WriteRequest):
    name: str = Field(min_length=1, max_length=120)
    topic_id: str | None = Field(default=None, min_length=1, max_length=80)
    run_ids: list[str] = Field(default_factory=list, max_length=500)
    request_id: str = Field(min_length=1, max_length=128)


class PatchCollection(WriteRequest):
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    run_ids: list[str] | None = Field(default=None, max_length=500)
    order_ids: list[str] | None = Field(default=None, max_length=10000)
    excluded_ids: list[str] | None = Field(default=None, max_length=10000)


class SortCollection(WriteRequest):
    expected_version: int = Field(ge=1)
    snapshot_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


def media_collection_routes(service):
    router = APIRouter(prefix="/api/creators/{creator_id}/collections")

    def response(value, status=200):
        return JSONResponse(jsonable_encoder(value), status_code=status,
                            headers={"Cache-Control": "no-store"})

    @router.get("")
    def list_collections(creator_id: str):
        return response(dict(items=service.list(creator_id)))

    @router.get("/sources")
    def sources(creator_id: str, topic_id: str | None = None):
        return response(dict(items=service.sources(creator_id, topic_id)))

    @router.post("", status_code=201)
    def create(creator_id: str, body: CreateCollection):
        return response(service.create(creator_id, **body.model_dump()), 201)

    @router.get("/{identifier}")
    def get(creator_id: str, identifier: str):
        return response(service.get(creator_id, identifier))

    @router.patch("/{identifier}")
    def patch(creator_id: str, identifier: str, body: PatchCollection):
        return response(service.patch(creator_id, identifier, **body.model_dump(exclude_unset=True)))

    @router.delete("/{identifier}")
    def delete(creator_id: str, identifier: str, expected_version: int = Query(ge=1)):
        return response(service.delete(creator_id, identifier, expected_version=expected_version))

    @router.post("/{identifier}/sort")
    def sort(creator_id: str, identifier: str, body: SortCollection):
        return response(service.sort(creator_id, identifier, **body.model_dump()))

    @router.get("/{identifier}/items/{run_id}/{revision_id}/{order}")
    def image(creator_id: str, identifier: str, run_id: str, revision_id: str, order: int,
              checksum: str = Query(pattern=r"^[a-f0-9]{64}$")):
        raw, mime = service.image(creator_id, identifier, run_id, revision_id, order, checksum=checksum)
        return Response(raw, media_type=mime, headers={"Cache-Control": "no-store",
                                                      "X-Content-Type-Options": "nosniff"})

    @router.get("/{identifier}/download")
    def download(creator_id: str, identifier: str, version: int = Query(ge=1),
                 snapshot: str = Query(pattern=r"^[a-f0-9]{64}$")):
        archive = service.download(creator_id, identifier, version=version, snapshot=snapshot)

        def chunks():
            try:
                while chunk := archive.read(64 * 1024):
                    yield chunk
            finally:
                archive.close()

        return StreamingResponse(chunks(), media_type="application/zip", headers={
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="collection-{identifier}.zip"'})

    return router
