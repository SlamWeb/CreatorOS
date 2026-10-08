"""Read-only, on-demand Observation API."""
from fastapi import APIRouter, Query, Response


def observation_routes(service):
    router = APIRouter(prefix="/api/observation")

    @router.get("/tree")
    def tree(response: Response, parent: str | None = Query(None, max_length=4096),
             offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100)):
        response.headers["Cache-Control"] = "no-store"
        return service.tree(parent, offset, limit)

    @router.get("/detail")
    def detail(response: Response, node_id: str = Query(..., min_length=1, max_length=4096)):
        response.headers["Cache-Control"] = "no-store"
        return service.detail(node_id)

    return router
