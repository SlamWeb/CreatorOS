"""Opt-in real Luna online research; isolated database, no production or publishing."""
import json
from datetime import datetime
from pathlib import Path
from time import sleep

from creatoros.integrations.producer_skills import ProducerSkillCatalog, skills_root_for
from creatoros.integrations.topic_research import TopicResearchService
from creatoros.storage import Database, upgrade_database, Creator, Series, CreatorPlatform


def main():
    root = Path("tmp") / ("topic-research-live-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    root.mkdir(parents=True)
    url = f"sqlite:///{(root.resolve() / 'test.db').as_posix()}"
    upgrade_database(url)
    db = Database(url)
    with db.session() as session:
        session.add(Creator(id="research-test", display_name="隔离调研验收", platform=CreatorPlatform.XIAOHONGSHU))
        session.flush()
        session.add(Series(id="research-series", creator_id="research-test", name="AI 面试图解",
                           description="面向初学者，用直观图解解释 Agent 工程知识，不夸大面试频率。",
                           audience="准备 AI 应用开发面试的 Python 初学者", skill_name="knowledge-to-carousel"))
    service = TopicResearchService(db, ProducerSkillCatalog(skills_root_for(db)))
    service.start()
    try:
        batch = service.submit("research-series", 2, "只调研 Agent 工具调用和上下文管理方向，优先官方文档。每个候选给 1–2 个来源即可，不要生图。")
        print(json.dumps({"root": str(root), "batch_id": batch["id"]}), flush=True)
        while True:
            batch = service.get(batch["id"])
            if batch["status"] != "researching":
                break
            sleep(2)
        (root / "result.json").write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": batch["status"], "count": len(batch["candidates"]), "attempts": batch["attempts"]}), flush=True)
        assert batch["status"] == "ready", batch["note"]
        assert len(batch["candidates"]) > 0
    finally:
        service.shutdown()
        db.close()


if __name__ == "__main__":
    main()
