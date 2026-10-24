"""Isolated collection UI probe using existing real production artifacts, read-only.

The formal SQLite is opened read-only and backed up to a new tmp database. The
application never starts production workers and only collection writes are
allowed by default. --agent-probe explicitly enables real DeepSeek conversation
writes; production, task recovery, approval and publication remain forbidden.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

import uvicorn
from fastapi.responses import JSONResponse

from creatoros.config import PROJECT_ROOT
from creatoros.runs import ContentRunService
from creatoros.storage import Database, upgrade_database
from creatoros.web import create_app


def digest_rows(path, run_ids):
    source = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        rows = source.execute("SELECT * FROM content_runs WHERE id IN (?,?) ORDER BY id", run_ids).fetchall()
        return hashlib.sha256(json.dumps(rows, default=str, ensure_ascii=False).encode()).hexdigest()
    finally:
        source.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--source-db", type=Path, default=PROJECT_ROOT / "data/creatoros.db")
    parser.add_argument("--creator", default="creator-e765be5862d3476cbdd7")
    parser.add_argument("--port", type=int, default=8894)
    parser.add_argument("--agent-probe", action="store_true",
                        help="Explicit paid DeepSeek browser probe; still forbids production.")
    args = parser.parse_args()
    if args.agent_probe and not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        raise RuntimeError("DEEPSEEK_API_KEY missing; use local .env or environment")
    root = args.root.resolve()
    if root.exists() or not root.is_relative_to((PROJECT_ROOT / "tmp").resolve()):
        raise ValueError("Use a fresh directory below project tmp")
    root.mkdir(parents=True)
    original = args.source_db.resolve(strict=True)
    target = root / "isolated.sqlite"
    source = sqlite3.connect(original.as_uri() + "?mode=ro", uri=True)
    try:
        source.execute("PRAGMA query_only=ON")
        with sqlite3.connect(target) as destination:
            source.backup(destination)
    finally:
        source.close()
    with sqlite3.connect(target) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("""
            SELECT cr.id AS run_id, cr.topic_id, t.title, r.id AS revision_id,
                   r.artifact_directory, r.validation_json
            FROM content_runs cr JOIN topics t ON t.id=cr.topic_id
            JOIN series s ON s.id=t.series_id
            JOIN content_revisions r ON r.content_run_id=cr.id
            LEFT JOIN topic_removals x ON x.topic_id=t.id
            WHERE s.creator_id=? AND s.is_active=1 AND x.topic_id IS NULL
              AND r.artifact_directory IS NOT NULL AND r.artifact_digest IS NOT NULL
            ORDER BY cr.created_at DESC, r.revision_number DESC LIMIT 30
        """, (args.creator,)).fetchall()
    chosen, seen = [], set()
    for row in rows:
        if row["topic_id"] in seen:
            continue
        directory = Path(row["artifact_directory"])
        if directory.is_dir() and directory.resolve().is_relative_to((PROJECT_ROOT / "outputs").resolve()):
            validation = json.loads(row["validation_json"] or "{}")
            if validation.get("images"):
                chosen.append(dict(row))
                seen.add(row["topic_id"])
        if len(chosen) == 2:
            break
    if len(chosen) != 2:
        raise ValueError("Two recorded real artifact Runs are required")
    run_ids = [row["run_id"] for row in chosen]
    before = digest_rows(original, run_ids)
    originals = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                 for row in chosen for path in (Path(row["artifact_directory"]) / "images").iterdir()
                 if path.is_file()}
    url = "sqlite:///" + target.as_posix()
    upgrade_database(url)
    database = Database(url)
    # Exact output roots must match recorded legacy artifacts. All reads use the
    # real validator; writes to business endpoints and all workers are disabled.
    runs = ContentRunService(database, output_root=PROJECT_ROOT / "outputs")
    app = create_app(database=database, run_service=runs, studio_dist=args.dist,
                     chat_root=root / "chats", eval_root=root / "eval", defer_background_start=True)
    seeded = None
    if args.agent_probe:
        seeded = app.state.media_collections.create(args.creator, name="英语视频素材",
            run_ids=run_ids, request_id="isolated-media-agent-fixture")
        from creatoros.integrations.atomic_file import atomic_write_text
        atomic_write_text(root / "setup.json", json.dumps({"creator_id": args.creator,
            "collection_id": seeded["id"], "name": seeded["name"], "image_count": len(seeded["items"]),
            "checksums": [item["checksum"] for item in seeded["items"]]}, ensure_ascii=False, indent=2))

    @app.middleware("http")
    async def collection_writes_only(request, call_next):
        agent_turn = args.agent_probe and request.method == "POST" and (
            request.url.path == "/api/agent/sessions"
            or re.fullmatch(r"/api/agent/sessions/[a-f0-9-]{36}/turns", request.url.path))
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not request.url.path.startswith(
                f"/api/creators/{args.creator}/collections") and not agent_turn:
            return JSONResponse({"error": {"message": "This probe permits collection metadata writes only"}}, status_code=403)
        return await call_next(request)

    @app.get("/test/media-fixture")
    def fixture():
        return {"creator_id": args.creator, "run_ids": run_ids,
                "topics": [{"id": row["topic_id"], "title": row["title"]} for row in chosen],
                "uses_real_artifacts": True, "workers_started": app.state.background_services_started,
                "collection": {"id": seeded["id"], "name": seeded["name"]} if seeded else None,
                "source_run_rows_unchanged": digest_rows(original, run_ids) == before,
                "source_images_unchanged": all(Path(path).is_file() and
                    hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
                    for path, digest in originals.items())}

    # Test route must precede the SPA fallback.
    app.router.routes.insert(0, app.router.routes.pop())
    try:
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    finally:
        if args.agent_probe:
            app.state.chat.shutdown()
        database.close()
        report = {"source_run_rows_unchanged": digest_rows(original, run_ids) == before,
                  "source_images_unchanged": all(Path(path).is_file() and
                      hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest for path, digest in originals.items()),
                  "source_run_row_sha256": before, "source_run_ids": run_ids,
                  "new_database": str(target), "real_model_probe": args.agent_probe, "workers_started": False}
        # Diagnostic report belongs only to this isolated probe.
        from creatoros.integrations.atomic_file import atomic_write_text
        atomic_write_text(root / "preservation.json", json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
