"""Per-human-turn pending reads are a pure scheduling decision, without HTTP."""
import json
from pathlib import Path
import unittest

from creatoros.agent.pending_tasks import PendingTaskReads
from creatoros.ai.types import ToolCall
from creatoros.context import RuntimeContext
from creatoros.tools.execution import execute_tool_call
from creatoros.tools.results import ToolResult


def arguments(**values):
    return json.dumps(values)


def receipt(identifier="job-a", kind="skill_extraction", status="running", **kwargs):
    return ToolResult(json.dumps({"id": identifier, "status": status}),
                      details={"pending_task": {"kind": kind, "id": identifier, "status": status}},
                      **kwargs)


class PendingTaskReadTests(unittest.TestCase):
    def test_ready_draft_with_active_revision_is_still_pending(self):
        from creatoros.tools.background_wait import background_receipt
        reads = PendingTaskReads()
        args = arguments(job_id="job-a")
        result = background_receipt(ToolResult(json.dumps({
            "id": "job-a", "status": "ready", "operation": "revise"})), "skill_extraction")
        reads.observe("get_skill_extraction", args, result)
        self.assertIsNotNone(reads.before("get_skill_extraction", args))

    def test_one_real_get_then_copy_of_receipt_defers_same_task(self):
        reads = PendingTaskReads()
        args = arguments(job_id="job-a")
        self.assertIsNone(reads.before("get_skill_extraction", args))
        original = receipt()
        reads.observe("get_skill_extraction", args, original)
        deferred = reads.before("get_skill_extraction", args)
        self.assertIsNotNone(deferred)
        self.assertEqual(deferred.content, original.content)
        self.assertEqual(deferred.details["next_action"], "wait_background")
        self.assertTrue(deferred.details["pending_read_deferred"])
        self.assertNotIn("pending_read_deferred", original.details)
        deferred.details["pending_task"]["status"] = "changed"
        self.assertEqual(original.details["pending_task"]["status"], "running")

    def test_submission_does_not_spend_get_and_successful_control_resets_it(self):
        reads = PendingTaskReads()
        identity = "a" * 64
        get = arguments(action="get", iteration_id=identity)
        start = arguments(action="start", series_id="s", skill_id="skill", topic_id="t")
        pending = receipt(identity, "skill_iteration")
        reads.observe("manage_skill_iteration", start, pending)
        self.assertIsNone(reads.before("manage_skill_iteration", get))
        reads.observe("manage_skill_iteration", get, pending)
        self.assertIsNotNone(reads.before("manage_skill_iteration", get))
        pause = arguments(action="pause", iteration_id=identity, expected_revision=2)
        reads.observe("manage_skill_iteration", pause, ToolResult('{"status":"paused"}'))
        self.assertIsNone(reads.before("manage_skill_iteration", get))

    def test_successful_draft_edit_resets_only_its_task(self):
        reads = PendingTaskReads()
        for identity in ("job-a", "job-b"):
            reads.observe("get_skill_extraction", arguments(job_id=identity), receipt(identity))
        reads.observe("cancel_skill_extraction", arguments(job_id="job-a"), ToolResult("cancelled"))
        self.assertIsNone(reads.before("get_skill_extraction", arguments(job_id="job-a")))
        self.assertIsNotNone(reads.before("get_skill_extraction", arguments(job_id="job-b")))

    def test_new_task_new_turn_and_terminal_result_allow_real_read(self):
        reads = PendingTaskReads()
        args = arguments(job_id="job-a")
        reads.observe("get_skill_extraction", args, receipt())
        self.assertIsNone(reads.before("get_skill_extraction", arguments(job_id="job-b")))
        self.assertIsNone(PendingTaskReads().before("get_skill_extraction", args))
        for status in ("ready", "saved", "completed", "failed", "cancelled", "interrupted", "paused"):
            with self.subTest(status=status):
                reads.observe("get_skill_extraction", args, receipt(status=status))
                self.assertIsNone(reads.before("get_skill_extraction", args))

    def test_invalid_arguments_and_account_override_never_reuse_receipt(self):
        reads = PendingTaskReads()
        reads.observe("get_skill_extraction", arguments(job_id="job-a"), receipt())
        for raw in ('{"job_id":"job-a","creator_id":"other"}',
                    '{"job_id":"job-a","unexpected":true}', '["job-a"]', 'broken'):
            with self.subTest(raw=raw):
                self.assertIsNone(reads.before("get_skill_extraction", raw))
                failure = execute_tool_call(ToolCall("invalid", "get_skill_extraction", raw),
                                            model_requested=True)
                self.assertTrue(failure.is_error)
                self.assertEqual(failure.error_type, "invalid_arguments")
        context = RuntimeContext(Path.cwd(), creator_id="current", allowed_tools=frozenset())
        self.assertIsNone(reads.before("get_skill_extraction", arguments(job_id="job-a"), context))
        self.assertIsNone(reads.before("get_skill_extraction", '{}'))
        self.assertIsNone(reads.before("get_skill_extraction_old", arguments(job_id="job-a")))

    def test_unknown_retains_error_and_model_receipt_without_inventing_failure(self):
        reads = PendingTaskReads()
        args = arguments(job_id="job-a")
        unknown = receipt(status="unknown", is_error=True, error_type="background_wait_timeout",
                          retryable=True, model_content='{"status":"unknown"}')
        reads.observe("get_skill_extraction", args, unknown)
        deferred = reads.before("get_skill_extraction", args)
        self.assertEqual(deferred.content, unknown.content)
        self.assertEqual(deferred.model_content, unknown.model_content)
        self.assertTrue(deferred.is_error)
        self.assertTrue(deferred.retryable)
        self.assertEqual(deferred.error_type, "background_wait_timeout")
        self.assertEqual(deferred.details["pending_task"]["status"], "unknown")
        failure = receipt(is_error=True, error_type="denied")
        clean = PendingTaskReads()
        clean.observe("get_skill_extraction", args, failure)
        self.assertIsNone(clean.before("get_skill_extraction", args))

    def test_exact_task_and_kind_required_and_no_url_inference(self):
        reads = PendingTaskReads()
        args = arguments(job_id="job-a")
        reads.observe("get_skill_extraction", args, receipt("job-b"))
        self.assertIsNone(reads.before("get_skill_extraction", args))
        reads.observe("get_skill_extraction", args, ToolResult('/skills?extraction=job-a'))
        self.assertIsNone(reads.before("get_skill_extraction", args))
        reads.observe("get_skill_extraction", args, receipt())
        wrong = receipt(status="unknown", is_error=True, error_type="wrong-kind")
        reads.observe("manage_skill_iteration", arguments(action="get", iteration_id="a" * 64), wrong)
        deferred = reads.before("get_skill_extraction", args)
        self.assertEqual(deferred.error_type, None)

    def test_failed_control_keeps_read_limit_but_successful_revise_releases_it(self):
        reads = PendingTaskReads()
        args = arguments(job_id="job-a")
        reads.observe("get_skill_extraction", args, receipt())
        cancel = arguments(job_id="job-a")
        reads.observe("cancel_skill_extraction", cancel, ToolResult("conflict", True, "conflict"))
        self.assertIsNotNone(reads.before("get_skill_extraction", args))
        revise = arguments(job_id="job-a", expected_digest="digest", request_id="revise", instruction="改稿")
        reads.observe("revise_extracted_skills", revise, receipt())
        self.assertIsNone(reads.before("get_skill_extraction", args))
        reads.observe("get_skill_extraction", args, receipt(status="queued"))
        self.assertEqual(reads.before("get_skill_extraction", args).details["pending_task"]["status"], "queued")

    def test_image_submit_and_external_receipt_refresh_do_not_request_http(self):
        reads = PendingTaskReads()
        submit = arguments(attachment_ids=["attachment-a"])
        get = arguments(job_id="job-a")
        reads.observe("extract_skill_from_images", submit, receipt())
        self.assertIsNone(reads.before("get_skill_extraction", get))
        reads.observe("get_skill_extraction", get, receipt())
        refreshed = receipt(status="unknown", is_error=True, error_type="transport_unknown")
        reads.observe("extract_skill_from_images", submit, refreshed)
        deferred = reads.before("get_skill_extraction", get)
        self.assertEqual(deferred.details["pending_task"]["status"], "unknown")
        self.assertEqual(deferred.error_type, "transport_unknown")
        self.assertEqual(reads.before("get_skill_extraction", get).content, refreshed.content)

    def test_terminal_error_clears_older_pending_and_deferred_is_not_a_new_observation(self):
        reads = PendingTaskReads()
        get = arguments(job_id="job-a")
        reads.observe("get_skill_extraction", get, receipt())
        deferred = reads.before("get_skill_extraction", get)
        deferred.details["pending_task"]["status"] = "queued"
        reads.observe("get_skill_extraction", get, deferred)
        self.assertEqual(reads.before("get_skill_extraction", get).details["pending_task"]["status"], "running")
        terminal = receipt(status="failed", is_error=True, error_type="backend_failed")
        reads.observe("get_skill_extraction", get, terminal)
        self.assertIsNone(reads.before("get_skill_extraction", get))


if __name__ == "__main__":
    unittest.main()
