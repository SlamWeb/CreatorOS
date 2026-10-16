"""Publication preparation is an Agent action; consent is a Studio UI action."""
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field


class PublicationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str | None = Field(default=None, max_length=500)
    body: str | None = Field(default=None, max_length=10000)
    tags: list[str] | None = Field(default=None, max_length=10)
    visibility: Literal["公开可见", "仅自己可见", "仅互关好友可见"] = "公开可见"


class PublicationConsent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class PublicationReconcile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    post_url: str | None = Field(default=None, max_length=2000)


def publication_routes(service) -> APIRouter:
    router = APIRouter()

    @router.get("/api/runs/{run_id}/publication-request")
    def current(run_id: str, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return service.current(run_id)

    @router.post("/api/runs/{run_id}/publication-request")
    async def prepare(run_id: str, payload: PublicationDraft):
        return await service.prepare(run_id, **payload.model_dump())

    @router.get("/api/publication-requests/{request_id}")
    def get(request_id: str, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return service.get(request_id)

    @router.post("/api/publication-requests/{request_id}/confirm")
    async def confirm(request_id: str, payload: PublicationConsent, request: Request):
        # No model parameter can grant consent, including an overview Agent.
        # This local-only boundary is not authentication against hostile local code.
        origin = urlsplit(request.headers.get("origin", ""))
        if (request.headers.get("x-creatoros-agent-session")
                or request.headers.get("x-creatoros-origin") == "agent"
                or origin.scheme != "http"
                or origin.hostname not in {"localhost", "127.0.0.1"}
                or origin.netloc != request.url.netloc):
            raise HTTPException(403, "请在本机 Studio 发布预览中确认。")
        return await service.confirm(request_id, payload.expected_digest)

    @router.post("/api/publication-requests/{request_id}/reconcile")
    async def reconcile(request_id: str, payload: PublicationReconcile):
        return await service.reconcile(request_id, post_url=payload.post_url)

    @router.post("/api/publication-requests/{request_id}/metrics")
    async def metrics(request_id: str):
        return await service.refresh_metrics(request_id)

    return router
