"""Isolated SQLite and local failure injection; never calls a paid model."""
import json
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from creatoros.integrations.codex import CodexSdkProducer
from creatoros.runs import ContentRunError, ContentRunService
from creatoros.runs.offline_adoption import adopt_completed_replay, inspect_source, _tree_hashes
from creatoros.storage import (ContentAttempt, ContentAttemptStatus, ContentRevision, ContentRun,
    ContentRunStatus, Creator, Database, Series, Topic, TopicSource, upgrade_database)
from creatoros.storage.models import ProductionQueueItem
from tests.smoke_native_production import NativeTransport, create_pair, producer_request


class OfflineAdoptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        catalog, self.pair = create_pair(self.root)
        self.source = self.root / "replay/revision-001/attempt-001"
        producer = CodexSdkProducer(project_root=self.root, generated_images_root=self.root / "generated")
        NativeTransport.reset(producer.generated_images_root)
        with patch("openai_codex.AsyncCodex", NativeTransport):
            producer_request(producer, self.source, self.pair, catalog.root)
        # The shared controlled transport reuses one turn id for both phases;
        # record a distinct completed production turn for this replay fixture.
        receipt_path = self.source / "worker_receipt.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf8"))
        receipt["turns"].append({"id": "production-turn", "phase": "production", "status": "completed"})
        receipt_path.write_text(json.dumps(receipt), encoding="utf8")
        url = "sqlite:///" + (self.root / "test.db").as_posix()
        upgrade_database(url)
        self.db = Database(url)
        self.addCleanup(self.db.close)
        self.service = ContentRunService(self.db, output_root=self.root / "outputs",
            producer_factory=lambda: self.fail("offline adoption constructed a producer"))
        self.original = self.root / "outputs/creator-test/series-test/run/revision-001/attempt-001"
        shutil.copytree(self.source, self.original)
        self.data = {"production_protocol": "native-v1", "creator_id": "creator-test",
            "series_id": "series-test", "series_name": "Test", "topic_id": "topic-test",
            "topic_title": "Native production", "topic_brief": "A scoped smoke test",
            "composition": self.pair.model_dump(mode="json")}
        with self.db.session() as session:
            session.add(Creator(id="creator-test", display_name="Test", platform="xiaohongshu"))
            session.add(Series(id="series-test", creator_id="creator-test", name="Test", description="",
                audience="", skill_name=None, mind_skill_id="mind", production_skill_id="maker"))
            session.add(Topic(id="topic-test", series_id="series-test", title="Native production",
                source=TopicSource.MANUAL, position=1))
            session.flush()
            session.add(ContentRun(id="run", topic_id="topic-test", idempotency_key="run", status=ContentRunStatus.FAILED,
                input_snapshot_json=self.data, error_type="skill_composition_needs_input", error_message="old failure", retryable=False))
            session.flush()
            session.add(ContentRevision(id="revision", content_run_id="run", revision_number=1, production_input_json=self.data))
            session.flush()
            session.add(ContentAttempt(id="old", revision_id="revision", attempt_number=1,
                status=ContentAttemptStatus.FAILED, output_directory=str(self.original),
                error_type="skill_composition_needs_input", error_message="old failure", retryable=False))
            session.add(ProductionQueueItem(run_id="run", revision_id="revision", expected_version=1, status="failed"))
        self.before = _tree_hashes(self.original)
        self.inspect = inspect_source(self.service, "run", self.source, 1)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def adopt(self, **changes):
        options = {"expected_version": self.service.get("run").version,
            "expected_fingerprint": self.inspect["fingerprint"], "expected_images": 1, **changes}
        with self.service.guard:
            return adopt_completed_replay(self.service, "run", self.source, **options)

    def test_adoption_same_revision_preserves_failed_attempt_and_is_idempotent(self):
        result = self.adopt()
        self.assertEqual(result.status, "awaiting_approval")
        self.assertEqual(self.service.get("run").active_revision_number, 1)
        attempts = self.service.repository.list_attempts("revision")
        self.assertEqual([a.status.value for a in attempts], ["failed", "succeeded"])
        self.assertFalse(attempts[0].retryable)
        self.assertEqual(attempts[0].error_message, "old failure")
        self.assertEqual(attempts[1].usage_json["input_tokens"], 0)
        self.assertEqual(_tree_hashes(self.original), self.before)
        from creatoros.runs.artifacts import validate_artifact
        self.assertEqual(validate_artifact(result.artifact_directory, composition=self.pair,
            production_protocol="native-v1").card_count, 1)
        events = self.service.repository.list_events("run")
        self.assertEqual([e.event_type.value for e in events], ["resumed", "produced", "validated"])
        self.assertEqual(self.adopt(expected_version=1).attempt_id, result.attempt_id)
        self.assertEqual(len(self.service.repository.list_events("run")), 3)
        with self.db.session() as session:
            from sqlalchemy import select
            self.assertEqual(session.scalar(select(ProductionQueueItem)).status, "succeeded")

    def test_stale_version_rejected(self):
        with self.assertRaises(ContentRunError):
            self.adopt(expected_version=99)

    def test_source_changes_rejected(self):
        (self.source / "extra.txt").write_text("changed", encoding="utf8")
        with self.assertRaises(ContentRunError):
            self.adopt()

    def test_sdk_receipt_must_be_completed(self):
        path = self.source / "worker_receipt.json"
        receipt = json.loads(path.read_text())
        receipt["turns"] = []
        path.write_text(json.dumps(receipt))
        with self.assertRaises(ValueError):
            self.adopt()

    def test_wrong_count_rejected(self):
        with self.assertRaises(ValueError):
            self.adopt(expected_images=8)

    def test_pending_task_rejected(self):
        with self.db.session() as session:
            from sqlalchemy import select
            session.scalar(select(ProductionQueueItem)).status = "pending"
        with self.assertRaises(ContentRunError):
            self.adopt()

    def test_superseded_run_rejected(self):
        with self.db.session() as session:
            session.add(ContentRun(id="new", topic_id="topic-test", idempotency_key="new",
                status=ContentRunStatus.QUEUED, input_snapshot_json=self.data))
        with self.assertRaises(ContentRunError) as error:
            self.adopt()
        self.assertEqual(error.exception.code, "run_superseded")

    def test_cancelled_run_and_lease_rejected(self):
        for values in ({"status": ContentRunStatus.CANCELLED},
                       {"status": ContentRunStatus.FAILED, "lease_owner": "another-worker"}):
            with self.db.session() as session:
                run = session.get(ContentRun, "run")
                for key, value in values.items():
                    setattr(run, key, value)
            with self.assertRaises(ContentRunError):
                self.adopt()

    def test_topic_removed_rejected(self):
        from creatoros.storage import TopicRemoval
        with self.db.session() as session:
            session.add(TopicRemoval(topic_id="topic-test", series_id="series-test",
                creator_id="creator-test", request_id="remove-topic", status="archived"))
        with self.assertRaises(ContentRunError):
            self.adopt()

    def test_wrong_manifest_owner_rejected(self):
        path = self.source / "social_content_pack.json"
        value = json.loads(path.read_text(encoding="utf8"))
        value["topic_id"] = "another-topic"
        path.write_text(json.dumps(value), encoding="utf8")
        with self.assertRaises(ValueError):
            self.adopt()

    def test_requires_global_guard(self):
        with self.assertRaises(RuntimeError):
            adopt_completed_replay(self.service, "run", self.source, expected_version=1,
                expected_fingerprint=self.inspect["fingerprint"], expected_images=1)

    def test_copy_failure_leaves_evidence_without_database_success(self):
        with patch("creatoros.runs.offline_adoption._copy_relocated", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.adopt()
        self.assertEqual(self.service.get("run").status, ContentRunStatus.FAILED)
        self.assertEqual(len(self.service.repository.list_attempts("revision")), 1)
        self.assertEqual(_tree_hashes(self.original), self.before)
        self.assertTrue((self.original.parent / "attempt-002/offline_adoption_error.txt").is_file())


if __name__ == "__main__":
    unittest.main()
