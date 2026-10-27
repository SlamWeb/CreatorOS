from __future__ import annotations

import os
import json
import re
import asyncio
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError

from creatoros.config import DATABASE_URL, PROJECT_ROOT
from creatoros.ai import DeepSeekProvider
from creatoros.operations import OperationPlanParser
from creatoros.operations.models import OperationPlan
from creatoros.operations.parser import LazyOperationParser, OperationParserUnavailable, OperationParseError, OperationScopeError
from creatoros.operations.service import PendingOperationError
from creatoros.storage import ContentRepository
from creatoros.runs import ContentRunError, ContentRunService, ManagedRunExecutor
from creatoros.publication import ManualPublicationService, PublicationService
from creatoros.publication.feedback import FeedbackService
from creatoros.publication.mcp_client import XhsMcpClient, XhsMcpError
from creatoros.runs.ownership import ExecutionOwnershipError
from creatoros.storage import ContentRunStatus, Database

from .queries import StudioQueryService
from .composition import CompositionError, SeriesCompositionService
from .schemas import (
    AssignmentRequest,
    CompositionUpdateRequest,
    CreatorCreateRequest,
    CreatorView,
    ErrorResponse,
    HealthView,
    OperationConfirmRequest,
    OperationEditRequest,
    OperationPreviewRequest,
    OperationProposeRequest,
    OperationVersionRequest,
    OverviewView,
    PageResponse,
    PendingOperationView,
    RunDetail,
    RunSummary,
    QueueTopicsRequest,
    QueueTopicsResponse,
    SeriesComposeRequest,
    SeriesDeleteRequest,
    SeriesDeleteResponse,
    SeriesRenameRequest,
    SeriesView,
    SeriesCreateRequest,
    SeriesWriteResponse,
    TopicEditRequest,
    TopicReorderRequest,
    TopicRemoveRequest,
    TopicRemoveResponse,
    TopicView,
    RunCancelRequest,
    RunStartRequest,
    RunExecuteRequest,
    RunBatchStartRequest,
    RunBatchItem,
    RunBatchResponse,
)
from .writes import StudioWriteError, StudioWriteService
from .artifacts import StudioArtifacts
from .reference_routes import reference_routes
from .run_routes import review_routes
from .publication_routes import publication_routes
from .analytics_routes import analytics_routes
from .media_collections import MediaCollectionService
from .media_collection_routes import media_collection_routes
from .production_task_routes import production_task_routes
from .material_routes import material_routes
from .skill_library_routes import create_skill_library_router
from creatoros.integrations.managed_materials import MaterialStore
from creatoros.integrations.skill_library import SkillLibraryService
from creatoros.runs.production_tasks import ProductionTaskService
from .static import mount_studio
from .chat import AgentChatService
from .chat_image_actions import chat_image_action_routes, extraction_owner_path
from .chat_routes import chat_routes
from .agent_scope import AgentScopeGuard
from .account_context import CreatorContextBuilder
from .chat_focus import ChatFocusBuilder
from .skill_routes import skill_routes
from .skill_version_routes import skill_version_routes
from .extraction_routes import extraction_routes
from .calibration_routes import calibration_routes
from .work_inbox import work_inbox_routes
from creatoros.integrations.skill_extraction import SkillExtractionService
from creatoros.integrations.skill_version_service import SkillVersionService
from creatoros.integrations.skill_iterations import SkillIterationService
from .skill_iteration_routes import skill_iteration_routes
from creatoros.integrations.skill_calibration import SkillCalibrationService
from .research_routes import research_routes
from creatoros.integrations.topic_research import TopicResearchService
from creatoros.integrations.content_discussion import ContentDiscussionService
from .discussion_routes import discussion_routes
from .worker_tasks import worker_task_routes
from .observation import ObservationService
from .observation_routes import observation_routes
from .eval_routes import eval_routes
from creatoros.evaluation.store import EvalStore
from creatoros.integrations.codex_executable import resolve_codex_executable
from creatoros.integrations.producer_skills import ProducerSkillCatalog, SkillInstallService, skills_root_for


