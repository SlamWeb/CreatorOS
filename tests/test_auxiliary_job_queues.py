"""Persistent install/discussion queues: controlled faults, real local files/SQLite."""
import hashlib
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch

from creatoros.integrations.content_discussion import ContentDiscussionService
from creatoros.integrations.producer_skills import ProducerSkillCatalog, SkillInstallService, _write
from tests.smoke_producer_skills import fixture as install_fixture
from tests.studio_review_fixtures import make_fixture


class InstallerQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.calls = []
        self.catalog = ProducerSkillCatalog(Path(self.tmp.name) / "skills")
        calls = self.calls

        class Installer:
            def install(self, url, workspace, cancel):
                calls.append(url)
                receipt = install_fixture(workspace)
                subprocess.run(["git", "-C", str(workspace / "source"), "remote", "set-url", "origin", url],
                               check=True, capture_output=True)
                return receipt

        self.installer = Installer()
        self.service = SkillInstallService(self.catalog, self.installer)
        self.addCleanup(self.service.shutdown)

    def test_restart_preserves_pending_and_does_not_repeat_started_install(self):
        with patch.object(self.service, "_kick"):
            first = self.service.submit("https://github.com/example/one")
            second = self.service.submit("https://github.com/example/two")
        _write(self.service._path(first["id"]), {**first, "status": "installing"})
        (self.catalog.root / "jobs" / "broken-history.json").write_text("{bad", encoding="utf-8")
        self.service.shutdown()
        restarted = SkillInstallService(self.catalog, self.installer)
        self.addCleanup(restarted.shutdown)
        restarted.start()
        restarted.thread.join(10)
        self.assertEqual(self.calls, ["https://github.com/example/two"])
        self.assertEqual(restarted.get(first["id"])["status"], "interrupted")
        self.assertEqual(restarted.get(second["id"])["status"], "installed")

    def test_recovery_write_failure_does_not_block_other_queued_install(self):
        with patch.object(self.service, "_kick"):
            first = self.service.submit("https://github.com/example/one")
            second = self.service.submit("https://github.com/example/two")
        _write(self.service._path(first["id"]), {**first, "status": "installing"})
        original_bytes = self.service._path(first["id"]).read_bytes()
        self.service.shutdown()
        restarted = SkillInstallService(self.catalog, self.installer)
        self.addCleanup(restarted.shutdown)

        def deny_one_recovery(path, value):
            if Path(path) == restarted._path(first["id"]) and value.get("status") == "interrupted":
                raise PermissionError("injected recovery write failure")
            return _write(path, value)

        with self.assertLogs("creatoros.integrations.producer_skills", level="ERROR"), \
             patch("creatoros.integrations.producer_skills._write", side_effect=deny_one_recovery):
            restarted.start()
            restarted.thread.join(10)
        self.assertFalse(restarted.thread.is_alive())
        self.assertEqual(self.calls, ["https://github.com/example/two"])
        self.assertEqual(restarted.get(second["id"])["status"], "installed")
        self.assertEqual(restarted._path(first["id"]).read_bytes(), original_bytes)

    def test_failed_install_does_not_block_next_and_thread_start_failure_is_retryable(self):
        original = self.installer.install

        def install(url, workspace, cancel):
            if url.endswith("/one"):
                self.calls.append(url)
                raise RuntimeError("injected download failure")
            return original(url, workspace, cancel)

        self.installer.install = install
        with patch("creatoros.integrations.producer_skills.Thread.start", side_effect=RuntimeError("no thread")):
            first = self.service.submit("https://github.com/example/one")
            second = self.service.submit("https://github.com/example/two")
        self.assertEqual(first["status"], "queued")
        self.assertEqual(second["status"], "queued")
        self.assertFalse(self.service._draining)
        self.service.submit("https://github.com/example/two")  # same id wakes, not duplicates
        self.service.thread.join(10)
        self.assertEqual(self.calls, ["https://github.com/example/one", "https://github.com/example/two"])
        self.assertEqual(self.service.get(first["id"])["status"], "failed")
        self.assertEqual(self.service.get(second["id"])["status"], "installed")

    def test_shutdown_interrupts_only_current_install_and_keeps_following_job(self):
        started = Event()
        original = self.installer.install

        def install(url, workspace, cancel):
            if url.endswith("/one"):
                self.calls.append(url)
                started.set()
                cancel.wait(8)
                raise RuntimeError("stopped")
            return original(url, workspace, cancel)

        self.installer.install = install
        first = self.service.submit("https://github.com/example/one")
        self.assertTrue(started.wait(5))
        second = self.service.submit("https://github.com/example/two")
        self.service.shutdown()
        self.assertEqual(self.service.get(first["id"])["status"], "interrupted")
        self.assertEqual(self.service.get(second["id"])["status"], "queued")
        restarted = SkillInstallService(self.catalog, self.installer)
        self.addCleanup(restarted.shutdown)
        restarted.start()
        restarted.thread.join(10)
        self.assertEqual(self.calls, ["https://github.com/example/one", "https://github.com/example/two"])
        self.assertEqual(restarted.get(second["id"])["status"], "installed")


class DiscussionQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db, self.runs, self.producer, self.app = make_fixture(self.root)
        self.addCleanup(self.db.close)
        self.run = self.runs.create("review-1")
        self.runs.execute(self.run.id)
        self.service = self.app.state.content_discussions
        self.addCleanup(self.service.shutdown)
        self.calls = []
        calls = self.calls

        class Reviewer:
            def review(self, workspace, prompt, images, thread_id, source, cancel, emit, bind):
                calls.append((thread_id, source))
                bind(thread_id or "isolated-discussion", "continued_discussion" if thread_id else "forked_production")
                return "isolated result", {"input_tokens": 1}

        self.reviewer = Reviewer()
        self.service.reviewer = self.reviewer
        self.before = self.app.state.queries.get_run(self.run.id).model_dump(mode="json")
        revision = self.before["revisions"][0]
        self.args = {"revision_id": revision["id"], "artifact_digest": revision["review_digest"], "message": "test"}
        root, _, _, _ = self.service.artifacts.locate(self.run.id, revision["id"])
        self.original_files = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in root.rglob("*") if p.is_file()}

    def submit(self, request_id):
        return self.service.submit(self.run.id, request_id=request_id, **self.args)

    def join(self, service=None):
        service = service or self.service
        service.worker.join(10)
        self.assertFalse(service.worker.is_alive())

    def unchanged(self):
        self.assertEqual(self.app.state.queries.get_run(self.run.id).model_dump(mode="json"), self.before)
        self.assertTrue(all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == digest
                            for p, digest in self.original_files.items()))

    def test_restart_only_runs_pending_and_preserves_original_artifact_and_run(self):
        with patch.object(self.service, "_kick"):
            first, second = self.submit("first"), self.submit("second")
        self.service._save({**first, "status": "running"})
        (self.service.root / "records" / "broken-history.json").write_text("{bad", encoding="utf-8")
        self.service.shutdown()
        restarted = ContentDiscussionService(self.db, self.service.artifacts, self.service.root, self.reviewer)
        self.addCleanup(restarted.shutdown)
        restarted.start()
        self.join(restarted)
        records = {r["id"]: r for r in restarted.get(self.run.id)["items"]}
        self.assertEqual(records[first["id"]]["status"], "interrupted")
        self.assertEqual(records[second["id"]]["status"], "completed")
        self.assertEqual(len(self.calls), 1)
        self.unchanged()

    def test_recovery_write_failure_does_not_block_other_queued_discussion(self):
        with patch.object(self.service, "_kick"):
            first, second = self.submit("first"), self.submit("second")
        self.service._save({**first, "status": "running"})
        original_file = self.service.root / "records" / f"{first['id']}.json"
        original_bytes = original_file.read_bytes()
        self.service.shutdown()
        restarted = ContentDiscussionService(self.db, self.service.artifacts, self.service.root, self.reviewer)
        self.addCleanup(restarted.shutdown)
        original_save = restarted._save

        def deny_one_recovery(record):
            if record["id"] == first["id"] and record["status"] == "interrupted":
                raise PermissionError("injected recovery write failure")
            return original_save(record)

        with self.assertLogs("creatoros.integrations.content_discussion", level="ERROR"), \
             patch.object(restarted, "_save", side_effect=deny_one_recovery):
            restarted.start()
            self.join(restarted)
        records = {r["id"]: r for r in restarted.get(self.run.id)["items"]}
        self.assertEqual(records[second["id"]]["status"], "completed")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(original_file.read_bytes(), original_bytes)
        self.unchanged()
    def test_thread_start_failure_keeps_message_and_second_message_continues_first(self):
        with patch("creatoros.integrations.content_discussion.Thread.start", side_effect=RuntimeError("no thread")):
            first, second = self.submit("first"), self.submit("second")
        self.assertEqual(first["status"], "queued")
        self.assertFalse(self.service._draining)
        self.service.start()
        self.join()
        self.assertEqual(len(self.calls), 2)
        self.assertIsNone(self.calls[0][0])
        self.assertEqual(self.calls[1][0], "isolated-discussion")
        self.assertEqual(self.submit("first")["id"], first["id"])
        self.assertEqual(len(self.calls), 2)
        self.unchanged()

    def test_tampered_pending_image_fails_before_model_and_does_not_block_followup(self):
        with patch.object(self.service, "_kick"):
            first, second = self.submit("first"), self.submit("second")
        image = self.service.root / "work" / first["id"] / first["input_files"][0]
        image.write_bytes(b"changed-after-submit")
        self.service.start()
        self.join()
        records = {r["id"]: r for r in self.service.get(self.run.id)["items"]}
        self.assertEqual(records[first["id"]]["status"], "failed")
        self.assertEqual(records[second["id"]]["status"], "completed")
        self.assertEqual(len(self.calls), 1)
        self.unchanged()

    def test_shutdown_leaves_later_messages_pending(self):
        started = Event()
        original = self.reviewer.review
        calls = []

        def review(workspace, prompt, images, thread_id, source, cancel, emit, bind):
            calls.append(workspace)
            if len(calls) == 1:
                started.set()
                cancel.wait(8)
                raise RuntimeError("stopped")
            return original(workspace, prompt, images, thread_id, source, cancel, emit, bind)

        self.reviewer.review = review
        first = self.submit("first")
        self.assertTrue(started.wait(5))
        second = self.submit("second")
        self.service.shutdown()
        records = {r["id"]: r for r in self.service.get(self.run.id)["items"]}
        self.assertEqual(records[first["id"]]["status"], "interrupted")
        self.assertEqual(records[second["id"]]["status"], "queued")
        restarted = ContentDiscussionService(self.db, self.service.artifacts, self.service.root, self.reviewer)
        self.addCleanup(restarted.shutdown)
        restarted.start()
        self.join(restarted)
        self.assertEqual(len(calls), 2)
        self.unchanged()


if __name__ == "__main__":
    unittest.main()
