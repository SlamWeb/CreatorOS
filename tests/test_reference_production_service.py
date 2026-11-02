"""Public reference submission over real isolated SQLite/files and controlled workers."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx

from creatoros.ai.types import ToolCall
from creatoros.context import RuntimeContext
from creatoros.integrations.topic_references import TopicReferenceStore
from creatoros.runs import ContentRunError, ReferenceProductionInput, ReferenceProductionService
from creatoros.storage import ContentRepository, Series, WriteReceipt
from creatoros.tools import execute_tool_call
from tests.agent_studio_support import serve
from tests import test_reference_skill_selection as selection
from tests import test_reference_production_api as reference


class ReferenceProductionServiceTests(unittest.TestCase):
    def setUp(self):
        selection.ReferenceSelectionTests.setUp(self)
        self.executors.append(self.app.state.executor)
        # Non-HTTP cases construct the public service directly. The shared
        # fixture supplies its real dependencies, without starting an HTTP server.
        self.direct = ReferenceProductionService(self.database, self.runs, self.app.state.executor,
            self.app.state.writes.pending_operations, library=self.library)
    tearDown = selection.ReferenceSelectionTests.tearDown
    service = selection.ReferenceSelectionTests.service
    counts = selection.ReferenceSelectionTests.counts

    def request(self, request_id="service-reference-once", **changes):
        store = TopicReferenceStore(self.database)
        image = store.upload("series", "original.png", base64.b64encode(reference.picture("blue")).decode())
        ref = store.freeze("series", [image["id"]], "完整保留原始要求", reference_presentation=True)
        return ReferenceProductionInput(reference_id=ref.id, title="Public service reference", request_id=request_id,
                                        skill_ids=[item["id"] for item in self.skills[:2]], **changes)

    def test_public_service_freezes_exact_inputs_and_replays_one_authorization(self):
        api = self.direct
        self.assertIsInstance(api, ReferenceProductionService)
        body = self.request()
        baseline = self.counts()
        try:
            run = api.submit("series", body, creator_id="creator", origin="cli")
            self.assertTrue(self.producer.started.wait(5))
            frozen = self.runs.get(run.id).input_snapshot_json
            spec = frozen["execution_spec"]
            self.assertEqual([item["skill_id"] for item in spec["skills"]], body.skill_ids)
            self.assertEqual(frozen["task_references"]["id"], body.reference_id)
            self.assertEqual(frozen["task_references"]["instructions"], "完整保留原始要求")
            self.assertEqual(Path(frozen["task_references"]["images"][0]["path"]).read_bytes(), reference.picture("blue"))
            original = Path(spec["skills"][0]["path"], "SKILL.md").read_bytes()
            Path(self.skills[0]["local_path"], "SKILL.md").write_text("Changed current source", encoding="utf-8")
            self.assertEqual(api.submit("series", body, creator_id="creator").id, run.id)
            self.assertEqual(Path(spec["skills"][0]["path"], "SKILL.md").read_bytes(), original)
            self.assertEqual(self.counts(), tuple(value + 1 for value in baseline))
            with self.database.session() as session:
                self.assertEqual(session.get(WriteReceipt, body.request_id).origin, "cli")
        finally:
            self.producer.release.set()

    def test_direct_scope_and_queue_failures_leave_no_topic_run_or_receipt(self):
        api = self.direct
        body = self.request()
        baseline = self.counts()
        with self.assertRaises(ContentRunError) as denied:
            api.submit("series", body, creator_id="foreign")
        self.assertEqual(denied.exception.status_code, 403)
        self.assertEqual(self.counts(), baseline)
        with patch.object(self.app.state.executor.queue, "enqueue", side_effect=ContentRunError("Injected queue failure")):
            with self.assertRaisesRegex(ContentRunError, "Injected queue failure"):
                api.submit("series", body, creator_id="creator")
        self.assertEqual(self.counts(), baseline)
        self.assertFalse(self.producer.started.is_set())

    def test_direct_reference_and_skill_scope_cannot_bypass_http_rules(self):
        api = self.direct
        body = self.request()
        repo = ContentRepository(self.database)
        repo.create_series(series_id="foreign-series", creator_id="foreign", name="Foreign", description="", audience="", skill_name=None)
        baseline = self.counts()
        with self.assertRaises(ContentRunError):
            api.submit("foreign-series", body, creator_id="foreign")
        invalid = body.model_copy(update={"skill_ids": [self.skills[2]["id"]]})
        with self.assertRaises(ContentRunError):
            api.submit("series", invalid, creator_id="creator")
        self.assertEqual(self.counts(), baseline)
        self.assertFalse(self.producer.started.is_set())

    def test_web_and_actual_agent_tool_use_the_same_public_service(self):
        api = self.app.state.reference_production
        with serve(self.app) as base, httpx.Client(base_url=base, trust_env=False, timeout=20) as client:
            session_id = client.post("/api/agent/sessions", json={"creator_id": "creator"}).json()["id"]
            attachment = client.post(f"/api/agent/sessions/{session_id}/attachments", json={
                "name": "agent-original.png", "data_base64": base64.b64encode(reference.picture("green")).decode()}).json()["id"]
            body = self.request("web-public-service")
            context = RuntimeContext(Path.cwd(), creator_id="creator", agent_session_id=session_id,
                                     studio_url=base, user_request_id="agent-public-service")
            try:
                with patch.object(api, "submit", wraps=api.submit) as submit:
                    response = client.post("/api/series/series/reference-production", json=body.model_dump())
                    self.assertEqual(response.status_code, 202, response.text)
                    result = execute_tool_call(ToolCall("public-reference", "start_content_run", json.dumps({
                        "action": "reference", "series_id": "series", "title": "Agent public service",
                        "instructions": "完整原话", "attachment_ids": [attachment], "skill_ids": body.skill_ids})), context)
                    self.assertFalse(result.is_error, result.content)
                    self.assertEqual(submit.call_count, 2)
                    self.assertEqual([call.kwargs["origin"] for call in submit.call_args_list], ["web", "agent"])
                    self.assertTrue(all(call.kwargs["creator_id"] == "creator" for call in submit.call_args_list))
            finally:
                self.producer.release.set()

    def test_owner_is_rechecked_inside_the_authorization_transaction(self):
        api = self.direct
        body = self.request()
        baseline = self.counts()
        load = api.references.load
        def reassigned(series_id, reference_id):
            value = load(series_id, reference_id)
            with self.database.session() as session:
                session.get(Series, "series").creator_id = "foreign"
            return value
        with patch.object(api.references, "load", side_effect=reassigned):
            with self.assertRaises(ContentRunError) as denied:
                api.submit("series", body, creator_id="creator")
        self.assertEqual(denied.exception.status_code, 403)
        self.assertEqual(self.counts(), baseline)
        self.assertFalse(self.producer.started.is_set())

    def test_concurrent_direct_callers_commit_only_one_authorization(self):
        api = self.direct
        body = self.request()
        baseline = self.counts()
        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                identifiers = list(pool.map(lambda _: api.submit("series", body, creator_id="creator").id, range(4)))
            self.assertEqual(len(set(identifiers)), 1)
            self.assertTrue(self.producer.started.wait(5))
            self.assertEqual(self.counts(), tuple(value + 1 for value in baseline))
            self.assertEqual(len(self.producer.calls), 1)
        finally:
            self.producer.release.set()
