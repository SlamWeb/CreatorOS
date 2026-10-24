"""Controlled loopback receipts exercise real adapters/client, without model jobs."""
import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from creatoros.integrations.studio import StudioClientError
from creatoros.tools.background_wait import observe_skill_extraction
from creatoros.tools.chat_images import extract_skill_from_images
from creatoros.tools.skill_extraction import extract_skills_from_artifact, get_skill_extraction, revise_extracted_skills
from creatoros.tools.skill_iterations import manage_skill_iteration


class ImmediateWait:
    def __init__(self):
        self.waits = []

    def is_set(self):
        return False

    def wait(self, seconds):
        self.waits.append(seconds)
        return False


@contextmanager
def receipt_server(receipts):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            length = int(self.headers.get("content-length", 0))
            payload = json.loads(self.rfile.read(length)) if length else None
            calls.append((self.command, self.path, payload))
            value = receipts[min(len(calls) - 1, len(receipts) - 1)]
            body = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = handle_request

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    worker.start()
    events = []
    context = SimpleNamespace(studio_url=f"http://127.0.0.1:{server.server_port}",
        agent_session_id="session", user_request_id="turn", background_progress=events.append,
        stopping=ImmediateWait(), background_wait_timeout_seconds=60)
    try:
        yield context, calls, events
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


