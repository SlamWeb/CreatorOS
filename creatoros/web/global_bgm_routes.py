from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse
from pydantic import Field

from creatoros.integrations.managed_materials import MAX_BYTES, MAX_SHARE_TEXT
from .schemas import ApiModel


class BgmUpload(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    data_base64: str = Field(min_length=1, max_length=MAX_BYTES * 4 // 3 + 8)


class BgmImportRequest(ApiModel):
    source_url: str = Field(min_length=1, max_length=MAX_SHARE_TEXT)


class BgmAnalysisRequest(ApiModel):
    force: bool = False


def build_global_bgm_router(store):
    router = APIRouter(prefix="/api")

    @router.get("/bgm")
    def list_tracks(request: Request, q: str = Query("", max_length=200), archived: bool = False):
        controller = getattr(request.app.state, "maintenance", None)
        return {"items": store.list(q=q, archived=archived, sync_legacy=not (controller and controller.held))}

    @router.post("/bgm", status_code=201)
    def upload(body: BgmUpload):
        return store.upload(body.name, body.data_base64)

    @router.get("/bgm/imports")
    def list_imports():
        return {"items": store.list_imports()}

    @router.post("/bgm/imports", status_code=202)
    def start_import(body: BgmImportRequest):
        return store.start_import(body.source_url)

    @router.get("/bgm/imports/{identifier}")
    def import_status(identifier: str):
        return store.import_status(identifier)

    @router.post("/bgm/imports/{identifier}/retry", status_code=202)
    def retry_import(identifier: str):
        return store.retry_import(identifier)

    @router.get("/bgm/{identifier}")
    def get_track(identifier: str):
        return store.get(identifier)

    @router.get("/bgm/{identifier}/file")
    def preview(identifier: str):
        found = store.resolve(identifier)
        return FileResponse(found["path"], headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @router.get("/bgm/{identifier}/analysis")
    def analysis_status(identifier: str):
        return store.analysis_status(identifier)

    @router.post("/bgm/{identifier}/analysis/run", status_code=202)
    def start_analysis(identifier: str, body: BgmAnalysisRequest):
        return store.start_analysis(identifier, force=body.force)

    @router.get("/bgm/{identifier}/usage")
    def usage(identifier: str):
        return store.usage(identifier)

    @router.post("/bgm/{identifier}/archive")
    def archive(identifier: str):
        return store.archive(identifier)

    @router.post("/bgm/{identifier}/restore")
    def restore(identifier: str):
        return store.restore(identifier)

    @router.get("/series/{series_id}/bgm")
    def playlist(series_id: str):
        return {"items": store.list_series(series_id)}

    @router.put("/series/{series_id}/bgm/{identifier}")
    def add_to_playlist(series_id: str, identifier: str):
        return store.add_to_series(series_id, identifier)

    @router.delete("/series/{series_id}/bgm/{identifier}")
    def remove_from_playlist(series_id: str, identifier: str):
        return store.remove_from_series(series_id, identifier)

    return router
