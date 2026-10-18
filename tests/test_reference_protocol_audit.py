"""Independent read-only audit tests: real HTTP writes with a lost response."""
import unittest
import base64
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx

from creatoros.web.app import create_app
from creatoros.integrations.studio import StudioClient
from creatoros.tools.studio import start_content_run
from creatoros.context import RuntimeContext
from tests.agent_studio_support import serve
from tests import test_reference_production_api as reference_fixture
from tests import test_production_queue as fixture


class DropOneProductionResponse(httpx.BaseTransport):
    def __init__(self):
        self.delegate = httpx.HTTPTransport(trust_env=False)
        self.dropped = False

    def handle_request(self, request):
        response = self.delegate.handle_request(request)
        if request.url.path.endswith("/reference-production") and not self.dropped:
            response.read()
            response.close()
            self.dropped = True
            raise httpx.ReadError("Injected lost response after the server accepted production", request=request)
        return response

    def close(self):
        self.delegate.close()


class ReferenceProtocolAudit(unittest.TestCase):
    setUp = fixture.ProductionQueueTests.setUp
    tearDown = fixture.ProductionQueueTests.tearDown
    service = fixture.ProductionQueueTests.service
    counts = reference_fixture.ReferenceProductionAPITests.counts
    upload = reference_fixture.ReferenceProductionAPITests.upload
    freeze = reference_fixture.ReferenceProductionAPITests.freeze

    def test_research_state_retries_a_transient_windows_file_share_without_fixed_temporary(self):
        import os
        from creatoros.integrations.topic_research import _write
        real_replace = os.replace
        paths = []
        def briefly_shared(source, target):
            paths.append(Path(source))
            if len(paths) == 1:
                error = PermissionError("Injected transient Windows sharing violation")
                error.winerror = 5
                raise error
            return real_replace(source, target)
        target = self.root / "research-state.json"
        with patch("creatoros.integrations.atomic_file.os.replace", briefly_shared):
            _write(target, {"status": "ready", "candidates": ["已完成的候选"]})
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["status"], "ready")
        self.assertEqual(len(paths), 2)
        self.assertNotEqual(paths[0], target.with_suffix(".tmp"))
        self.assertFalse(paths[0].exists())

    def test_agent_unknown_response_has_recovery_handles_and_next_turn_does_not_reproduce(self):
        producer = reference_fixture.CapturingProducer(block=True)
        runs = self.service(producer)
        app = create_app(database=self.database, run_service=runs)
        with serve(app) as base, httpx.Client(base_url=base, trust_env=False, timeout=20) as client:
            sid = client.post("/api/agent/sessions", json={"creator_id": "creator"}).json()["id"]
            original_words = "用户原话：这句要求不能被模型转述丢掉。" * 190
            doc = app.state.chat.get(sid)
            doc["entries"].append({"kind": "user", "text": original_words, "turn_id": "turn-one"})
            app.state.chat._save(doc)
            attachment = client.post(f"/api/agent/sessions/{sid}/attachments", json={
                "name": "参考.png", "data_base64": base64.b64encode(reference_fixture.picture("blue")).decode()}).json()["id"]
            context = RuntimeContext(Path.cwd(), creator_id="creator", agent_session_id=sid,
                                     studio_url=base, user_request_id="turn-one")
            arguments = dict(series_id="series", title="同一参考请求", instructions="完整要求不丢失。",
                             attachment_ids=[attachment], context=context)
            def losing_client(url, *, agent_session_id=None):
                return StudioClient(url, agent_session_id=agent_session_id,
                    client=httpx.Client(base_url=url, transport=DropOneProductionResponse(), timeout=20))
            try:
                with patch("creatoros.tools.studio.StudioClient", losing_client):
                    unknown = start_content_run(**arguments)
                self.assertTrue(unknown.is_error)
                data = json.loads(unknown.content)
                self.assertIn("request_id", data)
                self.assertIn("topic_id", data)
                self.assertIn("receipt_url", data)
                self.assertTrue(producer.started.wait(5))
                arguments["context"] = replace(context, user_request_id="a-later-user-turn")
                replay = start_content_run(**arguments)
                self.assertFalse(replay.is_error, replay.content)
                restored = json.loads(replay.content)
                self.assertEqual(restored["topic_id"], data["topic_id"])
            finally:
                producer.release.set()
            fixture.wait(lambda: runs.get(restored["run_id"]).status.value == "awaiting_approval")
            finished = json.loads(start_content_run(**arguments).content)
            self.assertEqual(finished["run_id"], restored["run_id"])
            self.assertFalse(finished["accepted"])
            self.assertNotIn("已进入持久化生产队列", finished["message"])
            self.assertEqual(len(producer.calls), 1)
            self.assertEqual(producer.requests[0]["task_references"]["source_user_text"], original_words)

    def test_lost_accepted_response_replay_does_not_reproduce_or_accept_changed_input(self):
        producer = reference_fixture.CapturingProducer(block=True)
        runs = self.service(producer)
        app = create_app(database=self.database, run_service=runs)
        with serve(app) as base, httpx.Client(base_url=base, trust_env=False, timeout=20) as client:
            image, _ = self.upload(client)
            reference_id = self.freeze(client, [image])
            request = {"reference_id": reference_id, "title": "Lost acknowledgement", "request_id": "audit-lost-reference"}
            endpoint = "/api/series/series/reference-production"
            before = self.counts()
            try:
                with httpx.Client(base_url=base, transport=DropOneProductionResponse(), timeout=20) as losing_client:
                    with self.assertRaises(httpx.ReadError):
                        losing_client.post(endpoint, json=request)
                self.assertTrue(producer.started.wait(5))
                replay = client.post(endpoint, json=request)
                self.assertEqual(replay.status_code, 202, replay.text)
                run_id = replay.json()["id"]
                self.assertEqual(self.counts(), tuple(count + 1 for count in before))
                changed = client.post(endpoint, json={**request, "title": "Changed user intent"})
                self.assertEqual(changed.status_code, 409, changed.text)
                self.assertEqual(self.counts(), tuple(count + 1 for count in before))
            finally:
                producer.release.set()
            fixture.wait(lambda: runs.get(run_id).status.value == "awaiting_approval")
            self.assertEqual(client.post(endpoint, json=request).json()["id"], run_id)
            self.assertEqual(len(producer.calls), 1)
            public = client.get(f"/api/runs/{run_id}").json()["input_snapshot"]["task_references"]
            self.assertNotIn("path", public["images"][0])


if __name__ == "__main__":
    unittest.main()
