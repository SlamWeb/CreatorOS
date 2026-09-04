from __future__ import annotations

import shutil
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError

from creatoros.config import DATABASE_URL
from creatoros.runs import ContentRunError, ContentRunService, ManagedRunExecutor
from creatoros.runs.ownership import ExecutionOwnershipError
from creatoros.storage import ContentRunStatus, Database

from .queries import StudioQueryService
from .schemas import (
    CreatorCreateRequest,
    CreatorView,
    ErrorResponse,
    HealthView,
    OperationConfirmRequest,
    OperationEditRequest,
    OperationPreviewRequest,
    OperationVersionRequest,
    OverviewView,
    PageResponse,
    PendingOperationView,
    RunDetail,
    RunSummary,
    SeriesView,
    SeriesCreateRequest,
    TopicView,
    RunCancelRequest,
    RunStartRequest,
    RunExecuteRequest,
)
from .writes import StudioWriteError, StudioWriteService
from .artifacts import StudioArtifacts
from .run_routes import review_routes


def create_app(
    database_url: str | None = None,
    *,
    database: Database | None = None,
    run_service: ContentRunService | None = None,
    run_executor: ManagedRunExecutor | None = None,
) -> FastAPI:
    """Create the local Studio API with a managed, single-writer run executor."""
    owns_database = database is None
    db = database or Database(database_url or DATABASE_URL)
    writes = StudioWriteService(db)
    runs = run_service or ContentRunService(db)
    artifacts = StudioArtifacts(db, runs.output_root)
    queries = StudioQueryService(db, artifacts=artifacts)
    executor = run_executor or ManagedRunExecutor(runs)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            _app.state.stop_observers = False
            executor.start()
            try:
                yield
            finally:
                executor.shutdown()
        finally:
            if owns_database:
                db.close()

    app = FastAPI(
        title="CreatorOS Studio API",
        version="0.1.0",
        description="本地 CreatorOS 运营工作台的业务投影与确认入口。",
        lifespan=lifespan,
        responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    )
    app.state.database = db
    app.state.queries = queries
    app.state.writes = writes
    app.state.executor = executor
    app.include_router(review_routes(runs, queries, artifacts))

    @app.middleware("http")
    async def local_writes(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            allowed = {"http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:8765", "http://localhost:8765"}
            if origin is not None and origin not in allowed:
                return _error_response(403, "origin_rejected", "写入只允许本机 Studio 页面。")
            if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
                return _error_response(415, "json_required", "写请求需要 application/json。")
        return await call_next(request)

    @app.exception_handler(ContentRunError)
    async def run_error_handler(_request: Request, error: ContentRunError):
        return _error_response(error.status_code, error.code, str(error), run_id=error.run_id)

    @app.exception_handler(ExecutionOwnershipError)
    async def ownership_error_handler(_request: Request, error: ExecutionOwnershipError):
        return _error_response(503, "recovery_blocked", str(error))

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
            writable_routes_enabled=True,
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

    @app.post("/api/creators", response_model=CreatorView, status_code=201)
    def create_creator(payload: CreatorCreateRequest) -> CreatorView:
        try:
            creator = writes.create_creator(payload)
        except StudioWriteError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        result = queries.get_creator(creator.id)
        if result is None:
            raise HTTPException(status_code=503, detail="账号已保存，但读取新账号失败。")
        return result

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

    @app.post("/api/creators/{creator_id}/series", response_model=SeriesView, status_code=201)
    def create_series(creator_id: str, payload: SeriesCreateRequest) -> SeriesView:
        try:
            series = writes.create_series(creator_id, payload)
        except StudioWriteError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        result = queries.get_series(series.id)
        if result is None:
            raise HTTPException(status_code=503, detail="栏目已保存，但读取新栏目失败。")
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

    @app.post("/api/runs", response_model=RunDetail, status_code=201)
    def start_run(payload: RunStartRequest, response: Response) -> RunDetail:
        existing = runs.repository.get_by_idempotency_key(f"content:{payload.topic_id}")
        content_run = runs.create(payload.topic_id)
        response.status_code = 200 if existing is not None else 201
        result = queries.get_run(content_run.id)
        if result is None:
            raise HTTPException(status_code=503, detail="Run 已创建，但读取状态失败。")
        return result

    @app.post("/api/runs/{run_id}/resume", response_model=RunDetail, status_code=202)
    @app.post("/api/runs/{run_id}/execute", response_model=RunDetail, status_code=202)
    def resume_run(run_id: str, payload: RunExecuteRequest) -> RunDetail:
        try:
            executor.submit(run_id, expected_version=payload.expected_version)
        except ExecutionOwnershipError:
            raise
        except RuntimeError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        result = queries.get_run(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail="ContentRun 不存在。")
        return result

    @app.post("/api/runs/{run_id}/cancel", response_model=RunDetail)
    def cancel_run(run_id: str, payload: RunCancelRequest) -> RunDetail:
        executor.cancel(run_id, expected_version=payload.expected_version)
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

    @app.post("/api/operations/preview", response_model=PendingOperationView, status_code=201)
    def preview_operation(payload: OperationPreviewRequest) -> PendingOperationView:
        try:
            pending = writes.preview_topics(payload)
        except StudioWriteError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        result = queries.get_operation(pending.id)
        if result is None:
            raise HTTPException(status_code=503, detail="Preview 已保存，但读取失败。")
        return result

    @app.post("/api/operations/{operation_id}/confirm", response_model=PendingOperationView)
    def confirm_operation(operation_id: str, payload: OperationConfirmRequest) -> PendingOperationView:
        try:
            writes.confirm(
                operation_id,
                expected_version=payload.expected_version,
                expected_revision=payload.expected_revision,
                confirmation_token=payload.confirmation_token,
            )
        except StudioWriteError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        result = queries.get_operation(operation_id)
        if result is None:
            raise HTTPException(status_code=404, detail="运营计划不存在。")
        return result

    @app.post("/api/operations/{operation_id}/edit", response_model=PendingOperationView)
    def edit_operation(operation_id: str, payload: OperationEditRequest) -> PendingOperationView:
        try:
            writes.edit(operation_id, payload)
        except StudioWriteError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        result = queries.get_operation(operation_id)
        if result is None:
            raise HTTPException(status_code=404, detail="运营计划不存在。")
        return result

    @app.post("/api/operations/{operation_id}/cancel", response_model=PendingOperationView)
    def cancel_operation(operation_id: str, payload: OperationVersionRequest) -> PendingOperationView:
        try:
            writes.cancel(
                operation_id,
                expected_version=payload.expected_version,
                expected_revision=payload.expected_revision,
            )
        except StudioWriteError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        result = queries.get_operation(operation_id)
        if result is None:
            raise HTTPException(status_code=404, detail="运营计划不存在。")
        return result

    return app


def _error_response(status_code: int, code: str, message: str, *, run_id: str | None = None):
    from fastapi.responses import JSONResponse
    from .queries import _error_message

    detail = {"code": code, "message": _error_message(message) or "请求失败。"}
    if run_id is not None:
        detail["run_id"] = run_id
    return JSONResponse(
        status_code=status_code,
        content={"error": detail},
    )


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("creatoros.web.app:app", host="127.0.0.1", port=8765)
