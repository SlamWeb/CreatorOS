"""Read-only isolated lifecycle assertions; write only an ignored evidence report."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

from creatoros.config import PROJECT_ROOT
from creatoros.integrations.producer_skills import ProducerSkillCatalog, _digest


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--phase", choices=("active", "rollback"), required=True)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    assert root.is_relative_to((PROJECT_ROOT / "tmp").resolve())
    setup = json.loads((root / "setup.json").read_text(encoding="utf-8"))
    catalog = ProducerSkillCatalog(root / "studio-producer-skills")
    trials, failed = [], {}
    for file in (catalog.root / "extractions/jobs").glob("*/job.json"):
        job = json.loads(file.read_text(encoding="utf-8"))
        for trial in job.get("trials", []):
            trial_root = file.parent / job["progress_directory"]
            usage_file = trial_root / "production_usage.json"
            usage = json.loads(usage_file.read_text(encoding="utf-8")) if usage_file.exists() else None
            trials.append(dict(job_id=job["id"], candidate_id=job["version_candidate_id"],
                candidate_digest=job["version_candidate_digest"], trial_id=trial["id"],
                status=trial["status"], thread_id=trial["thread_id"], usage=usage))
            if trial["status"] == "failed":
                failed[job["id"]] = hashes(file.parent)
    successful = [item for item in trials if item["status"] == "completed"]
    assert len(successful) == 1, trials
    candidate = successful[0]
    with sqlite3.connect(f"file:{(root / 'studio.db').as_posix()}?mode=ro", uri=True) as connection:
        rows = {row[0]: dict(skill_id=row[1], revision=row[2]) for row in connection.execute(
            "SELECT id,skill_name,revision FROM series")}
        receipts = [json.loads(row[0]) for row in connection.execute("SELECT response_json FROM write_receipts")]
        assert connection.execute("SELECT COUNT(*) FROM content_runs").fetchone()[0] == 0
    expected = candidate["candidate_id"] if args.phase == "active" else setup["skill_id"]
    assert rows[setup["series_id"]]["skill_id"] == expected, rows
    assert rows[setup["series_id"]]["revision"] == (2 if args.phase == "active" else 3), rows
    assert rows[setup["other_series_id"]] == dict(skill_id=setup["skill_id"], revision=1), rows
    assert _digest(catalog.locate(setup["skill_id"])) == setup["original_digest"]
    assert _digest(catalog.locate(candidate["candidate_id"])) == candidate["candidate_digest"]
    assert catalog.describe(candidate["candidate_id"])["version_status"] == "released"
    assert (catalog.locate(candidate["candidate_id"]) / "scripts/notes.txt").read_bytes() == (root / "source/scripts/notes.txt").read_bytes()
    assert (catalog.locate(candidate["candidate_id"]) / "assets/reference.png").read_bytes() == (root / "source/assets/reference.png").read_bytes()
    chats = [json.loads(file.read_text(encoding="utf-8")) for file in (root / "chats").glob("*/view.json")]
    assert len(chats) == 1 and chats[0]["status"] == "idle"
    tools = [entry["name"] for entry in chats[0]["entries"] if entry["kind"] == "tool"]
    assert tools == ["get_producer_skill", "get_producer_skill", "update_producer_skill_file"], tools
    reply = next(entry["text"] for entry in chats[0]["entries"] if entry["kind"] == "assistant" and entry.get("terminal"))
    assert "候选" in reply and "未生产" in reply, reply
    if args.phase == "rollback":
        previous = json.loads((root / "active-evidence.json").read_text(encoding="utf-8"))
        assert previous["failed_records"] == failed, "Historical failed evidence changed"
        assert len(receipts) == 2
    report = dict(phase=args.phase, passed=True, rows=rows, receipts=receipts, trials=trials,
        failed_records=failed, agent_tools=tools, agent_reply=reply,
        agent_usage=[entry for entry in chats[0]["entries"] if entry["kind"] == "usage"])
    (root / f"{args.phase}-evidence.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("phase", "passed", "rows", "trials", "agent_tools")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
