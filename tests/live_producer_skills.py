"""Real GitHub install of our existing carousel Skill into an isolated catalog.

No image generation, publication or writes to the operational database.
"""
from datetime import datetime
import json
from pathlib import Path
import time

from creatoros.config import PROJECT_ROOT
from creatoros.integrations.producer_skills import ProducerSkillCatalog, SkillInstallService


def main():
    root = PROJECT_ROOT / "tmp" / f"producer-skills-live-{datetime.now():%Y%m%d-%H%M%S}"
    service = SkillInstallService(ProducerSkillCatalog(root / "producer-skills"))
    url = "https://github.com/SlamWeb/CreatorOS/tree/main/creatoros/skills/knowledge-to-carousel"
    job = service.submit(url)
    print(f"evidence_root={root}", flush=True)
    try:
        while job["status"] == "installing":
            time.sleep(2)
            job = service.get(job["id"])
        print(json.dumps(job, ensure_ascii=False), flush=True)
        assert job["status"] == "installed", job["message"]
        assert service.catalog.resolve(job["skill"]["id"]).is_dir()
        (root / "result.json").write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        print("live_producer_skills=passed no_generation=true", flush=True)
    finally:
        service.shutdown()


if __name__ == "__main__":
    main()
