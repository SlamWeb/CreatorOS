"""Fresh-run state/snapshot contract; isolated SQLite/files, no model calls."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from sqlalchemy import func, select

from creatoros.integrations.producer_skills import ProducerSkillCatalog, skills_root_for
from creatoros.runs import ContentRunError, ContentRunService
from creatoros.storage import (ContentRepository, ContentRun, ContentRunStatus, Creator,
                              CreatorPlatform, Database, Series, Topic, TopicRemoval,
                              TopicSource, TopicStatus, upgrade_database)


class FreshContentRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        url = f"sqlite:///{(self.root / 'fresh.db').as_posix()}"
        upgrade_database(url)
        self.db = Database(url)
        self.addCleanup(self.db.close)
        self.catalog = ProducerSkillCatalog(skills_root_for(self.db))
        source = self.root / "skill"
        source.mkdir()
        (source / "assets").mkdir()
        (source / "SKILL.md").write_text(
            "---\nname: fresh-fixture\ndescription: Local fixture\n"
            "creatoros-output: social-content-pack.image-carousel\n---\nOLD_METHOD\n", encoding="utf-8")
        (source / "assets/reference.txt").write_text("OLD_ASSET", encoding="utf-8")
        self.skill_id = self.catalog.register_local(source, role="legacy_end_to_end")["id"]
        repo = ContentRepository(self.db)
        repo.create_creator(creator_id="creator", display_name="Local", platform=CreatorPlatform.XIAOHONGSHU)
        repo.create_series(series_id="series", creator_id="creator", name="Local", description="Local",
                           audience="Local", skill_name=self.skill_id)
        repo.add_topic(topic_id="topic", series_id="series", title="Same topic", source=TopicSource.MANUAL)
        self.runs = ContentRunService(self.db, output_root=self.root / "outputs")
        self.source = self.runs.create("topic")

    def settle(self, status=ContentRunStatus.FAILED):
        with self.db.session() as session:
            run = session.get(ContentRun, self.source.id)
            run.status = status
            run.retryable = status is ContentRunStatus.FAILED
        self.source = self.runs.get(self.source.id)
        return self.source

    def fresh(self, token="request-one", source=None, version=None):
        source = source or self.source
        return self.runs.restart_fresh(source.id, expected_version=version or source.version, request_id=token)

    def assert_error(self, code, action):
        with self.assertRaises(ContentRunError) as raised:
            action()
        self.assertEqual(raised.exception.code, code)

    def test_fresh_uses_current_skill_assets_no_previous_content_thread_or_checkpoint(self):
        old_prepared = self.runs._begin_attempt(self.source.id, owner_id="old-owner")
        self.runs._attach_thread(self.source.id, old_prepared["attempt_id"], "old-thread", "old-owner")
        old_directory = old_prepared["directory"]
        old_directory.mkdir(parents=True)
        (old_directory / "old-artifact.txt").write_text("keep", encoding="utf-8")
        self.settle()
        before = self.runs.get(self.source.id)
        old_events = [item.id for item in self.runs.repository.list_events(before.id)]
        skill = self.catalog.resolve(self.skill_id)
        (skill / "SKILL.md").write_text((skill / "SKILL.md").read_text(encoding="utf-8")
                                       .replace("OLD_METHOD", "NEW_METHOD"), encoding="utf-8")
        (skill / "assets/reference.txt").write_text("NEW_ASSET", encoding="utf-8")
        fresh = self.fresh()
        self.assertEqual(self.runs.create("topic").id, fresh.id)
        self.assertNotEqual(fresh.id, before.id)
        self.assertEqual(fresh.topic_id, before.topic_id)
        self.assertIsNone(fresh.producer_thread_id)
        self.assertNotEqual(fresh.input_snapshot_json["skill_digest"], before.input_snapshot_json["skill_digest"])
        prepared = self.runs._begin_attempt(fresh.id, owner_id="new-owner")
        self.assertEqual(prepared["revision_number"], 1)
        self.assertIsNone(prepared["instruction"])
        self.assertIsNone(prepared["thread_id"])
        self.assertIsNone(prepared["previous_pages"])
        self.assertNotEqual(prepared["directory"].parents[1], old_directory.parents[1])
        frozen = prepared["directory"].parents[1] / "skill-snapshot/skills/single"
        self.assertIn("NEW_METHOD", (frozen / "SKILL.md").read_text(encoding="utf-8"))
        self.assertEqual((frozen / "assets/reference.txt").read_text(encoding="utf-8"), "NEW_ASSET")
        old_frozen = old_directory.parents[1] / "skill-snapshot/skills/single"
        self.assertIn("OLD_METHOD", (old_frozen / "SKILL.md").read_text(encoding="utf-8"))
        self.assertEqual((old_directory / "old-artifact.txt").read_text(encoding="utf-8"), "keep")
        after = self.runs.get(before.id)
        self.assertEqual((after.status, after.version, after.producer_thread_id),
                         (before.status, before.version, before.producer_thread_id))
        self.assertEqual([item.id for item in self.runs.repository.list_events(before.id)], old_events)

    def test_request_replay_after_execution_returns_same_run_and_rejects_payload_mismatch(self):
        self.settle()
        with ThreadPoolExecutor(max_workers=4) as workers:
            ids = list(workers.map(lambda _: self.fresh().id, range(8)))
        self.assertEqual(len(set(ids)), 1)
        fresh = self.runs.get(ids[0])
        self.runs._begin_attempt(fresh.id, owner_id="new-owner")
        self.assertEqual(self.fresh().id, fresh.id)
        self.assert_error("idempotency_conflict", lambda: self.fresh(version=self.source.version + 1))
        with self.db.session() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(ContentRun)), 2)
            self.assertEqual(session.scalar(select(func.count()).select_from(Topic)), 1)

    def test_latest_run_required_and_old_mutations_do_not_corrupt_current_topic(self):
        self.settle()
        fresh = self.fresh()
        self.assert_error("run_superseded", lambda: self.fresh("new-request"))
        self.assert_error("run_superseded", lambda: self.runs.request_revision(
            self.source.id, "old revision", expected_version=self.source.version))
        self.assert_error("run_superseded", lambda: self.runs.cancel(
            self.source.id, expected_version=self.source.version))
        self.assert_error("run_superseded", lambda: self.runs._begin_attempt(self.source.id, owner_id="old"))
        with self.db.session() as session:
            self.assertEqual(session.get(Topic, "topic").status, TopicStatus.QUEUED)
            run = session.get(ContentRun, fresh.id)
            run.status = ContentRunStatus.INTERRUPTED
        latest = self.runs.get(fresh.id)
        self.assertNotEqual(self.fresh("next-request", latest).id, fresh.id)

    def test_version_and_active_status_guards(self):
        for status in (ContentRunStatus.QUEUED, ContentRunStatus.PRODUCING, ContentRunStatus.VALIDATING):
            self.settle(status)
            self.assert_error("run_active", lambda: self.fresh(status.value))
        self.settle()
        self.assert_error("version_conflict", lambda: self.fresh(version=self.source.version + 1))
        self.assert_error("invalid_request", lambda: self.fresh(""))
        self.assert_error("invalid_request", lambda: self.fresh("x" * 129))

    def test_current_binding_is_resolved_not_source_binding(self):
        self.settle()
        with self.db.session() as session:
            session.get(Series, "series").skill_name = "knowledge-to-carousel"
        fresh = self.fresh()
        self.assertEqual(fresh.input_snapshot_json["skill_name"], "knowledge-to-carousel")
        self.assertEqual(self.runs.get(self.source.id).input_snapshot_json["skill_name"], self.skill_id)

    def test_finished_source_statuses_allow_new_clean_runs(self):
        for index, status in enumerate((ContentRunStatus.AWAITING_APPROVAL, ContentRunStatus.FAILED,
                                       ContentRunStatus.INTERRUPTED, ContentRunStatus.APPROVED,
                                       ContentRunStatus.CANCELLED)):
            with self.subTest(status=status.value):
                self.settle(status)
                fresh = self.fresh(f"finished-{index}")
                self.assertEqual(fresh.status, ContentRunStatus.QUEUED)
                self.assertIsNone(fresh.approved_revision_id)
                self.assertIsNone(fresh.approved_artifact_digest)
                self.assertEqual(self.runs.get(self.source.id).status, status)
                self.source = fresh

    def test_latest_dual_skill_binding_freezes_both_current_assets(self):
        self.settle()
        ids = {}
        for role in ("mind", "production"):
            directory = self.root / role
            directory.mkdir()
            (directory / "assets").mkdir()
            (directory / "SKILL.md").write_text(
                f"---\nname: fresh-{role}\ndescription: Local {role}\n---\nNEW_{role}\n", encoding="utf-8")
            (directory / "assets/reference.txt").write_text(f"NEW_{role}_ASSET", encoding="utf-8")
            ids[role] = self.catalog.register_local(directory, role=role)["id"]
        with self.db.session() as session:
            series = session.get(Series, "series")
            series.skill_name = None
            series.mind_skill_id, series.production_skill_id = ids["mind"], ids["production"]
        fresh = self.fresh()
        self.assertIsNone(fresh.input_snapshot_json["skill_name"])
        self.assertEqual(fresh.input_snapshot_json["composition"]["mind"]["id"], ids["mind"])
        prepared = self.runs._begin_attempt(fresh.id, owner_id="new-owner")
        for role in ids:
            frozen = prepared["directory"].parents[1] / "skill-snapshot/skills" / role
            self.assertEqual((frozen / "assets/reference.txt").read_text(encoding="utf-8"), f"NEW_{role}_ASSET")
        self.assertIsNone(prepared["previous_pages"])
        self.assertIsNone(prepared["thread_id"])

    def test_request_token_cannot_be_reused_for_different_topic(self):
        self.settle()
        self.fresh()
        repo = ContentRepository(self.db)
        repo.add_topic(topic_id="another", series_id="series", title="Another topic", source=TopicSource.MANUAL)
        other = self.runs.create("another")
        with self.db.session() as session:
            session.get(ContentRun, other.id).status = ContentRunStatus.FAILED
        other = self.runs.get(other.id)
        self.assert_error("idempotency_conflict", lambda: self.fresh(source=other))

    def test_disabled_removed_and_external_import_guards(self):
        self.settle()
        with self.db.session() as session:
            session.get(Series, "series").is_active = False
        self.assert_error("conflict", self.fresh)
        with self.db.session() as session:
            session.get(Series, "series").is_active = True
            session.get(Creator, "creator").is_active = False
        self.assert_error("conflict", self.fresh)
        with self.db.session() as session:
            session.get(Creator, "creator").is_active = True
            run = session.get(ContentRun, self.source.id)
            run.input_snapshot_json = {**run.input_snapshot_json, "production_protocol": "external-import"}
        self.source = self.runs.get(self.source.id)
        self.assert_error("external_import_readonly", self.fresh)
        with self.db.session() as session:
            session.add(TopicRemoval(topic_id="topic", series_id="series", creator_id="creator",
                                     request_id="remove-local", status="archived"))
        self.assert_error("topic_removed", self.fresh)


if __name__ == "__main__":
    unittest.main()
