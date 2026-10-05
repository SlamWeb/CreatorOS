"""Explicit real Codex image-input extraction into an isolated catalog; no image generation.

python -m tests.live_skill_extraction --image <local-reference-image>
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
from tempfile import mkdtemp

from creatoros.integrations.producer_skills import ProducerSkillCatalog, inherit_copy_permissions
from creatoros.integrations.skill_extraction import EXTRACTION_MODEL, EXTRACTION_EFFORT, SkillExtractionService, extraction_timeout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--revise", action="store_true", help="Also run one real draft revision; no image generation")
    parser.add_argument("--mode", choices=["single", "mind", "visual", "pair"], default="single")
    parser.add_argument("--instruction", default="提炼完整英语教学 Skill，保留中英文说明与情境表达，题材可替换。")
    parser.add_argument("--no-save", action="store_true", help="Validate preview only; no registration even in the isolated catalog")
    args = parser.parse_args()
    temporary = Path(__file__).parents[1] / "tmp"
    temporary.mkdir(exist_ok=True)
    root = Path(mkdtemp(prefix="skill-extraction-live-", dir=temporary))
    inherit_copy_permissions(root)
    service = SkillExtractionService(ProducerSkillCatalog(root / "skills"))
    service.start()
    try:
        upload = service.upload(args.image.name, base64.b64encode(args.image.read_bytes()).decode())
        job = service.submit("live-image-" + args.mode, [upload["id"]], args.mode, args.instruction)
        print(f"evidence={root} job={job['id']}", flush=True)
        while service.worker.is_alive():
            service.worker.join(5)
            current = service.get(job["id"])
            progress = current.get("progress") or {}
            activities = service.events(job["id"])["items"]
            print(json.dumps({"status": current["status"], "activity": progress.get("activity"),
                              "public_events": len(activities), "last_title": activities[-1]["title"] if activities else None},
                             ensure_ascii=False), flush=True)
        job = service.get(job["id"])
        assert job["status"] == "ready", job["error"]
        extraction_thread = job["thread_id"]
        directory = service.root / "jobs" / job["id"]
        from creatoros.integrations.skill_draft_files import MODE_FOLDERS
        assert all((directory / "draft" / folder / "SKILL.md").is_file() for folder in MODE_FOLDERS[args.mode])
        if args.revise:
            original = job["digest"]
            service.revise(job["id"], "live-revise", original, "补充规则：用户可更换词组，解释始终中英双语；保留现有角色和情境表达方法。")
            service.worker.join(extraction_timeout() + 15)
            job = service.get(job["id"])
            assert not job["error"] and job["digest"] != original, job
            assert job["revision"] == 2
            assert list((directory / "revisions").glob("*/draft/*/SKILL.md"))
        saved = service.get(job["id"]) if args.no_save else service.save(job["id"], job["digest"])
        if not args.no_save:
            assert len(saved["saved_skills"]) == len(job["skills"])
            assert all(s["producible"] for s in saved["saved_skills"] if s["role"] != "mind")
        trace_paths = list(directory.rglob("codex_trace.jsonl"))
        events = [json.loads(line) for trace in trace_paths for line in trace.read_text(encoding="utf-8").splitlines()]
        forbidden = [e for e in events if e.get("item_type") in {"imageGeneration", "webSearch", "collabAgentToolCall"}]
        assert not forbidden, forbidden
        reference_digest = hashlib.sha256(args.image.read_bytes()).hexdigest()
        assert reference_digest in {hashlib.sha256(p.read_bytes()).hexdigest()
                                    for p in (directory / "references").glob("reference-*")}
        for skill in saved["saved_skills"]:
            if skill["role"] == "mind":
                continue
            assert reference_digest in {hashlib.sha256(p.read_bytes()).hexdigest()
                                       for p in (Path(skill["local_path"]) / "assets").rglob("*")
                                       if p.is_file()}, "Actual reference bytes must survive registration"
        instructions = [p.read_text(encoding="utf-8") for p in directory.rglob("instructions.txt")]
        assert all("skill-creator" not in p and "legacy_end_to_end" not in p for p in instructions)
        usage = {path.parent.relative_to(directory).as_posix(): json.loads(path.read_text())
                 for path in directory.rglob("production_usage.json")}
        activity = service.events(job["id"])
        assert any(e["kind"] == "message" for e in activity["items"]), "Real public Codex messages must be visible"
        assert any(e["kind"] == "tool" for e in activity["items"]), "Real tool activity must be visible"
        assert service.list()[0].get("events") is None, "Full activity must not enter job list"
        report = {"passed": True, "thread_id": job["thread_id"], "extraction_thread_id": extraction_thread,
                  "skills": [s["name"] for s in job["skills"]], "mode": args.mode, "registered": not args.no_save,
                  "public_event_count": len(activity["items"]),
                  "model": EXTRACTION_MODEL, "effort": EXTRACTION_EFFORT, "revised": args.revise,
                  "native_draft_written": True, "reference_bytes_preserved": True,
                  "usage": usage, "no_generation_or_search_observed": not forbidden, "quality_evaluated": False}
        (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False), flush=True)
    finally:
        service.shutdown()


if __name__ == "__main__":
    main()
