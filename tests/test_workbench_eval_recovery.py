"""Unpaid recovery grader/fault-observer checks, never real SDK eval scores."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from creatoros.evaluation.recovery_chain import RecoveryProbe, recovery_checks
from creatoros.integrations import native_production


def observed_facts():
    """Controlled evidence format for falsifying the grader, not generated content."""
    before_db = {name: [] for name in ("series", "topics", "content_runs", "content_revisions",
        "content_attempts", "manual_publications", "sessions", "messages")}
    before_db["series"] = [{"id": "series", "creator_id": "a", "skill_name": "single"}]
    original = "outputs/a/series/run/revision-001/attempt-001/"
    prefix = "sdk-files/" + original
    original_files = {original + "native_checkpoint.json": "checkpoint", original + "worker_receipt.json": "receipt",
                      original + "images/01.png": "image"}
    before_files = {"sources/single/SKILL.md": "skill"}
    snapshot = {"creator_id": "a", "series_id": "series", "topic_id": "topic"}
    failed_db = deepcopy(before_db)
    failed_db["topics"] = [{"id": "topic", "series_id": "series", "title": "bring take fetch carry", "status": "ready"}]
    failed_db["content_runs"] = [{"id": "run", "topic_id": "topic", "status": "failed", "retryable": 1,
        "version": 5, "producer_thread_id": "thread", "input_snapshot_json": snapshot}]
    failed_db["content_revisions"] = [{"id": "rev", "content_run_id": "run", "production_input_json": snapshot}]
    failed_db["content_attempts"] = [{"id": "old", "revision_id": "rev", "attempt_number": 1,
        "status": "failed", "error_type": "native_delivery_failed", "producer_thread_id": "thread"}]
    final_db = deepcopy(failed_db)
    final_db["content_runs"][0].update(status="awaiting_approval", retryable=0, version=9)
    final_db["content_attempts"].append({"id": "new", "revision_id": "rev", "attempt_number": 2,
        "status": "succeeded", "error_type": None, "producer_thread_id": "thread"})
    event = {"source": prefix + "codex_public_events.jsonl", "thread_id": "thread", "turn_id": "turn", "status": "completed"}
    generated = {**event, "method": "item/completed", "item_type": "imageGeneration",
                 "generation_artifact": {"verified": True, "sha256": "image"}}
    sdk = {"events": [generated, {**event, "method": "turn/completed"},
        {**event, "method": "capture/finished", "payload": {"capture_write_failed": False}}],
        "image_generation_events": [generated], "receipts": [{"source": prefix + "worker_receipt.json", "protocol": "creatoros-worker-v1",
        "thread_id": "thread", "turns": [{"id": "turn", "phase": "production", "status": "completed"}]}]}
    output = {"sdk_prefix": prefix, "delivery_turn_id": "turn", "receipt_completed": True, "attempt_id": "old", "revision_id": "rev",
        "thread_id": "thread", "images": [{"sha256": "image", "digest_matches": True, "width": 1024,
        "height": 1024, "image_prompt": "完整实际Prompt"}], "evidence_complete": True,
        "frozen_skill_files": {"single": "skill"}}
    calls = [{"operation": operation, "phase": "initial"} for operation in ("native_execute", "sdk_client_open")]
    failed_files = {**before_files, **original_files}
    final_files = {**failed_files, "outputs/a/series/run/revision-001/attempt-002/images/01.png": "image"}
    stages = [{"name": "failed", "state": {"database": failed_db, "files": failed_files},
               "outputs": deepcopy(output), "sdk": deepcopy(sdk), "calls": deepcopy(calls)},
              {"name": "restored", "state": {"database": final_db, "files": final_files},
               "outputs": deepcopy(output), "sdk": deepcopy(sdk), "calls": deepcopy(calls)}]
    stages[-1]["outputs"]["attempt_id"] = "new"
    output["attempt_id"] = "new"
    return {"before": {"database": before_db, "files": before_files}, "after": deepcopy(stages[-1]["state"]),
        "recovery_stages": stages, "sdk": sdk, "outputs": output, "recovery_calls": calls,
        "execution_counts": {"producer_instances": 2}, "collection_errors": [], "network_counts_ok": True,
        "browser": {"completed": True, "posts_after_refresh": 0, "page_errors": [], "image_sha256s": ["image"],
          "images_loaded": 1, "visible_prompts": ["完整实际Prompt"], "cover_sha256": "image", "cover_loaded": True,
          "failed_visible": True, "resume_clicked": True, "resume_request": {"body": {"expected_version": 5}}},
        "oracle": {"creator_id": "a", "target_series_id": "series", "topic_keywords": ["bring", "take", "fetch", "carry"],
          "single_skill_id": "single", "source_skill_md": {"single": "skill"}},
        "recovery_fault": {"actor": "controller", "count": 1, "point": "post_sdk_completed_final_ingest",
          "thread_id": "thread", "directory": original.rstrip("/"), "checkpoint_sha256": "checkpoint",
          "receipt_sha256": "receipt", "images": [{"sha256": "image"}]},
        "network": [{"method": "POST", "path": path, "status": status} for path, status in
           (("/api/series/series/queue", 201), ("/api/runs", 201), ("/api/runs/run/execute", 202), ("/api/runs/run/execute", 202))]}


class RecoveryGraderTests(unittest.TestCase):
    def test_complete_format_remains_independent_review_not_auto_pass(self):
        result = recovery_checks(observed_facts())
        self.assertEqual(result["auto_status"], "needs_review")
        self.assertFalse([row for row in result["checks"] if row["status"] == "failed"])

    def test_missing_corrupt_extra_sdk_or_gui_evidence_cannot_pass(self):
        mutations = {
            "stages missing": lambda e: e["recovery_stages"].pop(),
            "duplicate fault": lambda e: e["recovery_fault"].update(count=2),
            "not retryable": lambda e: e["recovery_stages"][0]["state"]["database"]["content_runs"][0].update(retryable=0),
            "no real SDK entry": lambda e: e["recovery_calls"].clear(),
            "extra recovery SDK": lambda e: e["recovery_calls"].append({"phase": "recovery", "operation": "sdk_client_open"}),
            "extra production instance": lambda e: e["execution_counts"].update(producer_instances=3),
            "receipt wrong thread": lambda e: e["recovery_stages"][0]["sdk"]["receipts"][0].update(thread_id="other"),
            "receipt wrong protocol": lambda e: e["recovery_stages"][0]["sdk"]["receipts"][0].update(protocol="fake-completed"),
            "receipt wrong phase": lambda e: e["recovery_stages"][0]["sdk"]["receipts"][0]["turns"][-1].update(phase="review"),
            "capture write failure": lambda e: e["recovery_stages"][0]["sdk"]["events"][-1]["payload"].update(capture_write_failed=True),
            "capture truncated": lambda e: e["recovery_stages"][0]["sdk"]["events"][0].update(truncated=True),
            "later failed turn": lambda e: e["recovery_stages"][0]["sdk"]["events"].append({**e["sdk"]["events"][-1], "status": "failed", "turn_id": "later"}),
            "image unverified": lambda e: e["recovery_stages"][0]["sdk"]["image_generation_events"][0]["generation_artifact"].update(verified=False),
            "new image": lambda e: e["outputs"]["images"][0].update(sha256="new"),
            "wrong prompt": lambda e: e["browser"].update(visible_prompts=["wrong"]),
            "image not loaded": lambda e: e["browser"].update(images_loaded=0),
            "old files changed": lambda e: e["after"]["files"].update({"outputs/a/series/run/revision-001/attempt-001/images/01.png": "changed"}),
            "old files appended": lambda e: e["after"]["files"].update({"outputs/a/series/run/revision-001/attempt-001/new.txt": "new"}),
            "output wrong attempt": lambda e: e["outputs"].update(attempt_id="old"),
            "old attempt rewritten": lambda e: e["recovery_stages"][-1]["state"]["database"]["content_attempts"][0].update(error_type=None),
            "new revision": lambda e: e["recovery_stages"][-1]["state"]["database"]["content_revisions"].append({"id": "extra"}),
            "wrong account": lambda e: e["recovery_stages"][-1]["state"]["database"]["series"][0].update(creator_id="foreign"),
            "approved unexpectedly": lambda e: e["recovery_stages"][-1]["state"]["database"]["content_runs"][0].update(approved_revision_id="rev"),
            "DS invoked": lambda e: e.update(requests=[{"request_id": "unexpected"}]),
            "global skill changed": lambda e: e["after"]["files"].update({"sources/single/SKILL.md": "edited"}),
            "extra GUI mutation": lambda e: e["network"].append({"method": "POST", "path": "/api/runs/run/approve", "status": 200}),
            "queue wrong account": lambda e: e["network"][0].update(path="/api/series/foreign/queue"),
            "CAS mismatch": lambda e: e["browser"]["resume_request"]["body"].update(expected_version=4),
            "GUI failed": lambda e: e["browser"].update(completed=False),
            "collection missing": lambda e: e["collection_errors"].append({"source": "database"}),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                evidence = observed_facts()
                mutate(evidence)
                self.assertEqual(recovery_checks(evidence)["auto_status"], "failed")

    def test_missing_tables_and_original_file_fail_closed(self):
        for mutate in (lambda e: e["after"]["database"].pop("messages"),
                       lambda e: e["after"]["files"].pop("outputs/a/series/run/revision-001/attempt-001/native_checkpoint.json")):
            evidence = observed_facts()
            mutate(evidence)
            self.assertEqual(recovery_checks(evidence)["auto_status"], "failed")

    def test_observer_rejects_outside_or_incomplete_delivery_and_restores_functions(self):
        # Local failure-boundary checks only; this object never calls an SDK/model.
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "fixture"
            directory = root / "outputs/attempt"
            directory.mkdir(parents=True)
            actual = []
            producer = SimpleNamespace(produce_to=lambda **kwargs: actual.append(kwargs))
            execute, client = native_production.execute, native_production._production_client
            probe = RecoveryProbe(root)
            probe.instrument(producer)
            with self.assertRaisesRegex(ValueError, "越界"):
                producer.produce_to(directory=root.parent)
            self.assertEqual(actual, [])
            with self.assertRaisesRegex(ValueError, "真实完整交付不足"):
                producer.produce_to(directory=directory)
            self.assertEqual(len(actual), 1)
            self.assertIs(native_production.execute, execute)
            self.assertIs(native_production._production_client, client)
            self.assertFalse(probe.injected)
            self.assertIsNone(probe.fault)


if __name__ == "__main__":
    unittest.main()
