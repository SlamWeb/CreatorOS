from __future__ import annotations

import shutil
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError

from creatoros.config import DATABASE_URL
from creatoros.storage import ContentRunStatus, Database

from .queries import StudioQueryService
from .schemas import (
    CreatorView,
    ErrorResponse,
    HealthView,
    OverviewView,
    PageResponse,
    PendingOperationView,
    RunDetail,
    RunSummary,
    SeriesView,
    TopicView,
)


def create_app(
    database_url: str | None = None,
    *,
    database: Database | None = None,
) -> FastAPI:
    """Create the read-only Studio API without running migrations or workers."""
    owns_database = database is None
    db = database or Database(database_url or DATABASE_URL)
    queries = StudioQueryService(db)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        if owns_database:
            db.close()

    app = FastAPI(
        title="CreatorOS Studio API",
        version="0.1.0",
        description="本地 CreatorOS 运营工作台的只读业务投影。",
        lifespan=lifespan,
        responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    )
    app.state.database = db
    app.state.queries = queries

    @app.exception_handler(ValueError)
    async def value_error_handler(_request: Request, error: ValueError):
        return _error_response(422, "invalid_request", str(error))

    @app.exception_handler(HTTPException)
    async def http_error_handler(_request: Request, error: HTTPException):
        code = {
            404: "not_found",
            409: "conflict",
            422: "invalid_request",
            503: "dependency_unavailable",
        }.get(error.status_code, "http_error")
        message = error.detail if isinstance(error.detail, str) else "请求失败。"
        return _error_response(error.status_code, code, message)

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(_request: Request, error: RequestValidationError):
        return _error_response(422, "invalid_request", "请求参数不符合接口契约。")

    @app.get("/api/health", response_model=HealthView)
    def health() -> HealthView:
        try:
            queries.health_database()
        except Exception as error:
            raise HTTPException(status_code=503, detail="数据库不可用。") from error
        return HealthView(
            status="ok",
            database="ok",
            codex_available=shutil.which("codex") is not None,
            writable_routes_enabled=False,
        )

    @app.get("/api/overview", response_model=OverviewView)
    def overview() -> OverviewView:
        return queries.overview()

    @app.get("/api/creators", response_model=PageResponse[CreatorView])
    def list_creators(
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> PageResponse[CreatorView]:
        return queries.list_creators(offset=offset, limit=limit)

    @app.get("/api/creators/{creator_id}", response_model=CreatorView)
    def get_creator(creator_id: str) -> CreatorView:
        result = queries.get_creator(creator_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Creator 不存在。")
        return result

    @app.get("/api/series/{series_id}", response_model=SeriesView)
    def get_series(series_id: str) -> SeriesView:
        result = queries.get_series(series_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Series 不存在。")
        return result

    @app.get("/api/series/{series_id}/topics", response_model=PageResponse[TopicView])
    def list_topics(
        series_id: str,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> PageResponse[TopicView]:
        result = queries.list_topics(series_id, offset=offset, limit=limit)
        if result is None:
            raise HTTPException(status_code=404, detail="Series 不存在。")
        return result

    @app.get("/api/runs", response_model=PageResponse[RunSummary])
    def list_runs(
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        status: ContentRunStatus | None = None,
        creator_id: str | None = None,
    ) -> PageResponse[RunSummary]:
        return queries.list_runs(
            offset=offset, limit=limit, status=status, creator_id=creator_id
        )

    @app.get("/api/runs/{run_id}", response_model=RunDetail)
    def get_run(run_id: str) -> RunDetail:
        result = queries.get_run(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail="ContentRun 不存在。")
        return result

    @app.get("/api/operations", response_model=PageResponse[PendingOperationView])
    def list_operations(
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> PageResponse[PendingOperationView]:
        return queries.list_operations(offset=offset, limit=limit)

    @app.get("/api/operations/{operation_id}", response_model=PendingOperationView)
    def get_operation(operation_id: str) -> PendingOperationView:
        result = queries.get_operation(operation_id)
        if result is None:
            raise HTTPException(status_code=404, detail="运营计划不存在。")
        return result

    return app


def _error_response(status_code: int, code: str, message: str):
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message[:500]}},
    )


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("creatoros.web.app:app", host="127.0.0.1", port=8765)
