"""Real isolated files; controlled model waits/failures exercise queue lifecycle."""
import asyncio
import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from creatoros.integrations.producer_skills import ProducerSkillCatalog, _digest
from creatoros.integrations.skill_extraction import (
    DraftSkill, ExtractionError, ExtractionResult, SkillExtractionService,
)
from tests.smoke_skill_workbench import TrialProducer


class SkillJobQueueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.catalog = ProducerSkillCatalog(self.root / "skills")
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = []
        self.producer = TrialProducer()
        self.service = self.new_service()
        self.service.start()

    def new_service(self):
        return SkillExtractionService(self.catalog, self.extract, self.revise, lambda: self.producer)

    def tearDown(self):
        self.release.set()
        self.service.shutdown()
        self.temporary.cleanup()

    async def extract(self, directory, images, mode, instruction, cancel, on_thread):
        self.calls.append(instruction)
        on_thread("controlled-" + instruction)
        if instruction == "block":
            self.started.set()
            while not self.release.is_set() and not cancel.is_set():
                await asyncio.sleep(.005)
        if cancel.is_set() or instruction == "fail":
            raise RuntimeError("injected cancellation/failure")
        result = ExtractionResult(note="queue wiring fixture", skills=[DraftSkill(
            name="queue-example", role="legacy_end_to_end", output_kind="text",
            skill_md="---\nname: queue-example\ndescription: Queue fixture\n---\n\nKeep concrete examples intact.\n")])
        if instruction == "merge":
            output = directory / "draft" / "skill"
            output.mkdir(parents=True)
            (output / "SKILL.md").write_text(result.skills[0].skill_md, encoding="utf-8")
            result._directories = {"legacy_end_to_end": output}
        return result

    async def revise(self, directory, images, mode, instruction, cancel, on_thread, current_skills):
        self.calls.append(instruction)
        return ExtractionResult(note="controlled revision", skills=[
            DraftSkill.model_validate({**current_skills[0],
                "skill_md": current_skills[0]["skill_md"] + "\n" + instruction + "\n"})])

    def submit(self, request, instruction):
        return self.service.submit(request, source_text="A reference story.", instruction=instruction)

    def block(self):
        job = self.submit("blocking", "block")
        self.assertTrue(self.started.wait(3))
        return job

    def join(self):
        worker = self.service.worker
        self.assertIsNotNone(worker)
        worker.join(5)
        self.assertFalse(worker.is_alive(), "queue consumer did not finish")

    def author(self, name):
        return self.service.author(name, name, "Fixture description", "Preserve the supplied concrete story.")

    def test_independent_jobs_queue_idempotently_and_failure_does_not_block(self):
        first = self.block()
        bad = self.submit("failing", "fail")
        final = self.submit("last", "success")
        self.assertEqual(bad["status"], "queued")
        self.assertEqual(final["queue_state"], "queued")
        self.assertEqual(self.submit("last", "success")["id"], final["id"])
        with self.assertRaises(ExtractionError):
            self.submit("last", "different")
        self.assertEqual(self.calls, ["block"])
        persisted = json.loads((self.service._path("jobs", final["id"]) / "job.json").read_text())
        self.assertEqual(persisted["queue"]["state"], "queued")
        self.release.set()
        self.join()
        self.assertEqual(self.calls, ["block", "fail", "success"])
        self.assertEqual(self.service.get(first["id"])["status"], "ready")
        self.assertEqual(self.service.get(bad["id"])["status"], "failed")
        self.assertEqual(self.service.get(final["id"])["status"], "ready")

    def test_cancel_pending_never_cancels_active(self):
        active = self.block()
        pending = self.submit("pending", "must-not-run")
        signal = self.service.cancel_event
        cancelled = self.service.cancel(pending["id"])
        self.assertEqual(cancelled["status"], "interrupted")
        self.assertIs(self.service.cancel_event, signal)
        self.assertFalse(signal.is_set())
        self.assertEqual(self.service.get(active["id"])["status"], "running")
        self.release.set()
        self.join()
        self.assertEqual(self.calls, ["block"])

    def test_author_and_edit_another_draft_preserve_running_cancel_signal(self):
        active = self.block()
        signal = self.service.cancel_event
        draft = self.author("local-author")
        changed = [{**draft["skills"][0], "skill_md": draft["skills"][0]["skill_md"] + "\nLocal change.\n"}]
        self.service.edit(draft["id"], draft["digest"], changed)
        self.assertIs(self.service.cancel_event, signal)
        self.service.cancel(active["id"])
        self.assertTrue(signal.is_set())
        self.join()
        self.assertEqual(self.service.get(active["id"])["status"], "interrupted")
        self.assertEqual(self.service.get(draft["id"])["status"], "ready")

    def test_shutdown_preserves_pending_and_restart_only_runs_pending(self):
        active = self.block()
        pending = self.submit("after-restart", "new-only")
        self.service.shutdown()
        self.assertEqual(self.service.get(active["id"])["status"], "interrupted")
        self.assertEqual(self.service.get(pending["id"])["status"], "queued")
        self.service = self.new_service()
        self.service.start()
        self.join()
        self.assertEqual(self.calls, ["block", "new-only"])
        self.assertEqual(self.service.get(pending["id"])["status"], "ready")

    def test_queued_revision_reconstructs_after_restart_and_rejects_same_draft_edits(self):
        draft = self.author("revise-draft")
        base = self.service._draft_root(self.service._read(draft["id"]))
        digest = _digest(base)
        self.block()
        queued = self.service.revise(draft["id"], "revise-once", draft["digest"], "Keep the cake payoff.")
        self.assertEqual(queued["queue_state"], "queued")
        self.assertEqual(queued["operation"], "revise")
        self.assertEqual(self.service.revise(draft["id"], "revise-once", draft["digest"],
                                             "Keep the cake payoff.")["id"], draft["id"])
        with self.assertRaises(ExtractionError):
            self.service.edit(draft["id"], draft["digest"], draft["skills"])
        with self.assertRaises(ExtractionError):
            self.service.trial(draft["id"], "conflicting-trial", draft["digest"], "new topic")
        self.service.shutdown()
        self.service = self.new_service()
        self.service.start()
        self.join()
        result = self.service.get(draft["id"])
        self.assertEqual(self.calls, ["block", "Keep the cake payoff."])
        self.assertEqual(result["revision"], 2)
        self.assertIn("Keep the cake payoff.", result["skills"][0]["skill_md"])
        self.assertEqual(_digest(base), digest)

    def test_trial_queued_snapshot_survives_restart_and_is_not_reproduced(self):
        draft = self.author("trial-draft")
        self.block()
        queued = self.service.trial(draft["id"], "trial-once", draft["digest"], "one new topic")
        self.assertEqual(queued["trials"][0]["status"], "queued")
        self.assertEqual(self.producer.calls, [])
        self.service.shutdown()
        self.service = self.new_service()
        self.service.start()
        self.join()
        result = self.service.get(draft["id"])
        self.assertEqual(result["trials"][0]["status"], "completed")
        self.assertEqual(len(self.producer.calls), 1)
        self.assertEqual(self.producer.calls[0]["topic_title"], "one new topic")
        self.assertEqual(self.service.trial(draft["id"], "trial-once", draft["digest"],
                                            "one new topic")["trials"][0]["status"], "completed")
        self.assertEqual(len(self.producer.calls), 1)

    def test_cancel_pending_trial_retains_draft_without_producer_call(self):
        draft = self.author("cancel-trial")
        self.block()
        self.service.trial(draft["id"], "queued-trial", draft["digest"], "not generated")
        result = self.service.cancel(draft["id"])
        self.assertEqual(result["digest"], draft["digest"])
        self.assertEqual(result["trials"][0]["status"], "interrupted")
        self.release.set()
        self.join()
        self.assertEqual(self.producer.calls, [])

    def test_consumer_start_failure_retains_request_and_same_request_retries_dispatch(self):
        original = threading.Thread.start

        def fail_consumer(thread):
            if thread.name == "creatoros-skill-queue":
                raise RuntimeError("injected start failure")
            return original(thread)

        with patch("threading.Thread.start", fail_consumer):
            job = self.submit("launch-fails", "success")
        self.assertEqual(job["status"], "queued")
        self.assertEqual(self.calls, [])
        self.submit("launch-fails", "success")
        self.join()
        self.assertEqual(self.calls, ["success"])
        self.assertEqual(self.service.get(job["id"])["status"], "ready")

    def test_merge_queues_immutable_sources_without_editing_registered_skills(self):
        drafts = [self.author(name) for name in ("source-one", "source-two")]
        registered = [self.service.save(job["id"], job["digest"])["saved_skills"][0] for job in drafts]
        before = [self.catalog.describe(item["id"])["digest"] for item in registered]
        self.block()
        queued = self.service.submit_merge("queued-merge", [item["id"] for item in registered], "merge")
        self.assertEqual(queued["queue_state"], "queued")
        self.assertEqual(self.service.submit_merge("queued-merge",
            [item["id"] for item in registered], "merge")["id"], queued["id"])
        self.release.set()
        self.join()
        result = self.service.get(queued["id"])
        self.assertEqual(result["status"], "ready", result.get("error"))
        self.assertEqual(self.calls, ["block", "merge"])
        self.assertEqual([self.catalog.describe(item["id"])["digest"] for item in registered], before)

    def test_crashed_claim_is_interrupted_not_automatically_replayed(self):
        with patch("creatoros.integrations.skill_job_queue.kick"):
            job = self.submit("old-claimed", "must-not-repeat")
        row = self.service._read(job["id"])
        self.service._update(job["id"], status="running",
                             queue={**row["queue"], "state": "running"})
        self.service.shutdown()
        self.service = self.new_service()
        self.service.start()
        result = self.service.get(job["id"])
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(result["queue_state"], "interrupted")
        self.assertEqual(self.calls, [])

    def test_published_draft_cannot_start_next_action_until_queue_receipt_finishes(self):
        published, finish = threading.Event(), threading.Event()
        original = self.service._run

        def pause_after_publish(*args):
            original(*args)
            published.set()
            self.assertTrue(finish.wait(4))

        with patch.object(self.service, "_run", pause_after_publish):
            job = self.submit("receipt-race", "success")
            self.assertTrue(published.wait(3))
            current = self.service.get(job["id"])
            self.assertEqual(current["status"], "ready")
            self.assertIsNone(current["operation"])
            self.assertEqual(current["queue_state"], "running")
            try:
                with self.assertRaises(ExtractionError):
                    self.service.revise(job["id"], "next-edit", current["digest"], "next requirement")
                with self.assertRaises(ExtractionError):
                    self.service.save(job["id"], current["digest"])
            finally:
                finish.set()
            self.join()
        self.service.revise(job["id"], "next-edit", current["digest"], "next requirement")
        self.join()
        latest = self.service.get(job["id"])
        self.assertEqual(latest["revision"], 2)
        self.assertIn("next requirement", latest["skills"][0]["skill_md"])
        self.assertEqual(latest["queue_state"], "completed")

    def test_late_revision_cancellation_keeps_old_draft_even_if_model_returns_valid_skill(self):
        draft = self.author("late-cancel")

        async def late_cancel(directory, images, mode, instruction, cancel, on_thread, current_skills):
            cancel.set()
            return ExtractionResult(note="valid but cancelled", skills=[
                DraftSkill.model_validate({**current_skills[0],
                    "skill_md": current_skills[0]["skill_md"] + "\nMust not publish this.\n"})])

        self.service.reviser = late_cancel
        self.service.revise(draft["id"], "cancel-at-return", draft["digest"], "try edit")
        self.join()
        result = self.service.get(draft["id"])
        self.assertEqual(result["digest"], draft["digest"])
        self.assertEqual(result["revision"], draft["revision"])
        self.assertEqual(result["skills"], draft["skills"])
        self.assertEqual(result["queue_state"], "interrupted")
        self.assertIsNone(result["operation"])

    def test_extract_and_merge_authorize_only_once_after_all_inputs_exist(self):
        from creatoros.integrations import skill_extraction
        drafts = [self.author(name) for name in ("atomic-source-one", "atomic-source-two")]
        ids = [self.service.save(job["id"], job["digest"])["saved_skills"][0]["id"] for job in drafts]
        original = skill_extraction._write
        writes = []

        def observe(path, value):
            if path.name == "job.json":
                self.assertEqual(value["queue"]["state"], "queued")
                self.assertTrue((path.parent / "request.json").is_file())
                if value.get("task_kind") == "merge":
                    self.assertTrue((path.parent / "draft").is_dir())
                else:
                    self.assertEqual((path.parent / "source.txt").read_text(), "A reference story.")
                writes.append(value["id"])
            return original(path, value)

        with patch.object(skill_extraction, "_write", observe), patch(
                "creatoros.integrations.skill_job_queue.kick"):
            extracted = self.submit("atomic-extract", "success")
            merged = self.service.submit_merge("atomic-merge", ids, "merge")
        self.assertEqual(writes, [extracted["id"], merged["id"]])
        self.assertEqual(self.calls, [])
        self.submit("atomic-extract", "success")
        self.join()
        self.assertEqual(self.calls, ["success", "merge"])

    def test_initial_job_write_failure_allows_same_extract_and_merge_request_retry(self):
        from creatoros.integrations import skill_extraction
        drafts = [self.author(name) for name in ("retry-source-one", "retry-source-two")]
        ids = [self.service.save(job["id"], job["digest"])["saved_skills"][0]["id"] for job in drafts]
        original = skill_extraction._write

        def fail_job_write(path, value):
            if path.name == "job.json":
                raise PermissionError("injected before authoritative job write")
            return original(path, value)

        for operation in ("extract", "merge"):
            with self.subTest(operation=operation):
                def submit():
                    return (self.submit("write-retry-extract", "success") if operation == "extract"
                            else self.service.submit_merge("write-retry-merge", ids, "merge"))
                with patch.object(skill_extraction, "_write", fail_job_write):
                    with self.assertRaises((PermissionError, ExtractionError)):
                        submit()
                job = submit()
                self.join()
                self.assertEqual(self.service.get(job["id"])["status"], "ready")
        self.assertEqual(self.calls, ["success", "merge"])

    def test_revision_and_trial_queue_write_failure_before_or_after_commit_retries_once(self):
        from creatoros.integrations import skill_job_queue
        original = skill_job_queue._write
        for operation in ("revise", "trial"):
            for committed in (False, True):
                with self.subTest(operation=operation, committed=committed):
                    draft = self.author(f"retry-{operation}-{int(committed)}")
                    def fail_commit(path, value):
                        if committed:
                            original(path, value)
                        raise PermissionError("injected queue commit response loss")
                    def submit():
                        if operation == "revise":
                            return self.service.revise(draft["id"], "retry-once", draft["digest"], "retry revision")
                        return self.service.trial(draft["id"], "retry-once", draft["digest"], "retry trial")
                    before_calls, before_productions = len(self.calls), len(self.producer.calls)
                    with patch.object(skill_job_queue, "_write", fail_commit):
                        with self.assertRaises(PermissionError):
                            submit()
                    self.assertEqual(len(self.calls), before_calls)
                    self.assertEqual(len(self.producer.calls), before_productions)
                    submit()
                    self.join()
                    submit()  # Lost response retry stays idempotent after completion too.
                    final = self.service.get(draft["id"])
                    self.assertEqual(final["queue_state"], "completed")
                    self.assertEqual(len(self.calls) - before_calls, int(operation == "revise"))
                    self.assertEqual(len(self.producer.calls) - before_productions, int(operation == "trial"))
                    if operation == "revise":
                        self.assertEqual(final["revision"], 2)
                    else:
                        self.assertEqual(len(final["trials"]), 1)

    def test_partial_revision_preparation_is_retained_and_same_request_can_retry(self):
        draft = self.author("partial-revision")
        with patch("creatoros.integrations.skill_draft_files.seed_file_drafts",
                   side_effect=OSError("injected preparation fault")):
            with self.assertRaises(OSError):
                self.service.revise(draft["id"], "partial-once", draft["digest"], "complete preparation")
        self.service.revise(draft["id"], "partial-once", draft["digest"], "complete preparation")
        self.join()
        root = self.service._path("jobs", draft["id"]) / "revisions"
        self.assertEqual(len(list(root.glob("*-incomplete-*"))), 1)
        self.assertEqual(self.calls, ["complete preparation"])
        self.assertEqual(self.service.get(draft["id"])["revision"], 2)

    def test_partial_trial_snapshot_is_retained_and_same_request_can_retry(self):
        from creatoros.integrations import skill_workbench
        draft = self.author("partial-trial")
        freeze = skill_workbench.freeze_skill

        def freeze_then_fail(*args):
            freeze(*args)
            raise OSError("injected failure after freezing trial input")

        with patch.object(skill_workbench, "freeze_skill", freeze_then_fail):
            with self.assertRaises(OSError):
                self.service.trial(draft["id"], "partial-trial-once", draft["digest"], "retry partial trial")
        self.service.trial(draft["id"], "partial-trial-once", draft["digest"], "retry partial trial")
        self.join()
        root = self.service._path("jobs", draft["id"]) / "trials"
        self.assertEqual(len(list(root.glob("*-incomplete-*"))), 1)
        self.assertEqual(len(self.producer.calls), 1)
        result = self.service.get(draft["id"])
        self.assertEqual(len(result["trials"]), 1)
        self.assertEqual(result["trials"][0]["status"], "completed")

    def test_broken_historical_record_does_not_block_start_or_pending_jobs(self):
        with patch("creatoros.integrations.skill_job_queue.kick"):
            job = self.submit("valid-pending", "success")
        corrupted = self.service.root / "jobs" / ("f" * 64) / "job.json"
        corrupted.parent.mkdir(parents=True)
        corrupted.write_bytes(b'{"status": "running", broken')
        before = corrupted.read_bytes()
        self.service.shutdown()
        self.service = self.new_service()
        self.service.start()
        self.join()
        self.assertEqual(self.calls, ["success"])
        self.assertEqual(self.service.get(job["id"])["status"], "ready")
        self.assertEqual([entry["id"] for entry in self.service.list()], [job["id"]])
        self.assertEqual(corrupted.read_bytes(), before)

    def test_recovery_write_failure_preserves_old_claim_and_runs_pending(self):
        with patch("creatoros.integrations.skill_job_queue.kick"):
            old = self.submit("old-unwritable", "must-not-replay")
            pending = self.submit("valid-pending", "success")
        row = self.service._read(old["id"])
        self.service._update(old["id"], status="running",
                             queue={**row["queue"], "state": "running"})
        path = self.service._path("jobs", old["id"]) / "job.json"
        before = path.read_bytes()
        self.service.shutdown()
        self.service = self.new_service()
        from creatoros.integrations.skill_extraction import _write

        def fail_old_recovery(target, value):
            if target == path and value.get("status") == "interrupted":
                raise PermissionError("injected recovery write failure")
            return _write(target, value)

        with patch("creatoros.integrations.skill_extraction._write", side_effect=fail_old_recovery):
            with self.assertLogs("creatoros.integrations.skill_extraction", level="ERROR"):
                self.service.start()
            self.join()
        self.assertEqual(self.calls, ["success"])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.service.get(old["id"])["queue_state"], "running")
        self.assertEqual(self.service.get(pending["id"])["status"], "ready")


if __name__ == "__main__":
    unittest.main()
