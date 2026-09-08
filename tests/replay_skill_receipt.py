"""Revalidate recorded real Codex bytes after fixing receipt normalization; no model call."""
import argparse
import json
from pathlib import Path

from creatoros.integrations.codex import parse_codex_jsonl
from creatoros.integrations.producer_skills import InstallReceipt, ProducerSkillCatalog, _write


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    catalog = ProducerSkillCatalog(args.root / "producer-skills")
    path = next((catalog.root / "jobs").glob("*.json"))
    job = json.loads(path.read_text(encoding="utf-8"))
    trace = next((catalog.root / "work" / job["id"]).rglob("codex_trace.jsonl"))
    run = parse_codex_jsonl(trace.read_text(encoding="utf-8"), receipt_model=InstallReceipt)
    record = catalog.register(trace.parent, job["github_url"], run.receipt)
    assert catalog.resolve(record["id"]).is_dir()
    job = {**job, "status": "installed", "skill": record, "thread_id": run.thread_id,
           "usage": run.usage.model_dump(), "message": "真实下载回执经路径兼容修复后核验通过；尚未绑定。"}
    _write(path, job)
    _write(args.root / "result.json", job)
    print(f"recorded_real_skill_receipt=passed skill={record['id']} commit={record['commit']}")


if __name__ == "__main__":
    main()
