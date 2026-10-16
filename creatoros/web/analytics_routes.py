"""Account feedback: persisted read models and explicit collection controls."""
from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict


class FeedbackSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool


def analytics_routes(service) -> APIRouter:
    router = APIRouter()

    @router.get("/api/creators/{creator_id}/analytics")
    def analytics(creator_id: str, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return service.view(creator_id)

    @router.put("/api/creators/{creator_id}/analytics/settings")
    async def settings(creator_id: str, payload: FeedbackSettings):
        return service.set_enabled(creator_id, payload.enabled)

    @router.post("/api/creators/{creator_id}/analytics/refresh")
    async def refresh(creator_id: str):
        return service.refresh(creator_id)

    return router
