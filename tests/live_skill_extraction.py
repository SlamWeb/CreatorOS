"""Explicit real Codex image-input extraction into an isolated catalog; no image generation.

python -m tests.live_skill_extraction --image <local-reference-image>
"""
import argparse
import base64
import json
from pathlib import Path
from tempfile import mkdtemp

from creatoros.integrations.producer_skills import ProducerSkillCatalog, inherit_copy_permissions
from creatoros.integrations.skill_extraction import EXTRACTION_MODEL, EXTRACTION_EFFORT, SkillExtractionService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--revise", action="store_true", help="Also run one real draft revision; no image generation")
    args = parser.parse_args()
    temporary = Path(__file__).parents[1] / "tmp"
    temporary.mkdir(exist_ok=True)
    root = Path(mkdtemp(prefix="skill-extraction-live-", dir=temporary))
    inherit_copy_permissions(root)
    service = SkillExtractionService(ProducerSkillCatalog(root / "skills"))
    service.start()
    try:
        upload = service.upload(args.image.name, base64.b64encode(args.image.read_bytes()).decode())
        job = service.submit("live-image-single", [upload["id"]], "single",
                             "提炼完整英语教学 Skill，保留中英文说明与情境表达，题材可替换。")
        print(f"evidence={root} job={job['id']}", flush=True)
        service.worker.join(200)
        job = service.get(job["id"])
        assert job["status"] == "ready", job["error"]
        extraction_thread = job["thread_id"]
        if args.revise:
            original = job["digest"]
            service.revise(job["id"], "live-revise", original, "补充规则：用户可更换词组，解释始终中英双语；保留现有角色和情境表达方法。")
            service.worker.join(200)
            job = service.get(job["id"])
            assert not job["error"] and job["digest"] != original, job
            assert job["revision"] == 2
        saved = service.save(job["id"], job["digest"])
        assert len(saved["saved_skills"]) == 1
        assert saved["saved_skills"][0]["producible"], "Extracted Skill must remain usable by current binding"
        directory = service.root / "jobs" / job["id"]
        trace_paths = list(directory.rglob("codex_trace.jsonl"))
        events = [json.loads(line) for trace in trace_paths for line in trace.read_text(encoding="utf-8").splitlines()]
        forbidden = [e for e in events if e.get("item_type") in {"imageGeneration", "webSearch", "collabAgentToolCall"}]
        assert not forbidden, forbidden
        assert all(list((Path(s["local_path"]) / "assets").glob("reference-*")) for s in saved["saved_skills"])
        usage = {path.parent.relative_to(directory).as_posix(): json.loads(path.read_text())
                 for path in directory.rglob("production_usage.json")}
        report = {"passed": True, "thread_id": job["thread_id"], "extraction_thread_id": extraction_thread,
                  "skills": [s["name"] for s in saved["saved_skills"]],
                  "model": EXTRACTION_MODEL, "effort": EXTRACTION_EFFORT, "revised": args.revise,
                  "usage": usage, "no_generation_or_search_observed": not forbidden, "quality_evaluated": False}
        (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False), flush=True)
    finally:
        service.shutdown()


if __name__ == "__main__":
    main()
