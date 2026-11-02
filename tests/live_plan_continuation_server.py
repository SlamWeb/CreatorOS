"""Fresh read-only-business plan fixture; explicit live DeepSeek or controlled adapter."""
from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import tempfile
from uuid import uuid4

REPO = Path(__file__).resolve().parents[1]


def isolated(value):
    boundary = REPO / "tmp"
    result = Path(value).resolve()
    if boundary.resolve() != boundary or result == boundary or not result.is_relative_to(boundary):
        raise ValueError("Use a dedicated directory below this checkout's non-redirected tmp")
    return result


def safe_steps(steps):
    if not isinstance(steps, list) or not 1 <= len(steps) <= 8:
        return False
    for step in steps:
        if not isinstance(step, dict) or not isinstance(step.get("arguments", {}), dict):
            return False
        tool, args = step.get("tool"), step.get("arguments", {})
        if tool in {"request_review", "get_producer_skill"}:
            continue
        if tool in {"manage_topics", "manage_material"} and args.get("action") in {"get", "list"}:
            continue
        return False
    return True


class FixtureAdmission:
    def __init__(self, root, fixture_id):
        self.root, self.fixture_id = Path(root), fixture_id

    def reserve(self):
        value = {"fixture_id": self.fixture_id, "token": str(uuid4())}
        self.claim("reservation", value)
        return value

    def authorized(self, token):
        try:
            return bool(token) and json.loads((self.root / "reservation.json").read_text(encoding="utf-8")) == {
                "fixture_id": self.fixture_id, "token": token}
        except (ValueError, OSError):
            return False

    def claim(self, name, payload):
        with (self.root / (name + ".json")).open("x", encoding="utf-8") as output:
            json.dump({"fixture_id": self.fixture_id, **payload}, output, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--scenario", choices=("review", "recovery"), required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live-models", action="store_true", help="Explicitly authorize real DeepSeek; never production")
    mode.add_argument("--controlled", action="store_true", help="Deterministic local provider, no model calls")
    args = parser.parse_args()
    try:
        root, dist = isolated(args.root), isolated(args.dist)
        if root.exists() or root.is_relative_to(dist) or dist.is_relative_to(root):
            raise ValueError("Choose a fresh root separate from the isolated build")
        if not (dist / "index.html").is_file():
            raise ValueError("An isolated build index.html is required")
        if not 1 <= args.port <= 65535 or args.port == 8765:
            raise ValueError("Use an unused non-production port other than 8765")
        if args.live_models and not os.environ.get("DEEPSEEK_API_KEY", "").strip():
            raise ValueError("Explicit DEEPSEEK_API_KEY required; .env is not loaded")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", args.port))
        root.mkdir(parents=True, exist_ok=False)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    for name in ("temp", "codex-home"):
        (root / name).mkdir()
    url = f"sqlite:///{(root / 'studio.db').as_posix()}"
    os.environ.update(DATABASE_URL=url, PYTHON_DOTENV_DISABLED="1", CODEX_HOME=str(root / "codex-home"),
        TEMP=str(root / "temp"), TMP=str(root / "temp"), TMPDIR=str(root / "temp"),
        OPENAI_API_KEY="", CODEX_API_KEY="", CREATOROS_CODEX_EXECUTABLE=str(root / "no-codex.exe"),
        CREATOROS_XHS_MCP_TOKEN="", CREATOROS_XHS_MCP_TOKEN_FILE=str(root / "no-publication-token"),
        CREATOROS_XHS_MCP_URL="http://127.0.0.1:1/mcp", CREATOROS_XHS_STAGING_ROOT=str(root / "publication-staging"),
        CREATOROS_STUDIO_URL=f"http://127.0.0.1:{args.port}")
    if not args.live_models:
        os.environ["DEEPSEEK_API_KEY"] = ""
    tempfile.tempdir = str(root / "temp")
    import uvicorn
    from fastapi import HTTPException
    from fastapi.responses import JSONResponse
    from creatoros.storage import Database, ContentRepository, TopicSource, upgrade_database
    from creatoros.runs import ContentRunService
    from creatoros.integrations.producer_skills import ProducerSkillCatalog, SkillInstallService, skills_root_for
    from creatoros.integrations.skill_extraction import SkillExtractionService
    from creatoros.integrations.topic_research import TopicResearchService
    from creatoros.web.app import create_app
    from tests.plan_continuation_controlled import ControlledPlanProvider

    upgrade_database(url)
    database = Database(url)
    content = ContentRepository(database)
    creator = "plan-live-" + uuid4().hex[:12]
    content.create_creator(creator_id=creator, display_name="[隔离验证] 只读计划")
    content.create_series(series_id="plan-series", creator_id=creator, name="计划验证栏目", description="", audience="", skill_name=None)
    content.add_topic(topic_id="plan-topic", series_id="plan-series", title="本地计划样本", brief="仅查询，不制作", source=TopicSource.MANUAL)
    forbidden = []

    def denied(*_args, **_kwargs):
        forbidden.append("blocked-production-or-external-call")
        raise RuntimeError("This fixture forbids Codex, production, installation, research and publication")

    class Forbidden:
        def __getattr__(self, _name):
            return denied

    catalog = ProducerSkillCatalog(skills_root_for(database))
    state = {}
    app = create_app(database=database,
        run_service=ContentRunService(database, output_root=root / "outputs", producer_factory=denied),
        studio_dist=dist, chat_root=root / "chats", eval_root=root / "eval", defer_background_start=True,
        skill_install_service=SkillInstallService(catalog, installer=Forbidden()),
        skill_extraction_service=SkillExtractionService(catalog, extractor=denied, reviser=denied, producer_factory=denied),
        topic_research_service=TopicResearchService(database, catalog, researcher=Forbidden()),
        publication_client=Forbidden(),
        chat_provider_factory=(lambda: ControlledPlanProvider(state, args.scenario)) if args.controlled else None)
    sid = app.state.chat.create(creator)["id"]
    state.update(app=app, session_id=sid)
    setup = {"kind": "plan-continuation-v1", "fixture_id": str(uuid4()), "root": str(root), "dist": str(dist),
        "temp": str(root / "temp"), "codex_home": str(root / "codex-home"), "database": str(root / "studio.db"),
        "url": f"http://127.0.0.1:{args.port}", "scenario": args.scenario, "creator_id": creator,
        "series_id": "plan-series", "session_id": sid,
        "mode": "live" if args.live_models else "controlled" if args.controlled else "prepare"}
    (root / "plan-fixture.json").write_text(json.dumps(setup, indent=2), encoding="utf-8")
    admission = FixtureAdmission(root, setup["fixture_id"])
    plan_path = f"/api/agent/sessions/{sid}/plans"
    active = args.live_models or args.controlled

    @app.middleware("http")
    async def restricted_writes(request, call_next):
        path, method = request.url.path, request.method
        if method not in {"GET", "HEAD", "OPTIONS"}:
            token = request.headers.get("x-creatoros-plan-token")
            agent = (request.headers.get("x-creatoros-origin") == "agent"
                     and request.headers.get("x-creatoros-agent-session") == sid)
            if method == "POST" and path == "/test/plan/reserve":
                return await call_next(request)
            if not active or not (agent or admission.authorized(token)):
                return JSONResponse({"error": "Fixture is not authorized for this write"}, status_code=403)
            try:
                payload = await request.json()
                if not isinstance(payload, dict):
                    raise ValueError("JSON object required")
                if method == "POST" and path == f"/api/agent/sessions/{sid}/turns" and admission.authorized(token):
                    admission.claim("initial-turn", {"request_id": payload.get("request_id"), "session_id": sid,
                        "state": "outcome_unknown_until_session_checked", "payload": payload})
                elif method in {"POST", "PATCH"} and (path == plan_path or re.fullmatch(re.escape(plan_path) + r"/[a-f0-9]{64}", path)):
                    if not safe_steps(payload.get("steps")):
                        return JSONResponse({"error": "Only checkpoint and read-only business steps are allowed"}, status_code=403)
                    if method == "POST":
                        identity = hashlib.sha256(json.dumps({"session_id": sid, "request_id": payload.get("request_id")},
                            ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                        admission.claim("plan-submission", {"plan_id": identity, "payload": payload,
                            "state": "outcome_unknown_until_plan_checked"})
                elif method == "POST" and re.fullmatch(re.escape(plan_path) + r"/[a-f0-9]{64}/actions", path):
                    if payload.get("action") not in {"pause", "resume", "skip"}:
                        return JSONResponse({"error": "Plan retry is disabled in this fixture"}, status_code=403)
                else:
                    return JSONResponse({"error": "Production, publishing and unrelated writes are disabled"}, status_code=403)
            except FileExistsError:
                return JSONResponse({"error": "Already dispatched; inspect the original session/plan, never replay"}, status_code=409)
            except ValueError as error:
                return JSONResponse({"error": str(error)}, status_code=422)
        return await call_next(request)

    @app.get("/test/plan/fixture")
    def fixture():
        with app.state.chat.lock:
            requests = app.state.chat._read(sid)["requests"]
            continuations = [{key: item.get(key) for key in ("id", "event_id", "status")}
                for item in requests if item.get("kind") == "plan_continuation"]
        return {**setup, "background_services_started": app.state.background_services_started,
            "forbidden_calls": forbidden, "reserved": (root / "reservation.json").exists(),
            "continuations": continuations,
            "initial_turn_claimed": (root / "initial-turn.json").exists(),
            "plan_claimed": (root / "plan-submission.json").exists()}

    @app.post("/test/plan/reserve")
    def reserve():
        try:
            return admission.reserve()
        except FileExistsError:
            raise HTTPException(409, "This root was already reserved; inspect the first invocation")

    app.router.routes[0:0] = app.router.routes[-2:]
    del app.router.routes[-2:]
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(instance):
        async with original_lifespan(instance):
            if active:
                app.state.chat.start()
                app.state.agent_plans.start()
            try:
                yield
            finally:
                if active:
                    app.state.agent_plans.shutdown()
                    app.state.chat.shutdown()

    app.router.lifespan_context = lifespan
    print(json.dumps(setup), flush=True)
    try:
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning", access_log=False)
    finally:
        database.close()


if __name__ == "__main__":
    main()