def create_app(
    database_url: str | None = None,
    *,
    database: Database | None = None,
    run_service: ContentRunService | None = None,
    run_executor: ManagedRunExecutor | None = None,
    operation_parser: OperationPlanParser | None = None,
    operation_parser_factory=None,
    studio_dist: str | os.PathLike[str] | None = None,
    chat_root: Path | None = None,
    chat_provider_factory=None,
    skill_install_service=None,
    topic_research_service=None,
    skill_extraction_service=None,
    content_discussion_service=None,
    publication_client=None,
    eval_root: Path | None = None,
    eval_cases_path: Path | None = None,
    skill_iteration_evaluator=None,
    defer_background_start: bool = False,
) -> FastAPI:
    """Create the local Studio API with a managed, single-writer run executor."""
    owns_database = database is None
    db = database or Database(database_url or DATABASE_URL)
    def default_parser():
        key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not key:
            raise OperationParserUnavailable("未配置 DeepSeek；表单添加选题仍可使用。")
        return OperationPlanParser(DeepSeekProvider(api_key=key, timeout_seconds=60, max_retries=0), ContentRepository(db))

    parser = operation_parser or LazyOperationParser(operation_parser_factory or default_parser)
    writes = StudioWriteService(db, parser=parser)
    runs = run_service or ContentRunService(db)
    publications = ManualPublicationService(db)
    artifacts = StudioArtifacts(db, runs.output_root)
    publishing = PublicationService(db, runs, artifacts, publication_client or XhsMcpClient())
    feedback = FeedbackService(db, publishing)
    queries = StudioQueryService(db, artifacts=artifacts)
    executor = run_executor or ManagedRunExecutor(runs)
    db_file = db.engine.url.database
    session_root = (Path(db_file).resolve().parent / (Path(db_file).stem + "-agent-sessions")
                    if db_file and db_file != ":memory:" else runs.output_root / ".agent-sessions")
    skill_installs = skill_install_service or SkillInstallService(ProducerSkillCatalog(skills_root_for(db)))
    account_context = CreatorContextBuilder(db, skill_installs.catalog)
    chat = AgentChatService(chat_root or session_root, chat_provider_factory,
                            creator_lookup=ContentRepository(db).get_creator,
                            creator_context_factory=account_context.build)
    chat.focus_context_builder = ChatFocusBuilder(db, skill_installs.catalog, queries, artifacts, chat.attachments)
    research = topic_research_service or TopicResearchService(db, skill_installs.catalog)
    composition = SeriesCompositionService(db, skill_installs.catalog)
    extractions = skill_extraction_service or SkillExtractionService(skill_installs.catalog)
    skill_versions = SkillVersionService(db, extractions)
    iterations = SkillIterationService(db, skill_versions, chat, evaluator=skill_iteration_evaluator)
    runs.iteration_trial_active = iterations.is_trial_active
    calibrations = SkillCalibrationService(db, extractions, artifacts)
    discussions = content_discussion_service or ContentDiscussionService(
        db, artifacts, session_root.with_name(session_root.name + "-discussions"))
    queries.discussions = discussions
    observations = ObservationService(db, chat=chat, research=research,
                                     discussions=discussions, extractions=extractions, runs=runs)
    agent_scope = AgentScopeGuard(db, chat, research, discussions=discussions, iterations=iterations)
    def skill_source_owner(record):
        match = re.fullmatch(r"artifact extraction ([a-f0-9]{64})", record.get("source_note", ""))
        if not match:
            return None
        try:
            owner = json.loads(extraction_owner_path(chat, match.group(1)).read_text(encoding="utf-8")).get("creator_id")
            return owner if isinstance(owner, str) else None
        except (OSError, ValueError, KeyError, HTTPException):
            return None

    skill_library = SkillLibraryService(db, skill_installs.catalog, source_owner=skill_source_owner)
    agent_scope.skill_library = skill_library
    account_context.skill_library = skill_library
    scope_request_lock = asyncio.Lock()

    started_services = []

    def start_background_services():
        if app.state.background_services_started:
            return
        if started_services:
            raise RuntimeError("Background startup previously failed; inspect before retrying.")
        for service in (executor, publishing, chat, skill_installs, research, extractions,
                        calibrations, iterations, discussions, feedback):
            started_services.append(service)
            service.start()
        app.state.background_services_started = True

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            _app.state.stop_observers = False
            try:
                if not defer_background_start:
                    start_background_services()
                yield
            finally:
                # A deferred replacement has not acquired execution ownership or
                # performed recovery. Do not "clean up" old task records there.
                with ExitStack() as cleanup:
                    for service in (executor, skill_installs, research, extractions, chat, discussions, iterations):
                        if service in started_services:
                            cleanup.callback(service.shutdown)
                    if feedback in started_services:
                        await feedback.shutdown()
                    if publishing in started_services:
                        await publishing.shutdown()
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
    app.state.background_services_started = False
    app.state.start_background_services = start_background_services
    app.state.publishing = publishing
    app.state.feedback = feedback
    app.state.queries = queries
    app.state.media_collections = MediaCollectionService(db, artifacts)
    app.state.skill_library = skill_library
    app.state.materials = MaterialStore(db)
    app.state.production_tasks = ProductionTaskService(db, runs, executor, library=skill_library,
        collections=app.state.media_collections, materials=app.state.materials)
    app.state.writes = writes
    app.state.executor = executor
    app.state.chat = chat
    app.state.skill_installs = skill_installs
    app.state.topic_research = research
    app.state.skill_extractions = extractions
    app.state.skill_versions = skill_versions
    app.state.skill_iterations = iterations
    app.state.skill_calibrations = calibrations
    app.state.content_discussions = discussions
    feedback.start_gate = lambda: not (getattr(app.state, "maintenance", None)
                                      and app.state.maintenance.held)
    app.state.observation = observations
    app.state.evaluation = EvalStore(eval_root, cases_path=eval_cases_path)
    app.include_router(eval_routes(app.state.evaluation))
    app.include_router(observation_routes(observations))
    app.include_router(discussion_routes(discussions))
    app.include_router(worker_task_routes(db, research, discussions, iterations=iterations))
    app.include_router(extraction_routes(extractions))
    app.include_router(calibration_routes(calibrations))
    app.include_router(work_inbox_routes(db, research, extractions))
    app.include_router(research_routes(research, queries))
    app.include_router(reference_routes(db, writes, runs, executor, queries))
    app.include_router(skill_routes(db, skill_installs))
    app.include_router(skill_version_routes(skill_versions))
    app.include_router(skill_iteration_routes(iterations, chat))
    app.include_router(chat_routes(chat))
    app.include_router(chat_image_action_routes(chat, skill_installs.catalog, extractions, runs, artifacts, agent_scope))
    app.include_router(review_routes(runs, queries, artifacts, publications))
    app.include_router(publication_routes(publishing))
    app.include_router(analytics_routes(feedback))
    app.include_router(media_collection_routes(app.state.media_collections))
    app.include_router(production_task_routes(app.state.production_tasks))
    app.include_router(create_skill_library_router(skill_library))
    app.include_router(material_routes(app.state.materials))

    @app.middleware("http")
    async def local_writes(request: Request, call_next):
        mutating = request.method in {"POST", "PUT", "PATCH", "DELETE"}
        if mutating:
            origin = request.headers.get("origin")
            parsed = urlsplit(origin) if origin is not None else None
            local_origin = parsed is not None and parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
            if origin is not None and not local_origin:
                return _error_response(403, "origin_rejected", "写入只允许本机 Studio 页面。")
            if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
                return _error_response(415, "json_required", "写请求需要 application/json。")
        if mutating or request.headers.get("x-creatoros-agent-session"):
            # A concurrent HTTP reassignment cannot land between the ownership
            # check and the route action. This is local to this Studio process.
            async with scope_request_lock:
                scoped_response = await agent_scope.check(request)
                if scoped_response is not None:
                    return scoped_response
                return await call_next(request)
        return await call_next(request)

    @app.exception_handler(ContentRunError)
    async def run_error_handler(_request: Request, error: ContentRunError):
        return _error_response(error.status_code, error.code, str(error), run_id=error.run_id,
                               iteration_id=error.iteration_id)

    @app.exception_handler(XhsMcpError)
    async def publication_transport_error(_request: Request, error: XhsMcpError):
        return _error_response(502, "publication_transport_error", str(error))

    @app.exception_handler(CompositionError)
    async def composition_error_handler(_request: Request, error: CompositionError):
        return _error_response(error.status_code, error.code, str(error))

    @app.exception_handler(ExecutionOwnershipError)
    async def ownership_error_handler(_request: Request, error: ExecutionOwnershipError):
        return _error_response(503, "recovery_blocked", str(error))

    @app.exception_handler(ValueError)
    async def value_error_handler(_request: Request, error: ValueError):
        return _error_response(422, "invalid_request", str(error))

    @app.exception_handler(OperationParserUnavailable)
    async def parser_unavailable(_request, error):
        return _error_response(503, "parser_unavailable", str(error))

    @app.exception_handler(OperationParseError)
    async def parser_invalid(_request, error):
        return _error_response(502, "parser_invalid_output", str(error))

    @app.exception_handler(OperationScopeError)
    async def invalid_scope(_request, error):
        return _error_response(error.status_code, "invalid_scope", str(error))

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
        try:
            resolve_codex_executable()
            available = True
        except FileNotFoundError:
            available = False
        return HealthView(
            status="ok",
            database="ok",
            codex_available=available,
            writable_routes_enabled=True,
            operation_parser_configured=bool(operation_parser or operation_parser_factory or os.environ.get("DEEPSEEK_API_KEY", "").strip()),
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

    @app.get("/api/series", response_model=list[SeriesView])
    def list_series() -> list[SeriesView]:
        return queries.list_series()

    @app.delete("/api/series/{series_id}", response_model=SeriesDeleteResponse)
    def delete_series(series_id: str, payload: SeriesDeleteRequest, request: Request) -> SeriesDeleteResponse:
        # Keep checks and the delete/archive transaction serialized against the
        # in-process run executor and research submitter.
        with runs._write_lock, research.lock:
            result_id, status, deduplicated, _creator_id = composition.delete_series(
                series_id,
                expected_revision=payload.expected_revision,
                request_id=payload.request_id,
                origin=_request_origin(request),
                research=research,
            )
        return SeriesDeleteResponse(id=result_id, status=status, deduplicated=deduplicated)

    @app.patch("/api/series/{series_id}", response_model=SeriesWriteResponse)
    def rename_series(series_id: str, payload: SeriesRenameRequest, request: Request) -> SeriesWriteResponse:
        return SeriesWriteResponse.model_validate(composition.rename_series(series_id,
            name=payload.name, expected_revision=payload.expected_revision,
            request_id=payload.request_id, origin=_request_origin(request)))

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

    def _request_origin(request: Request) -> str:
        origin = request.headers.get("x-creatoros-origin", "web")
        return origin if origin in {"web", "agent", "cli"} else "web"

    def _series_write_response(request_id: str, series_id: str, deduplicated: bool) -> SeriesWriteResponse:
        view = queries.get_series(series_id)
        if view is None:
            raise HTTPException(status_code=503, detail="写入已完成，但读取栏目失败。")
        return SeriesWriteResponse(request_id=request_id, deduplicated=deduplicated, series=view)

    @app.post("/api/series", response_model=SeriesWriteResponse, status_code=201)
    def compose_series(payload: SeriesComposeRequest, request: Request) -> SeriesWriteResponse:
        series_id, deduplicated = composition.create_series(
            name=payload.name,
            description=payload.description,
            audience=payload.audience,
            creator_id=payload.creator_id,
            skill_name=payload.skill_name,
            mind_skill_id=payload.mind_skill_id,
            production_skill_id=payload.production_skill_id,
            request_id=payload.request_id,
            origin=_request_origin(request),
        )
        return _series_write_response(payload.request_id, series_id, deduplicated)

    @app.post("/api/series/{series_id}/composition", response_model=SeriesWriteResponse)
    def update_composition(series_id: str, payload: CompositionUpdateRequest, request: Request) -> SeriesWriteResponse:
        result_id, deduplicated = composition.update_composition(
            series_id,
            mind_skill_id=payload.mind_skill_id,
            production_skill_id=payload.production_skill_id,
            expected_revision=payload.expected_revision,
            request_id=payload.request_id,
            origin=_request_origin(request),
        )
        return _series_write_response(payload.request_id, result_id, deduplicated)

    @app.post("/api/series/{series_id}/assignment", response_model=SeriesWriteResponse)
    def assign_series(series_id: str, payload: AssignmentRequest, request: Request) -> SeriesWriteResponse:
        result_id, deduplicated = composition.assign_series(
            series_id,
            creator_id=payload.creator_id,
            expected_revision=payload.expected_revision,
            request_id=payload.request_id,
            origin=_request_origin(request),
        )
        return _series_write_response(payload.request_id, result_id, deduplicated)

    @app.post("/api/series/{series_id}/queue", response_model=QueueTopicsResponse, status_code=201)
    def queue_topics(series_id: str, payload: QueueTopicsRequest, request: Request) -> QueueTopicsResponse:
        if any(item.reference_id for item in payload.topics):
            from creatoros.integrations.topic_references import TopicReferenceStore
            try:
                for item in payload.topics:
                    if item.reference_id:
                        TopicReferenceStore(db).load(series_id, item.reference_id)
            except (ValueError, OSError) as error:
                raise HTTPException(422, "选题参考不可用或不属于当前栏目。") from error
        topic_ids = [f"topic-{uuid4().hex[:20]}" for _ in payload.topics]
        plan = OperationPlan(operations=[
            {
                "action": "add_topics",
                "series_id": series_id,
                "topics": [
                    {"topic_id": topic_id, "title": item.title, "brief": item.brief, "source": item.source,
                     **({"reference_id": item.reference_id} if item.reference_id else {})}
                    for topic_id, item in zip(topic_ids, payload.topics)
                ],
            }
        ])
        try:
            pending, deduplicated = writes.pending_operations.execute_direct(
                payload.summary or f"直接入队 {len(payload.topics)} 条选题",
                plan,
                scope_series_id=series_id,
                request_id=payload.request_id,
                origin=_request_origin(request),
                generated_topic_ids=True,
            )
        except PendingOperationError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return QueueTopicsResponse(
            request_id=payload.request_id,
            deduplicated=deduplicated,
            operation_id=pending.id,
            topic_ids=[topic["topic_id"] for operation in pending.plan_json["operations"]
                       for topic in operation.get("topics", [])],
        )

    @app.get("/api/series/{series_id}/queue/receipts/{request_id}")
    def queue_receipt(series_id: str, request_id: str, response: Response):
        response.headers["Cache-Control"] = "no-store"
        if not 8 <= len(request_id) <= 64:
            raise HTTPException(status_code=422, detail="无效的请求标识。")
        if ContentRepository(db).get_series(series_id) is None:
            raise HTTPException(status_code=404, detail="栏目不存在。")
        try:
            receipt = writes.pending_operations.queue_receipt(series_id, request_id)
        except PendingOperationError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return receipt or {"request_id": request_id, "status": "unknown", "deduplicated": False}

    @app.post("/api/creators/{creator_id}/delete")
    def delete_creator(creator_id: str):
        try:
            writes.delete_creator(creator_id)
        except StudioWriteError as error:
            raise HTTPException(status_code=error.status_code, detail=str(error)) from error
        return {"ok": True}

    @app.get("/api/topics/{topic_id}", response_model=TopicView)
    def get_topic(topic_id: str, response: Response):
        response.headers["Cache-Control"] = "no-store"
        result = queries.get_topic(topic_id)
        if result is None:
            raise HTTPException(status_code=404, detail="选题不存在或已移出内容库。")
        return result

    @app.get("/api/topics/{topic_id}/edit-receipts/{request_id}")
    def topic_edit_receipt(topic_id: str, request_id: str, response: Response):
        response.headers["Cache-Control"] = "no-store"
        if not 8 <= len(request_id) <= 64:
            raise HTTPException(status_code=422, detail="无效的请求标识。")
        try:
            return writes.topic_edit_receipt(topic_id, request_id) or {
                "request_id": request_id, "status": "unknown", "deduplicated": False}
        except StudioWriteError as error:
            raise HTTPException(status_code=error.status_code, detail=str(error)) from error

    @app.patch("/api/topics/{topic_id}")
    def edit_topic(topic_id: str, payload: TopicEditRequest, request: Request):
        try:
            with runs._write_lock, discussions.lock:
                return writes.edit_topic(topic_id,
                    changes=payload.model_dump(include={"title", "brief"}, exclude_unset=True),
                    expected_digest=payload.expected_digest, request_id=payload.request_id,
                    origin=_request_origin(request), discussions=discussions)
        except StudioWriteError as error:
            raise HTTPException(status_code=error.status_code, detail=str(error)) from error

    @app.post("/api/topics/{topic_id}/delete")
    def delete_topic(topic_id: str):
        try:
            with runs._write_lock:
                writes.delete_topic(topic_id)
        except StudioWriteError as error:
            raise HTTPException(status_code=error.status_code, detail=str(error)) from error
        return {"ok": True}

    @app.post("/api/topics/{topic_id}/remove", response_model=TopicRemoveResponse)
    def remove_topic(topic_id: str, payload: TopicRemoveRequest):
        try:
            with runs._write_lock, research.lock, discussions.lock:
                return writes.remove_topic(topic_id, request_id=payload.request_id, research=research,
                    discussions=discussions, batch_id=payload.batch_id, candidate_id=payload.candidate_id)
        except StudioWriteError as error:
            raise HTTPException(status_code=error.status_code, detail=str(error)) from error

    @app.post("/api/series/{series_id}/reorder")
    def reorder_topics(series_id: str, payload: TopicReorderRequest):
        try:
            writes.reorder_topics(series_id, payload.ordered_topic_ids)
        except StudioWriteError as error:
            raise HTTPException(status_code=error.status_code, detail=str(error)) from error
        return {"ok": True}

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
        return result.model_copy(update={"production_queue": executor.queue_snapshot(run_id)})

    @app.post("/api/runs", response_model=RunDetail, status_code=201)
    def start_run(payload: RunStartRequest, response: Response, request: Request) -> RunDetail:
        session_id = request.headers.get("x-creatoros-agent-session")
        if payload.mode == "fresh":
            source = runs.get(payload.source_run_id)
            if source.topic_id != payload.topic_id:
                raise ContentRunError("来源任务与选题不匹配。", status_code=409, code="source_mismatch")
            if (not payload.skill_overrides and source.task_id is None
                    and (source.input_snapshot_json.get("execution_spec") or {}).get("kind") != "video"
                    and session_id and chat.get(session_id).get("scope_kind") == "creator"):
                topic = ContentRepository(db).get_topic(payload.topic_id)
                if topic is not None:
                    account_context.require_series_skills(topic.series_id)
            content_run = runs.restart_fresh(payload.source_run_id, expected_version=payload.expected_version,
                request_id=payload.request_id, origin_session_id=session_id, skill_overrides=payload.skill_overrides)
            result = queries.get_run(content_run.id)
            if result is None:
                raise HTTPException(status_code=503, detail="新任务已创建，请使用同一请求标识核对。")
            return result
        existing = runs.repository.get_by_idempotency_key(
            f"selected:{payload.request_id}" if payload.skill_overrides else f"content:{payload.topic_id}")
        if existing is None and not payload.skill_overrides and session_id and chat.get(session_id).get("scope_kind") == "creator":
            topic = ContentRepository(db).get_topic(payload.topic_id)
            if topic is not None:
                account_context.require_series_skills(topic.series_id)
        content_run = runs.create(payload.topic_id, origin_session_id=session_id,
                                 skill_overrides=payload.skill_overrides, request_id=payload.request_id)
        response.status_code = 200 if existing is not None else 201
        result = queries.get_run(content_run.id)
        if result is None:
            raise HTTPException(status_code=503, detail="Run 已创建，但读取状态失败。")
        return result

    @app.post("/api/runs/batch", response_model=RunBatchResponse, status_code=202)
    def start_batch(payload: RunBatchStartRequest, request: Request) -> RunBatchResponse:
        items = []
        session_id = request.headers.get("x-creatoros-agent-session")
        for topic_id in payload.topic_ids:
            run_id = None
            try:
                existing = runs.repository.get_by_idempotency_key(f"content:{topic_id}")
                if existing is None and session_id and chat.get(session_id).get("scope_kind") == "creator":
                    topic = ContentRepository(db).get_topic(topic_id)
                    if topic is not None:
                        account_context.require_series_skills(topic.series_id)
                run = runs.create(topic_id, origin_session_id=session_id)
                run_id = run.id
                detail = queries.get_run(run_id)
                queue = executor.queue_snapshot(run_id)
                fresh = (detail.status == "queued" and detail.active_revision_number == 1
                         and len(detail.revisions) == 1 and not detail.revisions[0].attempts
                         and (queue is None or queue["status"] == "pending"))
                accepted = False
                if fresh or (queue and queue["status"] in {"pending", "running"}):
                    executor.submit(run_id, expected_version=detail.version)
                    accepted = True
                current = queries.get_run(run_id)
                items.append(RunBatchItem(topic_id=topic_id, run_id=run_id, status=current.status,
                                          accepted=accepted, url=f"/runs/{run_id}"))
            except ContentRunError as error:
                items.append(RunBatchItem(topic_id=topic_id, run_id=run_id, status="rejected",
                                          accepted=False, error=str(error), error_code=error.code,
                                          iteration_id=error.iteration_id,
                                          url=(f"/skills?iteration={error.iteration_id}" if error.iteration_id
                                               else f"/runs/{run_id}" if run_id else None)))
            except HTTPException as error:
                items.append(RunBatchItem(topic_id=topic_id, run_id=run_id, status="rejected",
                                          accepted=False, error=str(error.detail), error_code="invalid_skill",
                                          url=f"/runs/{run_id}" if run_id else None))
            except ExecutionOwnershipError:
                items.append(RunBatchItem(topic_id=topic_id, run_id=run_id, status="rejected",
                                          accepted=False, error="执行环境需要核对，请查看运行记录后再提交。",
                                          error_code="execution_ownership", url=f"/runs/{run_id}" if run_id else None))
        return RunBatchResponse(items=items, accepted_count=sum(item.accepted for item in items))

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
        return result.model_copy(update={"production_queue": executor.queue_snapshot(run_id)})

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

    @app.post("/api/operations/propose", response_model=PendingOperationView, status_code=201)
    def propose_operation(payload: OperationProposeRequest) -> PendingOperationView:
        try:
            pending = writes.propose(payload)
        except StudioWriteError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        result = queries.get_operation(pending.id)
        if result is None:
            raise HTTPException(status_code=503, detail="运营计划已保存，但读取失败。")
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
        if result.status != "succeeded":
            raise HTTPException(status_code=409, detail="计划未执行成功，请重新查看状态和预览。")
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

    mount_studio(app, Path(studio_dist) if studio_dist is not None else PROJECT_ROOT / "web" / "dist")
    return app


def _error_response(status_code: int, code: str, message: str, *, run_id: str | None = None,
                    iteration_id: str | None = None):
    from fastapi.responses import JSONResponse
    from .queries import _error_message

    detail = {"code": code, "message": _error_message(message) or "请求失败。"}
    if run_id is not None:
        detail["run_id"] = run_id
    if iteration_id is not None:
        detail["iteration_id"] = iteration_id
        detail["url"] = f"/skills?iteration={iteration_id}"
    return JSONResponse(
        status_code=status_code,
        content={"error": detail},
    )


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("creatoros.web.app:app", host="127.0.0.1", port=8765)
