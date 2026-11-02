"""Fresh production-task fixture; read-only preparation unless --live-models."""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import tempfile
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def isolated_path(value):
    path = Path(value).resolve()
    boundary = PROJECT_ROOT / "tmp"
    if boundary.resolve() != boundary:
        raise ValueError("Repository tmp must not redirect elsewhere")
    if path == boundary or not path.is_relative_to(boundary):
        raise ValueError("Use a dedicated directory below repository tmp")
    return path


def fixture_write_allowed(method, path, live_models, headers):
    if not live_models or method != "POST":
        return False
    if path == "/api/agent/sessions" or (path.startswith("/api/agent/sessions/") and path.endswith("/turns")):
        return True
    # Agent tools submit through the real Studio HTTP interface, whose account
    # guard still verifies this session's ownership. Never allow legacy starts,
    # recovery, approval, publication, or writes to a different topic.
    return (path == "/api/topics/tasks-topic/production-tasks"
            and headers.get("x-creatoros-origin") == "agent"
            and bool(headers.get("x-creatoros-agent-session")))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare-only", action="store_true", help="Default: read-only HTTP, no workers or model calls")
    mode.add_argument("--live-models", action="store_true", help="Explicitly enable paid DeepSeek/Codex calls")
    parser.add_argument("--codex-auth-file", type=Path, help="Explicit auth.json to copy into this fixture's fresh CODEX_HOME")
    args = parser.parse_args()
    try:
        if not 1 <= args.port <= 65535 or args.port == 8765:
            raise ValueError("Use a valid non-production port other than 8765")
        root, dist = isolated_path(args.root), isolated_path(args.dist)
        if root.exists() or dist.is_relative_to(root) or root.is_relative_to(dist):
            raise ValueError("Choose a new root separate from the isolated build directory")
        if not (dist / "index.html").is_file():
            raise ValueError("--dist must contain an isolated build index.html")
        if args.codex_auth_file and (not args.live_models or not args.codex_auth_file.is_file()):
            raise ValueError("--codex-auth-file requires --live-models and an existing auth file")
        if args.live_models and not os.environ.get("DEEPSEEK_API_KEY", "").strip():
            raise ValueError("--live-models requires DEEPSEEK_API_KEY in the environment; .env is not loaded")
        if args.live_models and not (args.codex_auth_file or os.environ.get("CODEX_API_KEY", "").strip()
                                    or os.environ.get("OPENAI_API_KEY", "").strip()):
            raise ValueError("--live-models requires explicit Codex credentials for its fresh CODEX_HOME")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", args.port))
        root.mkdir(parents=True, exist_ok=False)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    temporary, codex_home = root / "temp", root / "codex-home"
    temporary.mkdir()
    codex_home.mkdir()
    if args.codex_auth_file:
        shutil.copyfile(args.codex_auth_file, codex_home / "auth.json")
    database_url = f"sqlite:///{(root / 'studio.db').as_posix()}"
    os.environ.update(DATABASE_URL=database_url, CREATOROS_PRODUCTION_CONCURRENCY="2",
                      TEMP=str(temporary), TMP=str(temporary), TMPDIR=str(temporary),
                      CODEX_HOME=str(codex_home), PYTHON_DOTENV_DISABLED="1",
                      CREATOROS_API_URL=f"http://127.0.0.1:{args.port}",
                      CREATOROS_STUDIO_URL=f"http://127.0.0.1:{args.port}",
                      CREATOROS_XHS_STAGING_ROOT=str(root / "publication-staging"),
                      CREATOROS_XHS_MCP_TOKEN="", CREATOROS_XHS_MCP_TOKEN_FILE=str(root / "no-publication-token"),
                      CREATOROS_XHS_MCP_URL="http://127.0.0.1:1/mcp")
    if not args.live_models:
        os.environ.update(DEEPSEEK_API_KEY="", OPENAI_API_KEY="", CODEX_API_KEY="",
                          CREATOROS_CODEX_EXECUTABLE=str(root / "no-codex.exe"))
    tempfile.tempdir = str(temporary)
    import uvicorn
    from creatoros.storage import Database, ContentRepository, TopicSource, upgrade_database
    from creatoros.integrations.producer_skills import ProducerSkillCatalog, skills_root_for
    from creatoros.runs import ContentRunService
    from creatoros.web.app import create_app
    from creatoros.web.server import StudioServer
    upgrade_database(database_url)
    database = Database(database_url)
    catalog = ProducerSkillCatalog(skills_root_for(database))
    skills = []
    for name, label, color in (("task-dawn", "清晨像素", "冷蓝色天空"), ("task-dusk", "黄昏像素", "暖橘色天空")):
        source = root / name
        source.mkdir()
        (source / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: 隔离并行制作验收，一幅极简像素场景。\n"
            f"creatoros-display-name: {label}\ncreatoros-output: social-content-pack.image-carousel\n---\n"
            f"为每个制作任务只生成一张3:4竖版像素插画，{color}，一个穿白上衣的棕发小人站在地面。"
            "保留大量留白，不画文字。只完成当前任务要求，不生成别的任务。使用生图工具，不用代码绘制代替。"
            "这是隔离链路验收，不需要反复优化，不联网。\n", encoding="utf-8")
        skills.append(catalog.register_local(source, role="legacy_end_to_end"))
    content = ContentRepository(database)
    content.create_creator(creator_id="tasks-live", display_name="[隔离验证] 多任务制作")
    content.create_series(series_id="tasks-series", creator_id="tasks-live", name="一天的两个世界",
                          description="", audience="", skill_name=skills[0]["id"])
    content.create_series(series_id="tasks-alternate", creator_id="tasks-live", name="黄昏备用栏目",
                          description="", audience="", skill_name=skills[1]["id"])
    content.add_topic(topic_id="tasks-topic", series_id="tasks-series", title="同一个人，从清晨到黄昏",
                      brief="同一件最终作品包含清晨与黄昏两张图。", source=TopicSource.MANUAL)
    setup = dict(creator_id="tasks-live", series_id="tasks-series", topic_id="tasks-topic", skills=skills,
                 fixture_id=str(uuid4()), root=str(root), dist=str(dist), temp=str(temporary),
                 codex_home=str(codex_home), database=str(root / "studio.db"),
                 output_root=str(root / "outputs"), base_url=f"http://127.0.0.1:{args.port}",
                 mode="live-models" if args.live_models else "prepare-only")
    (root / "setup.json").write_text(json.dumps(setup, ensure_ascii=False, indent=2), encoding="utf-8")
    runs = ContentRunService(database, output_root=root / "outputs")
    app = create_app(database=database, run_service=runs, studio_dist=dist, chat_root=root / "chats",
                     eval_root=root / "eval", defer_background_start=not args.live_models)
    from fastapi.responses import JSONResponse

    @app.middleware("http")
    async def scoped_writes(request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if not fixture_write_allowed(request.method, request.url.path, args.live_models, request.headers):
                return JSONResponse({"error": {"message": "Fixture write is not authorized"}}, status_code=403)
        return await call_next(request)

    @app.get("/test/production-tasks-fixture")
    def fixture():
        return {**setup, "workers_started": app.state.background_services_started}

    app.router.routes.insert(0, app.router.routes.pop())
    print(json.dumps(setup, ensure_ascii=False), flush=True)
    try:
        StudioServer(uvicorn.Config(app, host="127.0.0.1", port=args.port,
            log_level="warning", access_log=False, timeout_graceful_shutdown=3)).run()
    finally:
        database.close()


if __name__ == "__main__":
    main()