class BackgroundWaitTests(unittest.TestCase):
    def test_image_submit_observes_same_job_before_model_gets_terminal(self):
        active = {"id": "job/a", "status": "running", "operation": "extract"}
        ready = {**active, "status": "ready", "operation": None, "digest": "new"}
        with receipt_server([active, ready]) as (context, calls, events):
            result = extract_skill_from_images(["attachment"], context=context)
            self.assertEqual(json.loads(result.content)["status"], "ready")
            self.assertEqual([call[:2] for call in calls], [
                ("POST", "/api/agent/sessions/session/image-actions/extract"),
                ("GET", "/api/skill-extractions/job%2Fa")])
            self.assertEqual([event["status"] for event in events], ["running", "ready"])
            self.assertEqual(len(context.stopping.waits), 1)
            self.assertEqual(context.stopping.waits[0], 2)
            self.assertNotIn("pending_task", result.details)

    def test_artifact_submit_observes_without_resubmission(self):
        with receipt_server([{"id": "job", "status": "running"},
                             {"id": "job", "status": "ready"}]) as (context, calls, events):
            result = extract_skills_from_artifact(source_text="sample", request_id="stable", context=context)
            self.assertEqual(json.loads(result.content)["status"], "ready")
            self.assertEqual([call[0] for call in calls], ["POST", "GET"])
            self.assertEqual(len(events), 2)

    def test_revision_ready_status_with_active_operation_still_waits(self):
        with receipt_server([{"id": "job", "status": "ready", "operation": "revise"},
                             {"id": "job", "status": "ready", "operation": None,
                              "digest": "updated"}]) as (context, calls, events):
            result = revise_extracted_skills("job", "old", "stable", "改为动漫风", context)
            self.assertIsNone(json.loads(result.content)["operation"])
            self.assertEqual(json.loads(result.content)["digest"], "updated")
            self.assertEqual([call[0] for call in calls], ["POST", "GET"])
            self.assertEqual([event["operation"] for event in events], ["revise", None])

    def test_rsi_get_returns_true_status_and_background_instruction_once(self):
        receipt = {"id": "a" * 64, "status": "waiting_production", "stage": "waiting",
                   "next_action": "backend_waits_for_production", "active_round": {"production_submitted": True}}
        with receipt_server([receipt]) as (context, calls, events):
            result = manage_skill_iteration("get", iteration_id="a" * 64, context=context)
            data = json.loads(result.content)
            self.assertEqual(data["status"], "waiting_production")
            self.assertEqual(data["next_action"], "wait_background")
            self.assertEqual(len(calls), 1)
            self.assertFalse(events)
            self.assertEqual(data["backend_next_action"], "backend_waits_for_production")
            self.assertEqual(result.details["pending_task"], {
                "kind": "skill_iteration", "id": "a" * 64, "status": "waiting_production"})

    def test_rsi_start_does_not_wait_for_its_three_rounds(self):
        receipt = {"id": "a" * 64, "status": "queued", "next_action": "backend_evaluates"}
        with receipt_server([receipt]) as (context, calls, events):
            result = manage_skill_iteration("start", series_id="s", skill_id="skill", topic_id="topic", context=context)
            self.assertEqual(json.loads(result.content)["next_action"], "wait_background")
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], "POST")
            self.assertFalse(context.stopping.waits)
            self.assertFalse(events)

    def test_get_is_one_read_and_marks_pending_for_host(self):
        with receipt_server([{"id": "job", "status": "running"}]) as (context, calls, events):
            result = get_skill_extraction("job", context)
            self.assertEqual(json.loads(result.content)["status"], "running")
            self.assertEqual(result.details["pending_task"], {
                "kind": "skill_extraction", "id": "job", "status": "running"})
            self.assertEqual(len(calls), 1)
            self.assertFalse(events)
            self.assertFalse(context.stopping.waits)

    def test_cli_without_observer_preserves_asynchronous_response(self):
        receipt = {"id": "job", "status": "running", "progress": {"stage": "connecting"}}
        with receipt_server([receipt]) as (context, calls, events):
            context.background_progress = None
            result = extract_skills_from_artifact(source_text="sample", request_id="stable", context=context)
            self.assertEqual(json.loads(result.content), receipt)
            self.assertEqual(len(calls), 1)
            self.assertFalse(context.stopping.waits)
            self.assertFalse(events)

    def test_public_progress_goes_to_host_without_entering_model_body(self):
        receipts = [{"id": "job", "status": "running", "progress": {"stage": "connecting"}},
                    {"id": "job", "status": "ready", "progress": {"stage": "finished"}}]
        with receipt_server(receipts) as (context, calls, events):
            result = extract_skill_from_images(["attachment"], context=context)
            self.assertEqual([e["progress"]["stage"] for e in events], ["connecting", "finished"])
            self.assertNotIn("progress", json.loads(result.to_model_content()))
            self.assertIn("progress", json.loads(result.content))

    def test_terminal_failure_does_not_resubmit_or_cancel(self):
        receipts = [{"id": "job", "status": "running"},
                    {"id": "job", "status": "failed", "error_type": "codex_failed", "error": "recorded"}]
        with receipt_server(receipts) as (context, calls, events):
            result = extract_skill_from_images(["attachment"], context=context)
            self.assertTrue(result.is_error)
            self.assertEqual(result.error_type, "codex_failed")
            self.assertEqual(json.loads(result.content)["status"], "failed")
            self.assertNotIn("pending_task", result.details)
            self.assertEqual([call[0] for call in calls], ["POST", "GET"])

    def test_default_wait_is_bounded_to_sixty_seconds_of_host_time(self):
        with receipt_server([{"id": "job", "status": "running"}]) as (context, calls, events):
            del context.background_wait_timeout_seconds
            clock = [0.0]

            def wait(seconds):
                context.stopping.waits.append(seconds)
                clock[0] += seconds
                return False

            context.stopping.wait = wait
            with patch("creatoros.tools.background_wait.monotonic", side_effect=lambda: clock[0]):
                result = extract_skill_from_images(["attachment"], context=context)
            data = json.loads(result.content)
            self.assertEqual((data["status"], data["last_known_status"]), ("unknown", "running"))
            self.assertEqual(result.error_type, "background_wait_timeout")
            self.assertEqual(sum(context.stopping.waits), 60)
            self.assertTrue(all(seconds == 2 for seconds in context.stopping.waits))
            self.assertEqual(sum(call[0] == "POST" for call in calls), 1)
            self.assertEqual(len(events), 1)
            self.assertEqual(result.details["pending_task"]["last_known_status"], "running")

    def test_interruption_preserves_task_instead_of_cancelling(self):
        with receipt_server([{"id": "job", "status": "ready", "operation": "revise"}]) as (context, calls, events):
            context.stopping.is_set = lambda: True
            result = revise_extracted_skills("job", "old", "stable", "revise", context)
            data = json.loads(result.content)
            self.assertEqual((data["status"], data["last_known_status"]), ("unknown", "ready"))
            self.assertEqual(result.error_type, "background_wait_interrupted")
            self.assertEqual(result.details["pending_task"]["id"], "job")
            self.assertEqual(len(calls), 1)

    def test_read_failure_preserves_job_and_never_retries_write(self):
        calls = []

        def request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            raise StudioClientError("injected read outage", "studio_read_failed")

        context = SimpleNamespace(background_progress=lambda event: None, stopping=ImmediateWait())
        result = observe_skill_extraction(SimpleNamespace(request=request), {"id": "job", "status": "running"}, context)
        data = json.loads(result.content)
        self.assertEqual((data["status"], data["last_known_status"]), ("unknown", "running"))
        self.assertEqual(result.error_type, "studio_read_failed")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], ("GET", "/api/skill-extractions/job", {"timeout_seconds": 2.0}))
        self.assertEqual(result.details["pending_task"]["id"], "job")

    def test_wrong_job_response_is_unknown_for_original_identity(self):
        receipts = [{"id": "job", "status": "running"}, {"id": "other", "status": "ready"}]
        with receipt_server(receipts) as (context, calls, events):
            result = extract_skill_from_images(["attachment"], context=context)
            self.assertEqual(json.loads(result.content)["id"], "job")
            self.assertEqual(json.loads(result.content)["status"], "unknown")
            self.assertEqual(result.error_type, "studio_invalid_response")
            self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
