"""Isolated SQLite/catalog/HTTP research context contracts; no model calls."""
import json
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

from creatoros.storage import ContentRepository, Database, Series, upgrade_database
from creatoros.integrations.producer_skills import ProducerSkillCatalog, SkillInstallService, _write
from creatoros.integrations.skill_library import SkillLibraryService
from creatoros.integrations.topic_research import CodexTopicResearcher, TopicResearchService, ResearchReceipt
from creatoros.integrations.codex import CodexUsage
from tests.agent_studio_support import serve


class NoResearch:
    def research(self, *_args, **_kwargs):
        raise AssertionError("This snapshot test must never call a model")


class ResearchSkillContextTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.url = "sqlite:///" + (self.root / "research.db").as_posix()
        upgrade_database(self.url)
        self.db = Database(self.url)
        self.addCleanup(self.db.close)
        self.repo = ContentRepository(self.db)
        for owner in ("a", "b"):
            self.repo.create_creator(creator_id=owner, display_name=owner)
            self.repo.create_series(series_id=owner, creator_id=owner, name=owner,
                                    description="栏目自己的资料", audience="本账号读者", skill_name=None)
        self.catalog = ProducerSkillCatalog(self.root / "catalog")
        self.library = SkillLibraryService(self.db, self.catalog)
        self.service = TopicResearchService(self.db, self.catalog, researcher=NoResearch())
        self.addCleanup(self.service.shutdown)
        self.visual = self.register("visual-context", "production")
        self.mind = self.register("mind-context", "mind")
        for skill in (self.visual, self.mind):
            self.library.add("a", skill["id"], "account-" + skill["name"])

    def register(self, name, role):
        source = self.root / name
        (source / "examples").mkdir(parents=True)
        (source / "SKILL.md").write_text(f"---\nname: {name}\ndescription: context test\ncreatoros-outputs: [image]\n---\n"
            f"完整正文-{name} [样例](examples/example.md)\n", encoding="utf-8")
        (source / "examples" / "example.md").write_text("完整样例-" + name, encoding="utf-8")
        return self.catalog.register_local(source, role=role)

    def pool(self, defaults):
        current = self.library.get_series("a")
        return self.library.set_series("a", current["version"], [self.visual["id"], self.mind["id"]],
                                       {"image": defaults, "video": []})

    def test_unconfigured_series_has_explicitly_empty_skill_context(self):
        snapshot = self.service.snapshot("a")
        self.assertEqual(snapshot["skill_context"], {"source": "none", "skills": []})
        self.assertEqual(snapshot["skill_text"], "")
        self.assertIsNone(snapshot["skill_digest"])

    def test_explicit_image_defaults_include_each_full_skill_in_declared_order(self):
        self.pool([{"skill_id": self.visual["id"], "role": "primary"},
                   {"skill_id": self.mind["id"], "role": "supporting"}])
        snapshot = self.service.snapshot("a")
        selected = snapshot["skill_context"]["skills"]
        self.assertEqual(snapshot["skill_context"]["source"], "image_defaults")
        self.assertEqual([item["skill_id"] for item in selected], [self.visual["id"], self.mind["id"]])
        self.assertEqual([item["role"] for item in selected], ["primary", "supporting"])
        for item, skill in zip(selected, (self.visual, self.mind)):
            self.assertIn("完整正文-" + skill["name"], item["skill_text"])
            self.assertEqual(item["skill_examples"]["files"][0]["text"], "完整样例-" + skill["name"])
            self.assertEqual(len(item["digest"]), 64)

    def test_pool_without_defaults_never_reads_any_pool_or_old_binding_skill(self):
        with self.db.session() as session:
            session.get(Series, "a").skill_name = "knowledge-to-carousel"
        self.pool([])
        with patch.object(self.catalog, "resolve", side_effect=AssertionError("old binding must not be read")), \
                patch.object(self.catalog, "locate", side_effect=AssertionError("no arbitrary pool Skill")):
            snapshot = self.service.snapshot("a")
        self.assertEqual(snapshot["skill_context"], {"source": "none", "skills": []})

    def test_defaults_order_and_identity_affect_new_requests_but_unselected_pool_does_not(self):
        defaults = [{"skill_id": self.visual["id"], "role": "primary"},
                    {"skill_id": self.mind["id"], "role": "supporting"}]
        self.pool(defaults)
        before = self.service.snapshot("a")
        self.pool(defaults)  # A pool version increment with identical actual input.
        self.assertTrue(self.service._same_config(before, self.service.snapshot("a")))
        self.pool(list(reversed(defaults)))
        self.assertFalse(self.service._same_config(before, self.service.snapshot("a")))
        changed = deepcopy(before)
        changed["skill_context"]["skills"][1]["digest"] = "0" * 64
        self.assertFalse(self.service._same_config(before, changed))
        transferred = deepcopy(before)
        transferred["creator"]["id"] = "b"
        self.assertFalse(self.service._same_config(before, transferred))

    def test_legacy_single_and_mind_keep_original_context_and_old_comparison(self):
        with self.db.session() as session:
            session.get(Series, "a").skill_name = "knowledge-to-carousel"
        snapshot = self.service.snapshot("a")
        first = snapshot["skill_context"]["skills"][0]
        self.assertEqual(snapshot["skill_context"]["source"], "legacy")
        self.assertEqual(first["skill_text"], snapshot["skill_text"])
        self.assertEqual(first["digest"], snapshot["skill_digest"])
        old = deepcopy(snapshot)
        old.pop("skill_context")
        self.assertTrue(self.service._same_config(old, snapshot))
        with self.db.session() as session:
            series = session.get(Series, "a")
            series.skill_name, series.mind_skill_id, series.production_skill_id = None, self.mind["id"], self.visual["id"]
        snapshot = self.service.snapshot("a")
        self.assertEqual([skill["skill_id"] for skill in snapshot["skill_context"]["skills"]], [self.mind["id"]])
        self.assertIn("完整正文-mind-context", snapshot["skill_text"])

    def test_unselected_pool_bytes_and_membership_are_not_research_input(self):
        defaults = [{"skill_id": self.visual["id"], "role": "primary"}]
        self.pool(defaults)
        before = self.service.snapshot("a")
        unused = self.catalog.locate(self.mind["id"]) / "SKILL.md"
        unused.write_text("Unselected content changed", encoding="utf-8")
        current = self.library.get_series("a")
        self.library.set_series("a", current["version"], [self.visual["id"]], {"image": defaults, "video": []})
        self.assertTrue(self.service._same_config(before, self.service.snapshot("a")))
        selected = self.catalog.locate(self.visual["id"]) / "SKILL.md"
        selected.write_text(selected.read_text(encoding="utf-8") + "Changed selected content", encoding="utf-8")
        after = self.service.snapshot("a")
        self.assertFalse(self.service._same_config(before, after))
        self.assertIn("Changed selected content", after["skill_context"]["skills"][0]["skill_text"])

    def test_transferred_pool_rejects_foreign_bytes_before_loading_them(self):
        self.pool([{"skill_id": self.visual["id"], "role": "primary"}])
        with self.db.session() as session:
            session.get(Series, "a").creator_id = "b"
        with patch.object(self.catalog, "locate", side_effect=AssertionError("must not read other account's Skill")):
            with self.assertRaisesRegex(ValueError, "当前账号"):
                self.service.snapshot("a")

    def test_old_active_legacy_request_is_reused_without_upgrading_its_saved_snapshot(self):
        with self.db.session() as session:
            session.get(Series, "a").skill_name = "knowledge-to-carousel"
        with patch.object(self.service, "_kick"):
            first = self.service.submit("a", 1, "same request")
            record = self.service._load(first["id"])
            record["snapshot"].pop("skill_context")
            _write(self.service._path(first["id"]), record)
            before = self.service._path(first["id"]).read_bytes()
            second = self.service.submit("a", 1, " same   request ")
            self.assertEqual(first["id"], second["id"])
            self.assertEqual(self.service._path(first["id"]).read_bytes(), before)

    def test_prompt_contains_all_full_sources_once_and_explicitly_explains_absent_skill(self):
        self.pool([{"skill_id": self.visual["id"], "role": "primary"},
                   {"skill_id": self.mind["id"], "role": "supporting"}])
        researcher = CodexTopicResearcher()
        captured = []
        async def local(prompt, *_args, **_kwargs):
            captured.append(prompt)
            return "controlled-no-sdk"
        for number, snapshot in enumerate((self.service.snapshot("a"), self.service.snapshot("b"))):
            workspace = self.root / f"prompt-{number}"
            workspace.mkdir()
            with patch.object(researcher, "preflight"), patch.object(researcher, "_research_async", side_effect=local):
                self.assertEqual(researcher.research(snapshot, 1, "只调研当前栏目", workspace, Event()), "controlled-no-sdk")
        for skill in (self.visual, self.mind):
            self.assertEqual(captured[0].count("完整正文-" + skill["name"]), 1)
            self.assertEqual(captured[0].count("完整样例-" + skill["name"]), 1)
            self.assertNotIn("完整正文-" + skill["name"], captured[1])
        self.assertIn("没有提供任何 Skill 正文或样例", captured[1])

    def app(self):
        from creatoros.web.app import create_app
        from creatoros.runs import ContentRunService
        return create_app(database=self.db, topic_research_service=self.service,
            run_service=ContentRunService(self.db, output_root=self.root / "outputs"),
            skill_install_service=SkillInstallService(self.catalog), chat_root=self.root / "chats",
            eval_root=self.root / "eval", defer_background_start=True)

    def test_real_http_active_dedup_uses_actual_default_context_and_owner_scope(self):
        started, release = Event(), Event()
        snapshots = []
        class Controlled:
            def research(self, snapshot, *_args):
                snapshots.append(snapshot)
                started.set()
                if not release.wait(20):
                    raise RuntimeError("controlled test release timed out")
                return SimpleNamespace(receipt=ResearchReceipt(candidates=[], note="local controlled result"),
                                       thread_id="controlled-local", usage=CodexUsage())
        self.service.researcher = Controlled()
        app = self.app()
        sid = app.state.chat.create("a")["id"]
        headers = {"x-creatoros-origin": "agent", "x-creatoros-agent-session": sid}
        with serve(app) as base, httpx.Client(base_url=base, timeout=15, trust_env=False) as client:
            try:
                self.assertEqual(client.post("/api/series/b/topic-research", headers=headers, json={"count": 1}).status_code, 403)
                first = client.post("/api/series/a/topic-research", headers=headers, json={"count": 1, "instructions": "当前资料"})
                self.assertEqual(first.status_code, 202, first.text)
                self.assertTrue(started.wait(10))
                same = client.post("/api/series/a/topic-research", headers=headers, json={"count": 1, "instructions": " 当前资料 "})
                self.assertEqual(first.json()["id"], same.json()["id"])
                self.pool([{"skill_id": self.visual["id"], "role": "primary"}])
                changed = client.post("/api/series/a/topic-research", headers=headers, json={"count": 1, "instructions": "当前资料"})
                self.assertEqual(changed.status_code, 202, changed.text)
                self.assertNotEqual(first.json()["id"], changed.json()["id"])
            finally:
                release.set()
                if self.service.worker:
                    self.service.worker.join(20)
            self.assertEqual(client.get("/api/topic-research/" + changed.json()["id"]).json()["status"], "ready")
            self.assertEqual(snapshots[0]["skill_context"]["source"], "none")
            self.assertTrue(any(item["skill_context"]["source"] == "image_defaults" for item in snapshots[1:]))
            self.assertTrue(all(item["creator"]["id"] == "a" for item in snapshots))
            self.assertFalse(self.repo.list_topics("a"))

    def test_historical_candidates_queue_after_default_changes_without_rewriting_evidence(self):
        record = {"id": "a" * 32, "series_id": "a", "count": 1, "instructions": "", "status": "ready",
            "attempt": 1, "created_at": "2020-01-01T00:00:00+00:00", "snapshot": self.service.snapshot("a"),
            "candidates": [{"id": "c1", "title": "保留的旧候选", "angle": "旧切入点", "rationale": "旧理由",
                "sources": [{"title": "原来源", "url": "https://example.invalid/source"}]}], "note": "saved fixture", "attempts": []}
        _write(self.service._path(record["id"]), record)
        before = self.service._path(record["id"]).read_bytes()
        self.pool([{"skill_id": self.visual["id"], "role": "primary"}])
        app = self.app()
        with serve(app) as base, httpx.Client(base_url=base, timeout=15, trust_env=False) as client:
            endpoint = "/api/topic-research/" + record["id"]
            self.assertFalse(client.get(endpoint).json()["stale"])
            queued = client.post(endpoint + "/queue", json={"request_id": "choose-original-candidate", "selections": [{"candidate_id": "c1"}]})
            self.assertEqual(queued.status_code, 201, queued.text)
            self.assertEqual(self.repo.list_topics("a")[0].title, "保留的旧候选")
            self.assertIn("旧切入点", self.repo.list_topics("a")[0].brief)
            self.assertEqual(self.service._path(record["id"]).read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
