"""Fresh-only Skill batch probe. Preparation never starts a model or worker.

Use web/skill-edit-batch-live.mjs for the complete opt-in browser entrypoint.
"""
from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import uuid

REPO = Path(__file__).resolve().parent.parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def exclusive_json(path, value):
    # Never remove this marker, including after a failed/unknown request.
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def allowed_write(method, path, *, live, claimed):
    if method in {"GET", "HEAD", "OPTIONS"}:
        return True
    if not live or not claimed:
        return False
    return bool((method == "POST" and (path == "/api/agent/sessions" or re.fullmatch(
        r"/api/agent/sessions/[a-f0-9-]{36}/(?:attachments|turns|image-actions/(?:read|skill-asset))", path)))
        or (method == "PUT" and re.fullmatch(r"/api/producer-skills/[^/]+/(?:files/content|assets)", path))
        or (method == "DELETE" and re.fullmatch(r"/api/producer-skills/[^/]+/assets", path)))


def configure_environment(root, port, live):
    key = os.environ.get("DEEPSEEK_API_KEY", "") if live else ""
    for name in list(os.environ):
        upper = name.upper()
        if upper.startswith("CREATOROS_") or re.search(r"TOKEN|SECRET|PASSWORD|COOKIE|CREDENTIAL|API_KEY", upper):
            os.environ.pop(name)
    temporary = root / "temp"
    temporary.mkdir()
    os.environ.update(DATABASE_URL=f"sqlite:///{(root / 'studio.db').as_posix()}",
        DEEPSEEK_API_KEY=key, OPENAI_API_KEY="", CODEX_API_KEY="", PYTHON_DOTENV_DISABLED="1",
        TEMP=str(temporary), TMP=str(temporary), TMPDIR=str(temporary), CODEX_HOME=str(root / "codex-home"),
        CREATOROS_CODEX_EXECUTABLE=str(root / "no-codex.exe"),
        CREATOROS_POWERSHELL_EXECUTABLE=str(root / "no-powershell.exe"),
        CREATOROS_MEDIA_DOWNLOADER=str(root / "no-downloader.exe"),
        CREATOROS_STUDIO_URL=f"http://127.0.0.1:{port}",
        CREATOROS_XHS_MCP_URL="http://127.0.0.1:1/mcp", CREATOROS_XHS_MCP_TOKEN="",
        CREATOROS_XHS_MCP_TOKEN_FILE=str(root / "no-token"),
        CREATOROS_XHS_STAGING_ROOT=str(root / "publication-staging"),
        PERSONCLONE_BASE_URL="http://127.0.0.1:1", PERSONCLONE_SESSION_COOKIE="")
    return key


