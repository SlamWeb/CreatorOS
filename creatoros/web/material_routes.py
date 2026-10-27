from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import Field, model_validator
from .schemas import WriteRequest


class MaterialUpload(WriteRequest):
    name: str | None = Field(default=None, max_length=200)
    data_base64: str | None = None
    source_url: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def mode(self):
        if bool(self.source_url) == bool(self.data_base64) or (self.data_base64 and not self.name):
            raise ValueError("请选择上传文件或提供分享链接。")
        return self


def material_routes(store):
    router = APIRouter(prefix="/api/creators/{creator_id}/materials")

    @router.get("")
    def list_materials(creator_id: str):
        return {"items": store.list(creator_id)}

    @router.post("", status_code=201)
    def upload(creator_id: str, body: MaterialUpload):
        return (store.from_url(creator_id, body.source_url) if body.source_url else
                store.upload(creator_id, body.name, body.data_base64))

    @router.get("/{identifier}")
    def get(creator_id: str, identifier: str):
        return {key: value for key, value in store.resolve(creator_id, identifier).items() if key != "path"}

    @router.get("/{identifier}/file")
    def file(creator_id: str, identifier: str):
        value = store.resolve(creator_id, identifier)
        return FileResponse(value["path"], headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    return router
