"""HTTP input preservation with an isolated service and scripted model."""
import base64
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from uuid import uuid4

import httpx
from PIL import Image

from creatoros.session.snapshot import load_messages
from tests.agent_studio_support import serve
from tests.smoke_web_agent import wait_idle
from tests.studio_review_fixtures import make_fixture
from tests.test_reconstruct_public_contract import ScriptedProvider


class AgentInputWhitespaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(prefix="agent-input-whitespace-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db, self.runs, self.producer, self.app = make_fixture(self.root)
        self.addCleanup(self.db.close)
        self.providers = []

        def provider():
            value = ScriptedProvider([None])
            value.client = SimpleNamespace(close=lambda: None)
            self.providers.append(value)
            return value

        self.app.state.chat.provider_factory = provider
        self.session = self.app.state.chat.create("review-lab")
        self.sid = self.session["id"]

    def test_input_and_request_identity_preserve_exact_nonempty_text(self):
        text = "  ---\nname: example\ndescription: 说明\n---\n正文\n\n"
        request_id = str(uuid4())
        payload = {"text": text, "expected_version": self.session["version"], "request_id": request_id}
        with serve(self.app) as url, httpx.Client(base_url=url, timeout=15, trust_env=False) as client:
            endpoint = f"/api/agent/sessions/{self.sid}/turns"
            self.assertEqual(client.post(endpoint, json=payload).status_code, 202)
            done = wait_idle(client, self.sid)
            self.assertEqual(done["status"], "idle")
            self.assertEqual(client.post(endpoint, json=payload).status_code, 202)
            self.assertEqual(client.post(endpoint, json={**payload, "text": text.rstrip()}).status_code, 409)
            self.assertEqual(len(self.providers), 1)
            self.assertEqual([entry["text"] for entry in done["entries"] if entry.get("kind") == "user"], [text])
            messages, _ = self.providers[0].requests[0]
            self.assertTrue(any(m.get("role") == "user" and m.get("content") == text for m in messages))
            ledger = load_messages(self.app.state.chat._path(self.sid).with_name("messages.json"))
            self.assertEqual(sum(m.get("role") == "user" and m.get("content") == text for m in ledger), 1)
            self.assertEqual(self.producer.calls, 0)

    def test_whitespace_only_and_spaced_slash_keep_existing_boundaries(self):
        image = BytesIO()
        Image.new("RGB", (8, 8), "blue").save(image, format="PNG")
        attachment = self.app.state.chat.attachments.put(
            self.sid, "reference.png", base64.b64encode(image.getvalue()).decode())
        with serve(self.app) as url, httpx.Client(base_url=url, timeout=15, trust_env=False) as client:
            endpoint = f"/api/agent/sessions/{self.sid}/turns"
            base = {"expected_version": self.session["version"], "request_id": str(uuid4())}
            self.assertEqual(client.post(endpoint, json={**base, "text": " \n\t"}).status_code, 422)
            self.assertEqual(client.post(endpoint, json={**base, "text": " \n /reset \n"}).status_code, 422)
            self.assertEqual(client.post(endpoint, json={**base, "text": " \n\t", "attachment_ids": [attachment["id"]]}).status_code, 202)
            done = wait_idle(client, self.sid)
            self.assertEqual(done["status"], "idle")
            self.assertEqual(next(e["text"] for e in done["entries"] if e.get("kind") == "user"), "")
            messages, _ = self.providers[0].requests[0]
            contents = [m.get("content") for m in messages if m.get("role") == "user" and isinstance(m.get("content"), list)]
            self.assertTrue(any(any(item.get("type") == "text" and "用户发送了附件" in item.get("text", "")
                                    for item in content if isinstance(item, dict)) for content in contents))
            self.assertTrue(any(any(item.get("type") == "image_ref" for item in content if isinstance(item, dict))
                                for content in contents))
            self.assertEqual(self.producer.calls, 0)


if __name__ == "__main__":
    unittest.main()