def make_app(root, dist, port, fixture_id, live):
    key = configure_environment(root, port, live)
    # Set the private DB and disable dotenv before any CreatorOS import: app.py
    # also constructs a module-level application.
    from PIL import Image, ImageDraw
    from fastapi.responses import JSONResponse
    from sqlalchemy import text
    from creatoros.ai import DeepSeekProvider
    from creatoros.integrations.producer_skills import ProducerSkillCatalog, SkillInstallService
    from creatoros.integrations.skill_extraction import SkillExtractionService
    from creatoros.integrations.topic_research import TopicResearchService
    from creatoros.runs import ContentRunService
    from creatoros.storage import ContentRepository, Database, Series, upgrade_database
    from creatoros.web.app import create_app

    url = os.environ["DATABASE_URL"]
    upgrade_database(url)
    db = Database(url)
    repository = ContentRepository(db)
    creator_a, creator_b = f"asset-{fixture_id}-a", f"asset-{fixture_id}-b"
    series_a, series_b = f"asset-series-{fixture_id}-a", f"asset-series-{fixture_id}-b"
    repository.create_creator(creator_id=creator_a, display_name=f"[隔离测试 {fixture_id[:8]}] 主账号")
    repository.create_creator(creator_id=creator_b, display_name=f"[隔离测试 {fixture_id[:8]}] 其他账号")
    forbidden = []
    def denied(*_args, **_kwargs):
        forbidden.append("blocked-external-or-production-call")
        raise AssertionError("Skill batch probe forbids research, production, installation and publication")
    class Forbidden:
        def __getattr__(self, _name):
            return denied
    inputs = root / "inputs"
    inputs.mkdir()
    replacements = []
    for number in range(1, 9):
        image = Image.new("RGB", (160, 200), (20 * number, 210 - 15 * number, 35 + 20 * number))
        ImageDraw.Draw(image).text((8, 16), f"fixture {fixture_id[:8]} / {number}", fill="white")
        target = inputs / f"image-{number:02d}.png"
        image.save(target)
        if number > 6:
            replacements.append(target)
    catalog = ProducerSkillCatalog(root / "skills")
    records = {}
    for name in ("asset-live-main", "asset-live-other"):
        source = root / "sources" / name
        source.mkdir(parents=True)
        body = (f"---\nname: {name}\ndescription: 隔离批量编辑测试\n"
                "creatoros-output: social-content-pack.image-carousel\n---\n\n"
                f"# {name}\n\n保留规则：只保存候选，不生产、不发布。\nfixture: {fixture_id}\n\n")
        if name == "asset-live-main":
            (source / "assets").mkdir()
            for number in range(1, 7):
                filename = f"reference-{number:02d}.png"
                (source / "assets" / filename).write_bytes((inputs / f"image-{number:02d}.png").read_bytes())
                body += f"参考{number}：![参考{number}](assets/{filename})\n"
        (source / "SKILL.md").write_text(body, encoding="utf-8")
        records[name] = catalog.register_local(source, role="legacy_end_to_end")
    with db.session() as session:
        for sid, creator, name, skill in ((series_a, creator_a, "参考图栏目", "asset-live-main"),
                                         (series_b, creator_b, "其他账号栏目", "asset-live-other")):
            session.add(Series(id=sid, creator_id=creator, name=name, description="隔离资产验证", audience="测试",
                               skill_name=records[skill]["id"]))
    installs = SkillInstallService(catalog, installer=Forbidden())
    app = create_app(database=db, run_service=ContentRunService(db, output_root=root / "outputs", producer_factory=denied),
        skill_install_service=installs, skill_extraction_service=SkillExtractionService(catalog, extractor=denied,
            reviser=denied, producer_factory=denied), topic_research_service=TopicResearchService(db, catalog, researcher=Forbidden()),
        publication_client=Forbidden(), chat_root=root / "chats", eval_root=root / "eval", studio_dist=dist,
        chat_provider_factory=(lambda: DeepSeekProvider(api_key=key, model="deepseek-flash", timeout_seconds=60, max_retries=0)) if live else denied,
        defer_background_start=True)
    setup = {"protocol": "skill-edit-batch-v1", "fixture_id": fixture_id, "root": str(root),
        "url": f"http://127.0.0.1:{port}", "studio_dist": str(dist), "live_models": live,
        "creator_a": creator_a, "creator_b": creator_b, "series_a": series_a, "series_b": series_b,
        "skill_ids": {name: value["id"] for name, value in records.items()}, "old_reference_count": 6,
        "input_pngs": [{"path": str(path), "sha256": digest(path)} for path in replacements]}
    exclusive_json(root / "setup.json", setup)
    original_lifespan = app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(application):
        async with original_lifespan(application):
            if live:
                app.state.chat.start()
            try:
                yield
            finally:
                if live:
                    app.state.chat.shutdown()
    app.router.lifespan_context = lifespan
    claimed = False
    @app.middleware("http")
    async def writes(request, call_next):
        nonlocal claimed
        path, method = request.url.path, request.method
        if path == "/test/skill-edit-batch/claim" and method == "POST":
            if not live:
                return JSONResponse({"error": "Live mode is disabled"}, status_code=403)
            try:
                exclusive_json(root / "execution-started.json", {"fixture_id": fixture_id, "result": "unknown"})
            except FileExistsError:
                return JSONResponse({"error": "Already claimed; inspect existing evidence, never resend"}, status_code=409)
            claimed = True
            return JSONResponse({"fixture_id": fixture_id, "claimed": True}, status_code=201)
        if not allowed_write(method, path, live=live, claimed=claimed):
            return JSONResponse({"error": "Isolated Skill batch write gate"}, status_code=403)
        if method not in {"GET", "HEAD", "OPTIONS"}:
            if path == "/api/agent/sessions":
                body = await request.json()
                if body.get("creator_id") != creator_a:
                    return JSONResponse({"error": "Wrong fixture account"}, status_code=403)
                marker = "session-started.json"
            elif path.endswith("/turns"):
                marker = "turn-started.json"
            else:
                marker = None
            if path.startswith("/api/agent/sessions/"):
                sid = path.split("/")[4]
                try:
                    view = app.state.chat.get(sid)
                except Exception:
                    view = {}
                if view.get("creator_id") != creator_a:
                    return JSONResponse({"error": "Wrong fixture session"}, status_code=403)
            if "/producer-skills/" in path or path.endswith("/image-actions/skill-asset"):
                sid = path.split("/")[4] if "/image-actions/" in path else request.headers.get("x-creatoros-agent-session")
                try:
                    view = app.state.chat.get(sid) if sid else {}
                    family = catalog.list_skill_versions(records["asset-live-main"]["id"])["items"]
                    permitted = {item["id"] for item in family}
                except Exception:
                    permitted, view = set(), {}
                selected = (await request.json()).get("skill_id") if "/image-actions/" in path else path.split("/")[3]
                if view.get("creator_id") != creator_a or selected not in permitted:
                    return JSONResponse({"error": "Only the fixture Skill family can be edited"}, status_code=403)
            if marker:
                try:
                    exclusive_json(root / marker, {"path": path, "result": "unknown"})
                except FileExistsError:
                    return JSONResponse({"error": "Result may already exist; inspect, do not resend"}, status_code=409)
        return await call_next(request)
    @app.get("/api/skill-assets-probe/evidence")
    def evidence():
        with db.engine.connect() as connection:
            series = [dict(row._mapping) for row in connection.execute(text(
                "SELECT id,creator_id,name,description,audience,skill_name FROM series ORDER BY id"))]
            runs_count = connection.execute(text("SELECT COUNT(*) FROM content_runs")).scalar()
        files = [{"path": file.relative_to(root).as_posix(), "sha256": digest(file)}
                 for file in sorted((root / "skills").rglob("*"))
                 if file.is_file() and (file.name == "SKILL.md" or "assets" in file.parts)]
        return {"setup": setup, "series": series, "runs_count": runs_count, "files": files,
                "forbidden_calls": forbidden, "workers_started": app.state.background_services_started,
                "chat_started": live, "claimed": claimed}
    app.router.routes.insert(0, app.router.routes.pop())
    return app, db, setup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--prepare-only", action="store_true")
    modes.add_argument("--serve", action="store_true")
    parser.add_argument("--live-models", action="store_true")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--fixture-id", default=uuid.uuid4().hex)
    args = parser.parse_args()
    boundary, root = (REPO / "tmp").resolve(), args.root.resolve()
    if boundary != REPO.resolve() / "tmp" or root == boundary or not root.is_relative_to(boundary) or root.exists():
        parser.error("--root must be a fresh directory under this checkout's unredirected tmp")
    dist = args.dist.resolve()
    if not args.dist.is_absolute() or not dist.is_relative_to(boundary) or not (dist / "index.html").is_file():
        parser.error("--dist must be an independent build under this checkout's tmp")
    if dist.is_relative_to(root) or root.is_relative_to(dist):
        parser.error("Server and build directories must be separate")
    if not re.fullmatch(r"[a-f0-9]{32}", args.fixture_id):
        parser.error("Invalid fixture ID")
    if not 1 <= args.port <= 65535 or args.port == 8765:
        parser.error("Use an explicit valid non-production port other than 8765")
    if args.live_models and (not args.serve or os.environ.get("CREATOROS_SKILL_EDIT_LIVE") != "1"
                            or not os.environ.get("DEEPSEEK_API_KEY", "").strip()):
        parser.error("Live mode requires --serve, CREATOROS_SKILL_EDIT_LIVE=1 and DEEPSEEK_API_KEY")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", args.port))
    root.mkdir(parents=True, exist_ok=False)
    exclusive_json(root / "server-started.json", {"fixture_id": args.fixture_id, "live_models": args.live_models})
    app, db, setup = make_app(root, dist, args.port, args.fixture_id, args.live_models)
    print(json.dumps(setup, ensure_ascii=False), flush=True)
    try:
        if args.serve:
            import uvicorn
            from creatoros.web.server import StudioServer
            StudioServer(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning", access_log=False)).run()
    finally:
        db.close()


if __name__ == "__main__":
    main()
