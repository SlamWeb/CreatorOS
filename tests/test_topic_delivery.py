"""Topic result fixtures: real SQLite/files, controlled research output, no model calls."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from uuid import uuid4

from creatoros.integrations.codex import CodexUsage
from creatoros.integrations.producer_skills import ProducerSkillCatalog, skills_root_for
from creatoros.integrations.topic_research import TopicResearchService, ResearchReceipt, CandidateSelection, _write
from creatoros.storage import Database, upgrade_database, Creator, Series, CreatorPlatform, ContentRepository
from tests.smoke_topic_research import seed_batch


def candidate(title):
    return {"title": title, "angle": "中文切入点，保留原文。", "rationale": "适合初学者",
            "sources": [{"title": "Python官方文档", "url": "https://docs.python.org/3/library/json.html"}]}


class ControlledReceipt:
    def __init__(self, payload):
        self.payload = payload

    def research(self, snapshot, count, instructions, workspace, cancel):
        # Injection is at the researcher result boundary, not a live-model claim.
        receipt = ResearchReceipt.model_validate_json(json.dumps(self.payload, ensure_ascii=False))
        return SimpleNamespace(receipt=receipt, thread_id="fixture-thread", usage=CodexUsage())


class TopicDeliveryTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix="topic-delivery-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        url = "sqlite:///" + (self.root / "test.sqlite").as_posix()
        upgrade_database(url)
        self.db = Database(url)
        self.addCleanup(self.db.close)
        with self.db.session() as session:
            session.add(Creator(id="c", display_name="隔离账号", platform=CreatorPlatform.XIAOHONGSHU))
            session.flush()
            session.add(Series(id="s", creator_id="c", name="栏目", description="测试", audience="初学者",
                               skill_name="knowledge-to-carousel"))
        self.service = TopicResearchService(self.db, ProducerSkillCatalog(skills_root_for(self.db)))
        self.repo = ContentRepository(self.db)

    def deliver(self, payload):
        self.service.researcher = ControlledReceipt(payload)
        record = seed_batch(self.service, "s", uuid4().hex)
        record.update(status="researching", candidates=[],
                      progress={"stage": "researching", "last_activity_at": None, "events": []})
        _write(self.service._path(record["id"]), record)
        self.service._run(record["id"])
        return self.service.get(record["id"])

    def valid_batch(self):
        return self.deliver({"candidates": [candidate("第一题"), candidate("第二题")], "note": "隔离数据"})

    def test_candidates_persist_without_queue_and_selection_preserves_order_and_sources(self):
        batch = self.valid_batch()
        self.assertEqual(batch["status"], "ready")
        self.assertEqual([c["id"] for c in batch["candidates"]], ["c1", "c2"])
        self.assertFalse(self.repo.list_topics("s"))
        self.service.queue(batch["id"], [CandidateSelection(candidate_id="c2"), CandidateSelection(candidate_id="c1")],
                           request_id="select", origin="fixture")
        topics = self.repo.list_topics("s")
        self.assertEqual([t.title for t in topics], ["第二题", "第一题"])
        self.assertTrue(all("中文切入点" in t.brief and "https://docs.python.org/3/library/json.html" in t.brief for t in topics))

    def test_invalid_candidate_rejects_entire_receipt_without_partial_queue(self):
        invalid = candidate("bad")
        del invalid["angle"]
        batch = self.deliver({"candidates": [candidate("good"), invalid], "note": "invalid"})
        self.assertEqual(batch["status"], "failed")
        self.assertEqual(batch["candidates"], [])
        self.assertFalse(self.repo.list_topics("s"))
        with self.assertRaises(ValueError):
            self.service.queue(batch["id"], [CandidateSelection(candidate_id="c1")], request_id="fail", origin="fixture")

    def test_bad_source_and_wrong_field_type_fail_without_topics(self):
        for change in ({"sources": [{"title": "bad", "url": "javascript:alert(1)"}]},
                       {"sources": []}, {"angle": 42}):
            with self.subTest(change=change):
                item = candidate("invalid") | change
                batch = self.deliver({"candidates": [item], "note": "invalid"})
                self.assertEqual(batch["status"], "failed")
                self.assertEqual(batch["candidates"], [])
                self.assertFalse(self.repo.list_topics("s"))

    def test_duplicate_selection_and_unknown_candidate_are_rejected(self):
        batch = self.valid_batch()
        for ids in (("c1", "c1"), ("c1", "c99")):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                self.service.queue(batch["id"], [CandidateSelection(candidate_id=i) for i in ids],
                                   request_id="invalid", origin="fixture")
            self.assertFalse(self.repo.list_topics("s"))

    def test_repeated_selection_does_not_duplicate_topic_or_modify_batch(self):
        batch = self.valid_batch()
        before = self.service._path(batch["id"]).read_bytes()
        selection = [CandidateSelection(candidate_id="c1")]
        self.service.queue(batch["id"], selection, request_id="one", origin="fixture")
        with self.assertRaises(ValueError):
            self.service.queue(batch["id"], selection, request_id="two", origin="fixture")
        self.assertEqual(len(self.repo.list_topics("s")), 1)
        self.assertEqual(self.service._path(batch["id"]).read_bytes(), before)

    def test_empty_valid_research_is_ready_but_creates_no_topics(self):
        batch = self.deliver({"candidates": [], "note": "未找到合适来源，不凑数。"})
        self.assertEqual(batch["status"], "ready")
        self.assertEqual(batch["candidates"], [])
        self.assertFalse(self.repo.list_topics("s"))


if __name__ == "__main__":
    unittest.main()
