"""Local evidence-grader contracts; these are not model or paid-run scores."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from creatoros.evaluation import workbench
from creatoros.evaluation.discussion_rework_chain import discussion_rework_checks


HASH_SOURCE = "a" * 64
HASH_COPY = "a" * 64
HASH_SKILL = "b" * 64
HASH_INPUT = "c" * 64
HASH_NEW = "d" * 64


def _source():
    return {"image_sha256": HASH_SOURCE, "source_artifact_sha256": HASH_SOURCE,
        "copy_sha256": HASH_COPY, "image_path": "prior/image.png", "copy_path": "isolated/image.png",
        "skill_sha256": HASH_SKILL, "input_snapshot_sha256": HASH_INPUT,
        "generated_in_this_case": False, "production_thread_id": "thread-original",
        "seed_run_id": "run-seed"}


def _database():
    return {"content_runs": [{"id": "run-seed", "topic_id": "topic-1", "status": "awaiting_approval",
            "active_revision_number": 1, "approved_revision_id": None}],
        "content_revisions": [{"id": "rev-1", "content_run_id": "run-seed", "revision_number": 1}],
        "content_attempts": [{"id": "attempt-1", "revision_id": "rev-1", "attempt_number": 1}],
        "content_run_events": [{"id": "event-1", "content_run_id": "run-seed"}],
        "topics": [{"id": "topic-1", "title": "seed", "series_id": "series-target",
                    "status": "queued", "updated_at": "2026-10-11 01:00:00"}], "manual_publications": []}


def _p06():
    before = _database()
    records = [
        {"id": "discussion-1", "status": "completed", "run_id": "run-seed", "revision_id": "rev-1",
         "artifact_digest": "digest-1", "source_thread_id": "thread-original", "thread_id": "thread-discuss",
         "reply": "第二格字太密。"},
        {"id": "discussion-2", "status": "completed", "run_id": "run-seed", "revision_id": "rev-1",
         "artifact_digest": "digest-1", "source_thread_id": "thread-original", "thread_id": "thread-discuss",
         "reply": "第二格第二行可缩短为……。"}]
    return {"oracle": {"seed_run_id": "run-seed", "seed_revision_id": "rev-1", "seed_artifact_digest": "digest-1",
                "seed_image_sha256": HASH_SOURCE, "production_thread_id": "thread-original"},
        "before": {"database": before}, "after": {"database": deepcopy(before)},
        "browser": {"completed": True, "page_errors": [], "posts_after_refresh": 0,
                    "refreshed_discussion_ids": ["discussion-1", "discussion-2"]},
        "reused_source": _source(), "execution_counts": {"producer_instances": 0},
        "discussion_records": records,
        "discussion_probe": [
            {"operation": "thread_fork", "phase": "returned", "requested_thread_id": "thread-original", "thread_id": "thread-discuss"},
            {"operation": "thread_resume", "phase": "returned", "requested_thread_id": "thread-discuss", "thread_id": "thread-discuss"},
            {"operation": "thread_turn", "phase": "started", "thread_id": "thread-discuss", "inputs": [
                {"type": "TextInput", "module": "openai_codex"},
                {"type": "LocalImageInput", "module": "openai_codex._inputs", "path": "/isolated/one.png", "sha256": HASH_SOURCE}]},
            {"operation": "thread_turn", "phase": "started", "thread_id": "thread-discuss", "inputs": [
                {"type": "TextInput", "module": "openai_codex"},
                {"type": "LocalImageInput", "module": "openai_codex._inputs", "path": "/isolated/two.png", "sha256": HASH_SOURCE}]}],
        "discussion_usage": [{"image_sha256": HASH_SOURCE, "image_input_count": 1, "mode": "forked_production",
            "image_inputs": [{"path": "discussions/work/one/page-001.png", "image_sha256": HASH_SOURCE}]},
            {"image_sha256": HASH_SOURCE, "image_input_count": 1, "mode": "continued_discussion",
             "image_inputs": [{"path": "discussions/work/two/page-001.png", "image_sha256": HASH_SOURCE}]}],
        "source_files_unchanged": True, "seed_revision_files_unchanged": True,
        "sdk": {"events": [], "image_generation_events": []}}


def _p04():
    before = _database()
    old_run = deepcopy(before["content_runs"][0])
    run_after = {**old_run, "active_revision_number": 2}
    revision2 = {"id": "rev-2", "content_run_id": "run-seed", "revision_number": 2}
    attempt2 = {"id": "attempt-2", "revision_id": "rev-2", "attempt_number": 1}
    events = [{"id": f"event-{i}", "content_run_id": "run-seed"} for i in range(2, 6)]
    after = deepcopy(before)
    after["content_runs"] = [run_after]
    after["content_revisions"].append(revision2)
    after["content_attempts"].append(attempt2)
    after["content_run_events"].extend(events)
    after["topics"][0].update(status="ready", updated_at="2026-10-11 01:01:00")
    event = {"source": "sdk-files/task/codex_public_events.jsonl", "item_type": "imageGeneration", "status": "completed", "thread_id": "thread-new",
        "turn_id": "turn-1", "generation_artifact": {"verified": True, "saved_path": "outputs/new.png", "sha256": HASH_NEW}}
    output = {"thread_id": "thread-new", "delivery_turn_id": "turn-1", "sdk_prefix": "sdk-files/task/",
        "evidence_complete": True, "images": [{"sha256": HASH_NEW, "digest_matches": True, "width": 1024}]}
    return {"oracle": {"seed_run_id": "run-seed", "production_thread_id": "thread-original",
                "expected_after": {"topic_update": {"id": "topic-1", "status": "ready",
                    "mutable_columns": ["status", "updated_at"]},
                    "row_counts": {key: len(value) + {"content_revisions": 1, "content_attempts": 1,
                    "content_run_events": 4}.get(key, 0) for key, value in before.items()}}},
        "before": {"database": before}, "after": {"database": after},
        "browser": {"completed": True, "page_errors": [], "posts_after_refresh": 0},
        "reused_source": _source(), "execution_counts": {"producer_instances": 1},
        "rework_stages": [{"name": "saved_before_execute", "run_status": "queued", "new_attempt_count": 0,
                           "sdk_client_opens": 0, "image_generation_count": 0},
                          {"name": "completed", "explicit_execute_clicked": True, "old_revision_unchanged": True, "unapproved": True}],
        "outputs": output, "sdk": {"events": [event,
            {"source": "sdk-files/task/codex_public_events.jsonl", "method": "turn/completed",
             "thread_id": "thread-new", "turn_id": "turn-1", "status": "completed"},
            {"source": "sdk-files/task/codex_public_events.jsonl", "method": "capture/finished",
             "thread_id": "thread-new", "turn_id": "turn-1", "status": "completed",
             "payload": {"capture_write_failed": False}}], "image_generation_events": [event],
        "receipts": [{"source": "sdk-files/task/worker_receipt.json", "thread_id": "thread-new",
                "turns": [{"status": "completed", "id": "turn-1", "phase": "production"}]}]}}


class DiscussionReworkGraderTests(unittest.TestCase):
    def _ids(self, result):
        return {row["id"]: row["status"] for row in result["checks"]}

    def test_p06_local_evidence_contract_passes_but_remains_quality_review(self):
        evidence = _p06()
        with patch.object(workbench, "agent_protocol", return_value=(
                {"agent_terminal": True, "request_capture": True, "tool_protocol": True, "final_answer": True},
                [{"name": "get_content_run"}, {"name": "discuss_content_run"},
                 {"name": "discuss_content_run"}, {"name": "get_content_discussion"}])):
            result = discussion_rework_checks(evidence, "P06")
        checks = self._ids(result)
        self.assertTrue(all(checks[key] == "passed" for key in
            ("database_scope", "source_copy_verified", "deepseek_chat_tools", "two_real_discussions",
             "fork_then_continue", "actual_image_inputs", "read_only_no_generation", "round_continuity")))
        self.assertEqual(result["auto_status"], "needs_review")

    def test_p06_negative_cases_fail_closed(self):
        cases = []
        fake_tag = _p06()
        fake_tag["discussion_probe"] = []
        cases.append((fake_tag, "fork_then_continue"))
        attempted = _p06()
        attempted["sdk"]["events"] = [{"item_type": "imageGeneration", "status": "failed"}]
        cases.append((attempted, "read_only_no_generation"))
        copied_only = _p06()
        copied_only["discussion_probe"] = [row for row in copied_only["discussion_probe"] if row["operation"] != "thread_turn"]
        cases.append((copied_only, "actual_image_inputs"))
        changed_seed = _p06()
        changed_seed["seed_revision_files_unchanged"] = False
        cases.append((changed_seed, "read_only_no_generation"))
        missing_hash = _p06()
        missing_hash["reused_source"]["input_snapshot_sha256"] = None
        cases.append((missing_hash, "source_copy_verified"))
        bad_db = _p06()
        bad_db["after"]["database"]["content_runs"][0]["status"] = "queued"
        cases.append((bad_db, "database_scope"))
        for evidence, failed_check in cases:
            with self.subTest(failed_check=failed_check), patch.object(workbench, "agent_protocol", return_value=(
                    {"agent_terminal": True, "request_capture": True, "tool_protocol": True, "final_answer": True},
                    [{"name": "get_content_run"}, {"name": "discuss_content_run"},
                     {"name": "discuss_content_run"}, {"name": "get_content_discussion"}])):
                self.assertEqual(self._ids(discussion_rework_checks(evidence, "P06"))[failed_check], "failed")

    def test_p04_local_saved_path_receipt_and_database_contract_passes(self):
        evidence = _p04()
        result = discussion_rework_checks(evidence, "P04")
        checks = self._ids(result)
        self.assertTrue(all(checks[key] == "passed" for key in
            ("database_scope", "source_copy_verified", "save_without_execution", "new_revision_attempt",
             "new_real_image", "old_revision_preserved")))
        self.assertEqual(result["auto_status"], "needs_review")

    def test_p04_negative_receipt_database_and_saved_path_cases_fail(self):
        cases = []
        no_saved = _p04()
        no_saved["sdk"]["image_generation_events"][0]["generation_artifact"]["saved_path"] = None
        cases.append((no_saved, "new_real_image"))
        no_receipt = _p04()
        no_receipt["sdk"]["receipts"] = []
        cases.append((no_receipt, "new_real_image"))
        no_terminal = _p04()
        no_terminal["sdk"]["events"] = no_terminal["sdk"]["events"][:1]
        cases.append((no_terminal, "new_real_image"))
        mutate_old = _p04()
        mutate_old["after"]["database"]["content_revisions"][0]["title"] = "mutated"
        cases.append((mutate_old, "database_scope"))
        for evidence, failed_check in cases:
            with self.subTest(failed_check=failed_check):
                self.assertEqual(self._ids(discussion_rework_checks(evidence, "P04"))[failed_check], "failed")

    def test_p04_topic_ready_is_expected_but_other_mutations_fail(self):
        mutations = (
            lambda e: e["after"]["database"]["topics"][0].update(status="queued"),
            lambda e: e["after"]["database"]["topics"][0].update(title="rewritten"),
            lambda e: e["after"]["database"]["topics"][0].update(series_id="foreign"),
            lambda e: e["after"]["database"]["topics"][0].update(updated_at=None),
            lambda e: e["after"]["database"]["content_runs"][0].update(topic_id="foreign"),
            lambda e: e["oracle"]["expected_after"]["topic_update"].update(id="foreign"),
            lambda e: e["oracle"]["expected_after"].pop("topic_update"),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                evidence = _p04()
                mutate(evidence)
                self.assertEqual(self._ids(discussion_rework_checks(evidence, "P04"))["database_scope"], "failed")
        evidence = _p04()
        foreign = {"id": "topic-foreign", "title": "foreign", "status": "queued"}
        evidence["before"]["database"]["topics"].append(deepcopy(foreign))
        evidence["after"]["database"]["topics"].append(deepcopy(foreign))
        evidence["oracle"]["expected_after"]["row_counts"]["topics"] += 1
        self.assertEqual(self._ids(discussion_rework_checks(evidence, "P04"))["database_scope"], "passed")
        evidence["after"]["database"]["topics"][1]["status"] = "ready"
        self.assertEqual(self._ids(discussion_rework_checks(evidence, "P04"))["database_scope"], "failed")


if __name__ == "__main__":
    unittest.main()
