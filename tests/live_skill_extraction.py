"""Explicit real Codex image-input extraction into an isolated catalog; no image generation.

python -m tests.live_skill_extraction --image <local-reference-image>
"""
import argparse
import base64
import json
from pathlib import Path
from tempfile import mkdtemp

from creatoros.integrations.producer_skills import ProducerSkillCatalog, inherit_copy_permissions
from creatoros.integrations.skill_extraction import SkillExtractionService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    args = parser.parse_args()
    temporary = Path(__file__).parents[1] / "tmp"
    temporary.mkdir(exist_ok=True)
    root = Path(mkdtemp(prefix="skill-extraction-live-", dir=temporary))
    inherit_copy_permissions(root)
    service = SkillExtractionService(ProducerSkillCatalog(root / "skills"))
    service.start()
    try:
        upload = service.upload(args.image.name, base64.b64encode(args.image.read_bytes()).decode())
        job = service.submit("live-image-pair", [upload["id"]], "pair",
                             "英语教学保留中英文说明；提炼内容方法和视觉呈现两份 Skill，不固定主题、格数或图片数。")
        print(f"evidence={root} job={job['id']}", flush=True)
        service.worker.join(200)
        job = service.get(job["id"])
        assert job["status"] == "ready", job["error"]
        saved = service.save(job["id"], job["digest"])
        assert len(saved["saved_skills"]) == 2
        assert saved["saved_skills"][1]["producible"], "Extracted visual Skill must remain usable by current binding"
        directory = service.root / "jobs" / job["id"]
        events = [json.loads(line) for line in (directory / "codex_trace.jsonl").read_text(encoding="utf-8").splitlines()]
        forbidden = [e for e in events if e.get("item_type") in {"imageGeneration", "webSearch", "collabAgentToolCall"}]
        assert not forbidden, forbidden
        assert all(list((Path(s["local_path"]) / "assets").glob("reference-*")) for s in saved["saved_skills"])
        usage = json.loads((directory / "production_usage.json").read_text())
        report = {"passed": True, "thread_id": job["thread_id"], "skills": [s["name"] for s in saved["saved_skills"]],
                  "usage": usage, "no_generation_or_search_observed": not forbidden, "quality_evaluated": False}
        (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False), flush=True)
    finally:
        service.shutdown()


if __name__ == "__main__":
    main()
