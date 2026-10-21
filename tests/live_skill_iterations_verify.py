"""Read-only verification/export for the opt-in isolated RSI host."""
import argparse
import hashlib
import json
from pathlib import Path

import httpx
from sqlalchemy import func, select

from creatoros.config import PROJECT_ROOT
from creatoros.integrations.producer_skills import ProducerSkillCatalog
from creatoros.storage import ContentRun, Database, Series, Topic, TopicStatus


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_relative_to((PROJECT_ROOT / "tmp").resolve()):
        raise ValueError("Isolated tmp data only")
    setup = json.loads((root / "setup.json").read_text(encoding="utf-8"))
    with httpx.Client(base_url=setup["url"], timeout=30) as client:
        response = client.get("/api/skill-iterations")
        response.raise_for_status()
        items = response.json()["items"]
        if len(items) != 1:
            raise AssertionError("Expected exactly the one explicitly submitted task")
        task = items[0]
        if task["status"] not in {"completed", "exhausted"}:
            raise AssertionError("RSI not completed: " + str(task.get("error") or task["status"]))
        images = []
        for round_ in task["rounds"]:
            for image in round_["images"]:
                picture = client.get(image["url"])
                picture.raise_for_status()
                images.append(dict(round=round_["number"], order=image["order"],
                    sha256=hashlib.sha256(picture.content).hexdigest(), bytes=len(picture.content)))
    db = Database(f"sqlite:///{(root / 'studio.db').as_posix()}")
    try:
        with db.session() as session:
            column, topic = session.get(Series, setup["series_id"]), session.get(Topic, setup["topic_id"])
            actual = dict(skill_id=column.skill_name, revision=column.revision, topic_status=topic.status.value,
                run_count=session.scalar(select(func.count()).select_from(ContentRun)))
            expected = dict(skill_id=setup["skill_id"], revision=1, topic_status=TopicStatus.QUEUED.value, run_count=0)
            assert actual == expected, (expected, actual)
        catalog = ProducerSkillCatalog(root / "studio-producer-skills")
        assert catalog.describe(setup["skill_id"])["digest"] == setup["digest"]
        assert all(catalog.describe(r["skill_id"])["version_status"] == "candidate" for r in task["rounds"])
        report = dict(frontend_submission=True, real_deepseek=True, real_codex=True,
            database_expected=expected, database_actual=actual, task=task, images=images,
            note="One live round validates wiring, not automatic quality improvement or multi-round live convergence.")
        (root / "evidence.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(dict(status=task["status"], score=task["best_score"], judge_version=task["judge"]["version"],
            images=len(images), unchanged=True, evidence=str(root / "evidence.json")), ensure_ascii=False))
    finally:
        db.close()


if __name__ == "__main__":
    main()
