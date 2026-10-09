"""Diagnostic fault injection + real loopback HTTP; no paid SDK or formal data."""
import asyncio
import json
import os
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from creatoros.integrations.atomic_file import atomic_write_text, write_diagnostic_text
from creatoros.integrations.codex import CodexProducerError
from creatoros.integrations.content_discussion import ContentDiscussionService, DiscussionProgress
from creatoros.integrations.production_progress import collect_observed_turn, read_observation_warning
from creatoros.integrations.topic_research import CodexTopicResearcher
from tests.agent_studio_support import serve
from tests.smoke_production_progress import FakeTurn, assistant_item, completed_item, token_update, turn_completed
from tests.smoke_topic_research_sdk import ControlledSdk
from tests.studio_review_fixtures import make_fixture


@contextmanager
def deny_diagnostics(predicate, before=None):
    """Cover both old direct writes and new atomic diagnostic writes."""
    original_text = Path.write_text
    hits = []

    def denied(path):
        if not predicate(Path(path)):
            return False
        if before:
            before(Path(path))
        hits.append(Path(path))
        raise PermissionError("PRIVATE_DIAGNOSTIC_PATH_OR_SECRET")

    def text(path, *args, **kwargs):
        denied(path)
        return original_text(path, *args, **kwargs)

    def atomic(path, value):
        denied(path)
        return atomic_write_text(path, value)

    with patch.object(Path, "write_text", text), \
         patch("creatoros.integrations.atomic_file.atomic_write_text", atomic), \
         patch("creatoros.integrations.topic_research.atomic_write_text", atomic):
        yield hits


class Reviewer:
    def __init__(self, error=None):
        self.error, self.calls = error, 0

    def review(self, directory, prompt, images, thread_id, source, cancel, emit, bind):
        self.calls += 1
        bind("test-discussion-thread", "explicit_snapshot")
        if self.error:
            raise self.error
        return "这是已完成的讨论答复，没有修改作品。", {"scope": "last_model_request"}


class Researcher:
    def __init__(self, preflight=False, interrupted=False):
        self.preflight_failure, self.calls = preflight, 0
        self.error = CodexProducerError("original failure", error_type=(
            "codex_interrupted" if interrupted else "codex_timeout"))

    def preflight(self):
        if self.preflight_failure:
            raise self.error

    def research(self, *args):
        self.calls += 1
        raise self.error


class WorkerFinalizationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="worker-finalization-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    @contextmanager
    def fixture(self, name):
        directory = self.root / name
        directory.mkdir()
        db, runs, producer, app = make_fixture(directory)
        with ExitStack() as cleanup:
            cleanup.callback(db.close)
            run = runs.create("review-1")
            runs.execute(run.id)
            with serve(app) as base, httpx.Client(base_url=base, trust_env=False, timeout=10) as client:
                yield client, app, run.id, producer

    def test_discussion_error_report_cannot_mask_failed_or_interrupted_state(self):
        for kind, status in (("codex_timeout", "failed"), ("codex_interrupted", "interrupted")):
            with self.subTest(status=status), self.fixture(status) as (client, app, run_id, producer):
                service = app.state.content_discussions
                reviewer = Reviewer(CodexProducerError("original SDK failure", error_type=kind))
                service.reviewer = reviewer
                original = client.get(f"/api/runs/{run_id}").json()
                revision = original["revisions"][0]

                def persisted_first(path):
                    records = list((service.root / "records").glob("*.json"))
                    self.assertEqual(len(records), 1)
                    self.assertEqual(json.loads(records[0].read_text(encoding="utf-8"))["status"], status)

                with deny_diagnostics(lambda p: p.name == "error.txt", persisted_first) as hits:
                    response = client.post(f"/api/runs/{run_id}/discussion", json={
                        "request_id": "failure-report", "revision_id": revision["id"],
                        "artifact_digest": revision["review_digest"], "message": "只讨论，不修改"})
                    self.assertEqual(response.status_code, 202, response.text)
                    identifier = response.json()["id"]
                    service.workers[identifier].join(5)
                self.assertTrue(hits)
                self.assertFalse(service.workers[identifier].is_alive())
                path = service.root / "records" / f"{identifier}.json"
                before = path.read_bytes()
                for _ in range(2):
                    item = client.get(f"/api/runs/{run_id}/discussion").json()["items"][0]
                    self.assertEqual(item["status"], status)
                    self.assertIn("original SDK failure", item["error"])
                    self.assertNotIn("PRIVATE_DIAGNOSTIC", json.dumps(item))
                self.assertEqual(path.read_bytes(), before, "GET must not rewrite/retry")
                self.assertEqual(reviewer.calls, 1)
                current = client.get(f"/api/runs/{run_id}").json()
                self.assertEqual((current["status"], current["version"]), (original["status"], original["version"]))
                self.assertEqual(current["revisions"][0]["review_digest"], revision["review_digest"])
                self.assertEqual(producer.calls, 1)

    def test_completed_discussion_survives_reply_copy_failure(self):
        with self.fixture("reply") as (client, app, run_id, producer):
            service = app.state.content_discussions
            reviewer = Reviewer()
            service.reviewer = reviewer
            revision = client.get(f"/api/runs/{run_id}").json()["revisions"][0]
            with deny_diagnostics(lambda p: p.name == "reply.txt") as hits:
                response = client.post(f"/api/runs/{run_id}/discussion", json={
                    "request_id": "reply-copy", "revision_id": revision["id"],
                    "artifact_digest": revision["review_digest"], "message": "只讨论"})
                self.assertEqual(response.status_code, 202, response.text)
                service.workers[response.json()["id"]].join(5)
            item = client.get(f"/api/runs/{run_id}/discussion").json()["items"][0]
            self.assertTrue(hits)
            self.assertEqual(item["status"], "completed")
            self.assertIn("已完成的讨论答复", item["reply"])
            self.assertEqual(reviewer.calls, 1)
            self.assertEqual(producer.calls, 1)

    def test_discussion_usage_is_optional_but_forbidden_tools_are_not(self):
        progress = DiscussionProgress(self.root, lambda *args: None)
        turn = FakeTurn([completed_item(assistant_item("final", "真实 collector 的最终答复", "final_answer")),
                         token_update(12), turn_completed()])
        with deny_diagnostics(lambda p: p.name == "usage.json") as hits:
            result = asyncio.run(collect_observed_turn(turn, progress, progress.public))
            usage = progress.finish_usage(SimpleNamespace(last={"input_tokens": 42}, total={"input_tokens": 99}))
        self.assertTrue(hits)
        self.assertEqual(usage["last_request"]["input_tokens"], 42)
        self.assertEqual(result.final_response, "真实 collector 的最终答复")
        self.assertEqual(turn.stream_calls, 1)
        with self.assertRaisesRegex(RuntimeError, "非预期工具"):
            progress.public(SimpleNamespace(method="item/started", payload={"item": {"type": "commandExecution"}}))

    def test_authoritative_discussion_save_error_is_not_best_effort(self):
        service = ContentDiscussionService(None, None, self.root, Reviewer())
        with patch.object(service, "_save", side_effect=PermissionError("authority unavailable")), \
             patch("creatoros.integrations.content_discussion.write_diagnostic_text") as diagnostic:
            with self.assertRaises(PermissionError):
                service._execute({"status": "queued"}, self.root, "", [])
        diagnostic.assert_not_called()
        self.assertEqual(service.reviewer.calls, 0)
        with patch("creatoros.integrations.atomic_file.atomic_write_text", side_effect=ValueError("invalid serializer")):
            with self.assertRaises(ValueError):
                write_diagnostic_text(self.root / "usage.json", "{}")

    def test_research_error_report_preserves_preflight_and_worker_failures(self):
        for preflight, interrupted in ((True, False), (False, False), (False, True)):
            with self.subTest(preflight=preflight, interrupted=interrupted), \
                 self.fixture(f"research-{preflight}-{interrupted}") as (client, app, run_id, producer):
                service = app.state.topic_research
                researcher = Researcher(preflight, interrupted)
                service.researcher = researcher
                status = "interrupted" if interrupted else "failed"

                def persisted_first(path):
                    identifier = path.name.removesuffix("-error.txt")
                    batch = service._load(identifier)
                    self.assertEqual(batch["status"], status)
                    self.assertEqual(batch["error_type"], researcher.error.error_type)

                with deny_diagnostics(lambda p: p.name.endswith("-error.txt"), persisted_first) as hits:
                    response = client.post("/api/series/agent-notes/topic-research", json={"count": 1})
                    self.assertEqual(response.status_code, 202, response.text)
                    identifier = response.json()["id"]
                    if service.worker:
                        service.worker.join(5)
                self.assertTrue(hits)
                before = service._path(identifier).read_bytes()
                for _ in range(2):
                    batch = client.get(f"/api/topic-research/{identifier}").json()
                    self.assertEqual(batch["status"], status)
                    self.assertEqual(batch["error_type"], researcher.error.error_type)
                    self.assertEqual(batch["candidates"], [])
                self.assertEqual(service._path(identifier).read_bytes(), before)
                self.assertEqual(researcher.calls, 0 if preflight else 1)
                self.assertEqual(len(service.repository.list_topics("agent-notes")), 2)
                self.assertEqual(producer.calls, 1)
                record = service._load(identifier)
                with patch("creatoros.integrations.topic_research._write", side_effect=PermissionError("authority")), \
                     patch("creatoros.integrations.topic_research.write_diagnostic_text") as diagnostic:
                    with self.assertRaises(PermissionError):
                        service._fail(record, researcher.error)
                    diagnostic.assert_not_called()

    def test_research_sdk_trace_and_response_faults_preserve_success_and_real_failure(self):
        original_open = Path.open
        for mode in ("success", "failed", "bad_receipt", "no_search"):
            workspace = self.root / mode
            workspace.mkdir()
            cancel = Event()
            sdk = ControlledSdk(mode, cancel)
            observed, trace_hits = [], []

            def deny_trace(path, *args, **kwargs):
                if path.name == "codex_trace.jsonl" and args and args[0] == "a":
                    trace_hits.append(path)
                    raise PermissionError("PRIVATE_TRACE")
                return original_open(path, *args, **kwargs)

            with patch.dict(os.environ, {"CREATOROS_CODEX_EXECUTABLE": ""}), \
                 patch("openai_codex.AsyncCodex", side_effect=sdk.build), \
                 patch.object(Path, "open", deny_trace), \
                 deny_diagnostics(lambda p: p.name == "response.txt") as response_hits:
                researcher = CodexTopicResearcher(timeout_seconds=5)
                if mode == "success":
                    result = researcher.research({"series": {}}, 1, "", workspace, cancel, public_observer=observed.append)
                    self.assertEqual(len(result.receipt.candidates), 1)
                    self.assertTrue(response_hits)
                else:
                    with self.assertRaises(CodexProducerError) as error:
                        researcher.research({"series": {}}, 1, "", workspace, cancel, public_observer=observed.append)
                    self.assertEqual(error.exception.error_type, {
                        "failed": "codex_sdk_failed", "bad_receipt": "invalid_research_receipt",
                        "no_search": "research_no_search"}[mode])
            self.assertTrue(trace_hits)
            self.assertIsNotNone(read_observation_warning(workspace))
            self.assertTrue(any(e["type"] == "thread.started" for e in observed))
            self.assertEqual(sdk.turn_handle.stream_calls, 1)
            self.assertEqual(len(sdk.turn_calls), 1)
            self.assertTrue(sdk.turn_handle.closed)

    def test_research_authoritative_observer_errors_are_not_swallowed(self):
        cancel = Event()
        sdk = ControlledSdk("success", cancel)
        def failed_authority(event):
            if event["type"] == "item.completed":
                raise PermissionError("authority observer unavailable")
        with patch.dict(os.environ, {"CREATOROS_CODEX_EXECUTABLE": ""}), \
             patch("openai_codex.AsyncCodex", side_effect=sdk.build):
            with self.assertRaises(CodexProducerError):
                CodexTopicResearcher(timeout_seconds=5).research(
                    {"series": {}}, 1, "", self.root, cancel, public_observer=failed_authority)
        self.assertTrue(sdk.turn_handle.interrupted)
        self.assertEqual(sdk.turn_handle.stream_calls, 1)
        self.assertNotEqual(json.loads((self.root / "production_progress.json").read_text())["status"], "completed")


if __name__ == "__main__":
    unittest.main()
