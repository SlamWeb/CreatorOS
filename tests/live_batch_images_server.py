"""Fresh 15-image extraction host. Preparation is local; --live-models opts into Codex."""
from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
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


class BatchGate:
    """Fixture-only admission; a lost response never grants permission to replay."""
    def __init__(self, root, fixture_id, live):
        self.root, self.fixture_id, self.live = Path(root), fixture_id, live

    def reserve(self):
        receipt = {"fixture_id": self.fixture_id, "token": str(uuid4())}
        with (self.root / "batch-reservation.json").open("x", encoding="utf-8") as output:
            json.dump(receipt, output)
        return receipt

    def authorized(self, token):
        try:
            receipt = json.loads((self.root / "batch-reservation.json").read_text(encoding="utf-8"))
            return bool(token) and receipt == {"fixture_id": self.fixture_id, "token": token}
        except (OSError, ValueError):
            return False

    def claim(self, payload, token):
        if not self.live or not self.authorized(token):
            raise PermissionError("Explicit live fixture authorization is required")
        ids = payload.get("upload_ids", [])
        request = payload.get("request_id", "")
        if (not isinstance(ids, list) or len(ids) != 15
                or any(not isinstance(item, str) or not re.fullmatch(r"[a-f0-9]{64}", item) for item in ids)
                or len(set(ids)) != 15 or not isinstance(request, str) or not 1 <= len(request.strip()) <= 128
                or payload.get("mode", "single") != "single" or payload.get("source_text", "")):
            raise ValueError("This fixture accepts exactly 15 distinct uploaded images in single mode")
        receipt = {"fixture_id": self.fixture_id, "request_id": request,
                   "job_id": hashlib.sha256(request.encode()).hexdigest(), "payload": payload,
                   "state": "dispatch_outcome_unknown_until_job_checked"}
        with (self.root / "batch-submission.json").open("x", encoding="utf-8") as output:
            json.dump(receipt, output, ensure_ascii=False, indent=2)
        return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--live-models", action="store_true", help="Explicitly authorize one real Codex extraction")
    parser.add_argument("--codex-auth-file", type=Path, help="Explicit auth.json copied into the fresh isolated CODEX_HOME")
    args = parser.parse_args()
    try:
        root, dist = isolated(args.root), isolated(args.dist)
        if root.exists() or root.is_relative_to(dist) or dist.is_relative_to(root):
            raise ValueError("root must be new and separate from the isolated build")
        if not (dist / "index.html").is_file():
            raise ValueError("dist must contain an isolated index.html")
        if not 1 <= args.port <= 65535 or args.port == 8765:
            raise ValueError("Choose an unused non-production port other than 8765")
        if args.codex_auth_file and (not args.live_models or not args.codex_auth_file.is_file()):
            raise ValueError("--codex-auth-file requires live mode and an existing file")
        if args.live_models and not (args.codex_auth_file or os.environ.get("CODEX_API_KEY", "").strip()
                                    or os.environ.get("OPENAI_API_KEY", "").strip()):
            raise ValueError("Live mode requires explicitly supplied Codex credentials")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", args.port))
        root.mkdir(parents=True, exist_ok=False)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    for name in ("temp", "codex-home"):
        (root / name).mkdir()
    if args.codex_auth_file:
        shutil.copyfile(args.codex_auth_file, root / "codex-home" / "auth.json")
    url = f"sqlite:///{(root / 'studio.db').as_posix()}"
    os.environ.update(DATABASE_URL=url, PYTHON_DOTENV_DISABLED="1", CODEX_HOME=str(root / "codex-home"),
                      TEMP=str(root / "temp"), TMP=str(root / "temp"), TMPDIR=str(root / "temp"),
                      DEEPSEEK_API_KEY="", CREATOROS_XHS_MCP_TOKEN="",
                      CREATOROS_XHS_MCP_TOKEN_FILE=str(root / "no-publication-token"),
                      CREATOROS_XHS_MCP_URL="http://127.0.0.1:1/mcp",
                      CREATOROS_XHS_STAGING_ROOT=str(root / "publication-staging"),
                      CREATOROS_STUDIO_URL=f"http://127.0.0.1:{args.port}")
    if not args.live_models:
        os.environ.update(CODEX_API_KEY="", OPENAI_API_KEY="", CREATOROS_CODEX_EXECUTABLE=str(root / "no-codex.exe"))
    tempfile.tempdir = str(root / "temp")
    # Set the database before importing app.py, including its module-level app.
    import uvicorn
    from fastapi import HTTPException
    from fastapi.responses import JSONResponse
    from creatoros.storage import Database, upgrade_database
    from creatoros.runs import ContentRunService
    from creatoros.web.app import create_app
    upgrade_database(url)
    database = Database(url)
    app = create_app(database=database, run_service=ContentRunService(database, output_root=root / "outputs"),
                     studio_dist=dist, chat_root=root / "chats", eval_root=root / "eval", defer_background_start=True)
    service = app.state.skill_extractions
    setup = {"fixture_id": str(uuid4()), "kind": "batch-images-15-v1", "root": str(root), "dist": str(dist),
             "database": str(root / "studio.db"), "skills_root": str(service.catalog.root),
             "temp": str(root / "temp"), "codex_home": str(root / "codex-home"),
             "url": f"http://127.0.0.1:{args.port}", "live_models": args.live_models}
    (root / "batch-setup.json").write_text(json.dumps(setup, indent=2), encoding="utf-8")
    gate = BatchGate(root, setup["fixture_id"], args.live_models)

    @app.middleware("http")
    async def batch_only(request, call_next):
        path = request.url.path
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            token = request.headers.get("x-creatoros-batch-token")
            if request.method != "POST" or path not in {
                    "/test/batch/reserve", "/api/skill-extractions/uploads", "/api/skill-extractions"}:
                return JSONResponse({"error": "Only this isolated batch operation is allowed"}, status_code=403)
            if path != "/test/batch/reserve" and not gate.authorized(token):
                return JSONResponse({"error": "Reserve this fixture before uploading/submitting"}, status_code=403)
            if path == "/api/skill-extractions":
                try:
                    payload = await request.json()
                    if not isinstance(payload, dict):
                        raise ValueError("Extraction payload must be an object")
                    # Claim durably before any model-capable route is called.
                    gate.claim(payload, token)
                except PermissionError as error:
                    return JSONResponse({"error": str(error)}, status_code=403)
                except FileExistsError:
                    return JSONResponse({"error": "Already submitted; inspect batch-submission.json and its job, never replay"}, status_code=409)
                except ValueError as error:
                    return JSONResponse({"error": str(error)}, status_code=422)
        return await call_next(request)

    @app.get("/test/batch/fixture")
    def fixture():
        return {**setup, "background_services_started": app.state.background_services_started,
                "reserved": (root / "batch-reservation.json").exists(),
                "submitted": (root / "batch-submission.json").exists()}

    @app.post("/test/batch/reserve")
    def reserve():
        try:
            return gate.reserve()
        except FileExistsError:
            raise HTTPException(409, "This root was already reserved; inspect its original result before further work")

    app.router.routes[0:0] = app.router.routes[-2:]
    del app.router.routes[-2:]
    # No producer/chat/research/publication worker starts. Extraction submit()
    # starts its own queue only after the live gate has admitted the one request.
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(instance):
        async with original_lifespan(instance):
            try:
                yield
            finally:
                if args.live_models:
                    service.shutdown()

    app.router.lifespan_context = lifespan
    print(json.dumps(setup), flush=True)
    try:
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning", access_log=False)
    finally:
        database.close()


if __name__ == "__main__":
    main()
