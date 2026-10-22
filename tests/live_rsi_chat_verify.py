"""Read-only collector for the explicitly operated isolated frontend scenario.

Never submits a model/tool/production request. Saves separate immutable before
and timestamped after reports; run the original UI yourself before collecting.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

import httpx

from creatoros.config import PROJECT_ROOT


def files(directory):
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob("*") if path.is_file()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--before-remake", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    if not root.is_relative_to((PROJECT_ROOT / "tmp").resolve()):
        raise ValueError("Isolated tmp host only")
    setup = json.loads((root / "setup.json").read_text(encoding="utf-8"))
    connection = sqlite3.connect((root / "studio.db").as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in connection.execute("SELECT * FROM content_runs ORDER BY created_at,id")]
        topic = dict(connection.execute("SELECT * FROM topics WHERE id=?", (setup["topic_id"],)).fetchone())
        column = dict(connection.execute("SELECT * FROM series WHERE id=?", (setup["series_id"],)).fetchone())
        iterations = [dict(row) for row in connection.execute("SELECT * FROM skill_iterations ORDER BY id")]
        assert connection.execute("SELECT count(*) FROM topics").fetchone()[0] == 1
    finally:
        connection.close()
    before_file = root / "before-remake.json"
    if args.before_remake:
        assert len(rows) == 1 and rows[0]["status"] == "awaiting_approval", rows
        assert len(iterations) == 1 and iterations[0]["status"] in {"completed", "exhausted"}
        old_files = files(root / "outputs")
        assert any(path.endswith(".png") for path in old_files)
        snapshot = dict(run=rows[0], files=old_files, topic=topic, series=column, iteration=iterations[0])
        with before_file.open("x", encoding="utf-8") as output:
            json.dump(snapshot, output, ensure_ascii=False, indent=2)
        print(json.dumps({"before": str(before_file), "old_run": rows[0]["id"], "files": len(old_files)}))
        return
    before = json.loads(before_file.read_text(encoding="utf-8"))
    checks = {
        "same_original_topic": topic["id"] == before["topic"]["id"],
        "brief_changed": topic["brief"] != before["topic"]["brief"],
        "old_run_every_column_unchanged": before["run"] in rows,
        "only_one_explicit_fresh_run": len(rows) == 2,
        "rsi_every_column_unchanged": len(iterations) == 1 and iterations[0] == before["iteration"],
        "series_every_column_unchanged": column == before["series"],
    }
    new_run = next(row for row in rows if row["id"] != before["run"]["id"])
    checks["new_run_completed_once"] = new_run["status"] == "awaiting_approval"
    checks["clean_thread"] = bool(new_run["producer_thread_id"]) and new_run["producer_thread_id"] != before["run"]["producer_thread_id"]
    snapshot = json.loads(new_run["input_snapshot_json"])
    checks["current_brief_in_new_snapshot"] = snapshot.get("topic_brief") == topic["brief"]
    from creatoros.integrations.producer_skills import ProducerSkillCatalog
    catalog = ProducerSkillCatalog(root / "studio-producer-skills")
    candidate = catalog.describe(snapshot["skill_name"])
    checks["exact_candidate_only_for_new_run"] = (candidate["version_status"] == "candidate"
        and candidate["id"] != column["skill_name"] and candidate["digest"] == snapshot["skill_digest"])
    original = catalog.locate(column["skill_name"])
    checks["candidate_assets_unchanged"] = files(original / "assets") == files(catalog.locate(candidate["id"]) / "assets")
    checks["shared_skill_digest_unchanged"] = catalog.describe(setup["skill_id"])["digest"] == setup["digest"]
    current_files = files(root / "outputs")
    checks["old_files_sha256_unchanged"] = all(current_files.get(key) == value for key, value in before["files"].items())
    with httpx.Client(base_url=setup["url"], timeout=30) as client:
        task = client.get("/api/skill-iterations/" + iterations[0]["id"]).json()
        response = client.get("/api/runs/" + new_run["id"])
        response.raise_for_status()
        checks["frontend_readable"] = response.json()["status"] == "awaiting_approval"
    chats = [json.loads(path.read_text(encoding="utf-8")) for path in (root / "chats").glob("*/view.json")]
    usage = [entry for chat in chats for entry in chat["entries"] if entry.get("kind") == "usage"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    report = dict(checks=checks, passed=all(checks.values()), task=task,
        runs=[{key: row[key] for key in ("id", "topic_id", "status", "producer_thread_id")} for row in rows],
        chat_usage={"requests": len(usage), "input_tokens": sum(item.get("input_tokens", 0) for item in usage),
                    "output_tokens": sum(item.get("output_tokens", 0) for item in usage)},
        note="Original frontend operated by CUA, real DS/Codex. Judge scores are model output, not a quality-gain benchmark.")
    report["codex_usage"] = [{"file": str(path.relative_to(root)), **json.loads(path.read_text(encoding="utf-8"))}
                             for path in root.rglob("production_usage.json")]
    output = root / f"remake-evidence-{stamp}.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": checks, "evidence": str(output)}, ensure_ascii=False))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
