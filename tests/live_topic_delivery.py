"""Opt-in real Codex research -> candidate JSON -> isolated SQLite delivery."""
import json
from datetime import datetime
from pathlib import Path
from time import monotonic, sleep

from creatoros.integrations.codex import CODEX_MODEL, CODEX_EFFORT
from creatoros.integrations.producer_skills import ProducerSkillCatalog, skills_root_for
from creatoros.integrations.topic_research import TopicResearchService, CandidateSelection
from creatoros.storage import Database, upgrade_database, Creator, Series, CreatorPlatform, ContentRepository


def main():
    root = Path("tmp") / ("topic-delivery-live-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    root.mkdir(parents=True)
    url = f"sqlite:///{(root.resolve() / 'test.db').as_posix()}"
    upgrade_database(url)
    db = Database(url)
    with db.session() as session:
        session.add(Creator(id="delivery-test", display_name="交付隔离验证", platform=CreatorPlatform.XIAOHONGSHU))
        session.flush()
        session.add(Series(id="delivery-series", creator_id="delivery-test", name="Python基础",
                           description="以初学者能理解的例子介绍Python标准库。", audience="Python初学者",
                           skill_name="knowledge-to-carousel"))
    service = TopicResearchService(db, ProducerSkillCatalog(skills_root_for(db)))
    repo = ContentRepository(db)
    report = {"status": "started", "directory": str(root.resolve()), "model": CODEX_MODEL,
              "effort": CODEX_EFFORT, "formal_data_written": False, "production_calls": 0}
    save = lambda: (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    save()
    try:
        batch = service.submit("delivery-series", 2,
            "请联网并打开Python官方文档，给两个小选题：pathlib的路径拼接、json的序列化。每题一个官方来源即可，简短回答，不生图。")
        report["batch_id"] = batch["id"]
        save()
        print(json.dumps(report, ensure_ascii=False), flush=True)
        deadline = monotonic() + 600
        while batch["status"] in {"queued", "researching"}:
            if monotonic() > deadline:
                raise TimeoutError("Live research did not finish in 600 seconds")
            sleep(2)
            batch = service.get(batch["id"])
        (root / "batch.json").write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8")
        assert batch["status"] == "ready", batch.get("note")
        assert len(batch["candidates"]) == 2, "Expected exactly two candidates for this probe"
        assert len(repo.list_topics("delivery-series")) == 0
        selected = list(reversed(batch["candidates"]))
        service.queue(batch["id"], [CandidateSelection(candidate_id=c["id"]) for c in selected],
                      request_id="delivery-live-select", origin="isolated-delivery-probe")
        topics = repo.list_topics("delivery-series")
        assert [t.title for t in topics] == [c["title"] for c in selected]
        assert all(c["angle"] in t.brief and c["sources"][0]["url"] in t.brief for c, t in zip(selected, topics))
        trace = service.root / "work" / batch["id"] / "1" / "codex_trace.jsonl"
        events = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
        searches = [e for e in events if e.get("type") == "item.completed" and e.get("item", {}).get("type") == "web_search"]
        report.update(status="passed", candidate_count=2, topics_before_selection=0, topics_after_selection=2,
                      selected_order=[c["id"] for c in selected], source_and_angle_preserved=True,
                      completed_web_events=len(searches), attempts=batch["attempts"], batch_status=batch["status"])
    except BaseException as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error))
        raise
    finally:
        service.shutdown()
        db.close()
        save()
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
