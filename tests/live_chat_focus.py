"""Opt-in original-SPA/DeepSeek check over a read-only copied real work.

No source SQLite/file writes and no production consumers. Keep this evidence
separate from deterministic protocol tests and from image-quality evaluation.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

import uvicorn

from creatoros.config import PROJECT_ROOT
from creatoros.integrations.producer_skills import ProducerSkillCatalog, SkillInstallService
from creatoros.runs import ContentRunService
from creatoros.runs.artifacts import validate_artifact
from creatoros.runs.models import ContentRunInput
from creatoros.storage import Database, upgrade_database
from creatoros.web.app import create_app
from creatoros.web.server import StudioServer


def source_connection():
    connection = sqlite3.connect((PROJECT_ROOT / "data/creatoros.db").resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def source_digest():
    with source_connection() as connection:
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        values = {table: sorted([list(row) for row in connection.execute(f'SELECT * FROM "{table}"')], key=repr) for table in tables}
    return {"sha256": hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            "rows": {name: len(rows) for name, rows in values.items()}}


def copy_tree(source, destination, boundary):
    source = Path(source).absolute()
    if not source.resolve().is_relative_to(boundary.resolve()):
        raise ValueError("source outside approved tree")
    for path in (source, *source.rglob("*")):
        if path.is_symlink() or (path.exists() and getattr(path.lstat(), "st_file_attributes", 0) & 0x400):
            raise ValueError("source links are not accepted")
    shutil.copytree(source, destination)


def seed(root, run_id):
    before = source_digest()
    url = f"sqlite:///{(root / 'studio.db').as_posix()}"
    upgrade_database(url)
    source = source_connection()
    try:
        run = dict(source.execute("SELECT * FROM content_runs WHERE id=?", (run_id,)).fetchone())
        topic = dict(source.execute("SELECT * FROM topics WHERE id=?", (run["topic_id"],)).fetchone())
        column = dict(source.execute("SELECT * FROM series WHERE id=?", (topic["series_id"],)).fetchone())
        selected = {"creators": [dict(row) for row in source.execute("SELECT * FROM creators")],
                    "series": [dict(row) for row in source.execute("SELECT * FROM series")],
                    "topics": [dict(row) for row in source.execute("SELECT * FROM topics")],
                    "topic_removals": [dict(row) for row in source.execute("SELECT * FROM topic_removals")],
                    "content_runs": [run],
                    "content_revisions": [dict(row) for row in source.execute("SELECT * FROM content_revisions WHERE content_run_id=?", (run_id,))]}
        revision_ids = [item["id"] for item in selected["content_revisions"]]
        selected["content_attempts"] = [dict(row) for row in source.execute(
            "SELECT * FROM content_attempts WHERE revision_id IN (" + ",".join("?" for _ in revision_ids) + ")", revision_ids)]
    finally:
        source.close()
    if run["status"] not in {"awaiting_approval", "approved"}:
        raise ValueError("Only copy an already completed work; never resume source tasks")
    output_source = PROJECT_ROOT / "outputs"
    output_target = root / "outputs"
    relative = Path(column["creator_id"]) / column["id"] / run_id
    copy_tree(output_source / relative, output_target / relative, output_source)
    source_catalog = PROJECT_ROOT / "data/producer-skills"
    target_catalog = root / "studio-producer-skills"
    ids = {item[key] for item in selected["series"] for key in ("skill_name", "mind_skill_id", "production_skill_id") if item.get(key)}
    for identifier in sorted(ids):
        if identifier == "knowledge-to-carousel":
            continue
        record = source_catalog / "registry" / f"{identifier}.json"
        working = source_catalog / "working" / identifier
        if not record.is_file() or not working.is_dir():
            continue  # preserve real unavailable metadata, not a made-up Skill
        copy_tree(working, target_catalog / "working" / identifier, source_catalog)
        destination = target_catalog / "registry" / record.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(record, destination)
    for rows in selected.values():
        for row in rows:
            for key in ("artifact_directory", "manifest_path", "output_directory"):
                if row.get(key):
                    path = Path(row[key])
                    row[key] = str(output_target / path.relative_to(output_source))
            for key in ("input_snapshot_json", "production_input_json"):
                if row.get(key):
                    snapshot = json.loads(row[key])
                    if snapshot.get("skill_path"):
                        snapshot["skill_path"] = str(target_catalog / "working" / snapshot["skill_name"])
                    for value in (snapshot.get("composition") or {}).values():
                        if isinstance(value, dict) and value.get("local_path"):
                            value["local_path"] = str(target_catalog / "working" / Path(value["local_path"]).name)
                    row[key] = json.dumps(snapshot, ensure_ascii=False)
    for path in output_target.rglob("native_checkpoint.json"):
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
        for page in checkpoint.get("pages", []):
            image = Path(page["image_path"])
            if image.is_absolute():
                page["image_path"] = str(output_target / image.relative_to(output_source))
        path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")
    for path in output_target.rglob("references/input.json"):
        reference = json.loads(path.read_text(encoding="utf-8"))
        for image in reference["images"]:
            image["path"] = str(path.parent / Path(image["path"]).name)
        path.write_text(json.dumps(reference, ensure_ascii=False, indent=2), encoding="utf-8")
    for revision in selected["content_revisions"]:
        if not revision.get("artifact_directory"):
            continue
        frozen = ContentRunInput.model_validate_json(revision["production_input_json"])
        checked = validate_artifact(Path(revision["artifact_directory"]), composition=frozen.composition,
                                   production_protocol=frozen.production_protocol)
        revision["artifact_digest"] = checked.artifact_digest
        revision["validation_json"] = checked.model_dump_json()
    with sqlite3.connect(root / "studio.db") as destination:
        for table, rows in selected.items():
            for row in rows:
                columns = list(row)
                destination.execute(f'INSERT INTO "{table}" (' + ",".join(f'"{key}"' for key in columns)
                    + ") VALUES (" + ",".join("?" for _ in columns) + ")", [row[key] for key in columns])
    after = source_digest()
    if before != after:
        raise ValueError("Source changed during copying; retain evidence and reseed deliberately")
    setup = {"creator_id": column["creator_id"], "series_id": column["id"], "topic_id": topic["id"],
        "run_id": run_id, "title": topic["title"], "source_database_before": before,
        "source_files": {str(path.relative_to(PROJECT_ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in (output_source / relative).rglob("*") if path.is_file()},
        "note": "Real original work copied; no new Codex call. Other directory/topic rows retained as distractors; no historical workers/RSI copied."}
    (root / "setup.json").write_text(json.dumps(setup, ensure_ascii=False, indent=2), encoding="utf-8")
    return setup


def verify(root):
    setup = json.loads((root / "setup.json").read_text(encoding="utf-8"))
    result = json.loads((root / "frontend-result.json").read_text(encoding="utf-8"))
    trace_rows = [json.loads(line) for line in (root / "chats" / result["session_id"] / "messages.context-trace.jsonl").read_text(encoding="utf-8").splitlines()]
    finished = [row for row in trace_rows if row.get("event") == "finished"]
    checks = {"source_database_every_row_unchanged": source_digest() == setup["source_database_before"],
        "source_run_files_unchanged": all(hashlib.sha256((PROJECT_ROOT / relative).read_bytes()).hexdigest() == digest
            for relative, digest in setup["source_files"].items()),
        "one_real_model_request": result["model_requests"] == 1,
        "one_successful_trace_request": len(finished) == 1 and finished[0].get("status") == "succeeded",
        "no_account_wide_tool_lookup": result["tools"] == [],
        "eight_production_images_loaded": sum(item["kind"] == "production" and item["loaded"] for item in result["image_inventory"]) == 8,
        "reference_actually_loaded": any(item["kind"] == "skill_reference" and item["loaded"] for item in result["image_inventory"])}
    source_catalog = PROJECT_ROOT / "data/producer-skills"
    checks["copied_skill_files_still_match_source"] = all(
        hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256((source_catalog / path.relative_to(root / "studio-producer-skills")).read_bytes()).digest()
        for path in (root / "studio-producer-skills").rglob("*") if path.is_file())
    with sqlite3.connect(root / "studio.db") as connection:
        counts = {table: connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                  for table in ("content_runs", "content_revisions", "content_attempts", "skill_iterations", "production_queue_items")}
    checks["no_new_production_or_rsi"] = counts["content_runs"] == 1 and counts["skill_iterations"] == 0 and counts["production_queue_items"] == 0
    reports = {"checks": checks, "passed": all(checks.values()), "counts": counts, "usage": result["usage"],
               "finished_trace_requests": len(finished),
               "note": "Real SPA and DeepSeek; original completed images reused. This is not an equal-query A/B or quality-gain benchmark."}
    (root / "preservation-result.json").write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": reports["passed"], "checks": checks, "counts": counts}, ensure_ascii=False))
    if not reports["passed"]:
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--dist", type=Path)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--port", type=int, default=8896)
    parser.add_argument("--run", default="60506acd-aaf6-41a0-b030-46ae2c380ae9")
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_relative_to((PROJECT_ROOT / "tmp").resolve()):
        raise ValueError("Use an isolated ignored tmp folder")
    if args.verify:
        verify(root)
        return
    if args.dist is None:
        parser.error("--dist is required when serving")
    root.mkdir(parents=True, exist_ok=False)
    setup = seed(root, args.run)
    database = Database(f"sqlite:///{(root / 'studio.db').as_posix()}")
    catalog = ProducerSkillCatalog(root / "studio-producer-skills")
    app = create_app(database=database, run_service=ContentRunService(database, output_root=root / "outputs"),
        chat_root=root / "chats", skill_install_service=SkillInstallService(catalog),
        eval_root=root / "eval", studio_dist=args.dist.resolve(), defer_background_start=True)
    detail = app.state.queries.get_run(args.run)
    if not detail or not any(revision.artifact_available and len(revision.cards) == 8 for revision in detail.revisions):
        raise ValueError("Copied real eight-image work failed offline validation; do not replace it with a fixture")
    print(json.dumps({key: setup[key] for key in ("creator_id", "series_id", "topic_id", "run_id", "title")}, ensure_ascii=False), flush=True)
    try:
        StudioServer(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning", access_log=False)).run()
    finally:
        app.state.chat.shutdown()
        database.close()


if __name__ == "__main__":
    main()
