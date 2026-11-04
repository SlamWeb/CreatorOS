"""Audio chat attachments through isolated loopback HTTP and SQLite.

The provider is controlled; these checks cover account scope and request
identity, not speech understanding. Set DATABASE_URL to a test database before
running a suite that imports creatoros.web.app, which creates a default app.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from types import SimpleNamespace
import unittest
from uuid import uuid4
import wave
from contextlib import ExitStack
from time import monotonic, sleep

import httpx

from creatoros.ai.types import StreamEnd, TextDelta
from creatoros.runs import ContentRunService
from creatoros.storage import ContentRepository, Database, upgrade_database
from tests.agent_studio_support import serve


class RecordingProvider:
    def __init__(self, captures, guard):
        self.client = SimpleNamespace(close=lambda: None)
        self.captures = captures
        self.guard = guard

    def stream(self, context):
        with self.guard:
            self.captures.append(deepcopy(context.to_request()))
        yield TextDelta("音频附件已收到。")
        yield StreamEnd("stop")


def wav(value: int) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        audio.writeframes(value.to_bytes(2, "little", signed=True) * 1600)
    return output.getvalue()


class BgmChatAttachmentTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(TemporaryDirectory(prefix="creatoros-bgm-chat-")))
        url = f"sqlite:///{(self.root / 'studio.db').as_posix()}"
        upgrade_database(url)
        self.db = Database(url)
        self.stack.callback(self.db.close)
        repo = ContentRepository(self.db)
        repo.create_creator(creator_id="audio-a", display_name="A")
        repo.create_creator(creator_id="audio-b", display_name="B")
        self.captures, self.guard = [], Lock()
        from creatoros.web.app import create_app
        runs = ContentRunService(self.db, output_root=self.root / "outputs")
        self.app = create_app(database=self.db, run_service=runs, chat_root=self.root / "chats",
            eval_root=self.root / "eval", defer_background_start=True,
            chat_provider_factory=lambda: RecordingProvider(self.captures, self.guard))
        self.stack.callback(self.app.state.chat.shutdown)
        origin = self.stack.enter_context(serve(self.app))
        self.client = self.stack.enter_context(httpx.Client(base_url=origin, timeout=15, trust_env=False))
        self.endpoint = "/api/agent/sessions"
        self.sid = self.client.post(self.endpoint, json={"creator_id": "audio-a"}).json()["id"]
        self.a = self.upload("audio-a", "a.wav", 1)
        self.a2 = self.upload("audio-a", "a2.wav", 2)
        self.foreign = self.upload("audio-b", "foreign.wav", 3)

    def upload(self, owner, name, value):
        response = self.client.post(f"/api/creators/{owner}/materials", json={
            "name": name, "data_base64": base64.b64encode(wav(value)).decode("ascii")})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def turn(self, ids, request_id, *, text="", version=0):
        return self.client.post(f"{self.endpoint}/{self.sid}/turns", json={
            "request_id": request_id, "expected_version": version,
            "text": text, "material_ids": ids})

    def idle(self):
        deadline = monotonic() + 20
        while monotonic() < deadline:
            response = self.client.get(f"{self.endpoint}/{self.sid}")
            self.assertEqual(response.status_code, 200, response.text)
            view = response.json()
            if view["status"] != "running":
                return view
            sleep(.05)
        self.fail("isolated chat did not become idle")

    def test_audio_only_is_accepted_and_does_not_expose_host_path(self):
        request_id = str(uuid4())
        sent = self.turn([self.a["id"]], request_id)
        self.assertEqual(sent.status_code, 202, sent.text)
        view = self.idle()
        self.assertEqual(view["status"], "idle", view)
        user = next(item for item in view["entries"] if item["kind"] == "user")
        self.assertEqual([item["id"] for item in user["materials"]], [self.a["id"]])
        self.assertEqual(len(self.captures), 1)
        prompt = json.dumps(self.captures[0], ensure_ascii=False)
        self.assertIn(self.a["id"], prompt)
        host_path = str(self.app.state.materials.resolve("audio-a", self.a["id"])["path"])
        def strings(value):
            if isinstance(value, str):
                yield value.replace("\\", "/").casefold()
            elif isinstance(value, dict):
                for item in value.values():
                    yield from strings(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    yield from strings(item)
        for visible in (view, self.captures[0]):
            for text in strings(visible):
                self.assertNotIn(host_path.replace("\\", "/").casefold(), text)
                self.assertNotIn(str(self.root).replace("\\", "/").casefold(), text)

    def test_foreign_audio_is_rejected_before_model(self):
        rejected = self.turn([self.foreign["id"]], str(uuid4()), text="分析这段音频")
        self.assertIn(rejected.status_code, (403, 404, 422), rejected.text)
        self.assertEqual(self.captures, [])
        view = self.client.get(f"{self.endpoint}/{self.sid}").json()
        self.assertFalse(any(item["kind"] == "user" for item in view["entries"]))
        self.assertNotIn(str(self.root), rejected.text)

    def test_corrupt_music_status_does_not_block_normal_chat(self):
        path = self.app.state.materials.resolve("audio-a", self.a["id"])["path"]
        (path.parent / "bgm-analysis-status.json").write_text("{", encoding="utf-8")
        sent = self.turn([], str(uuid4()), text="你好")
        self.assertEqual(sent.status_code, 202, sent.text)
        view = self.idle()
        self.assertEqual(view["status"], "idle", view)
        self.assertEqual(len(self.captures), 1)
        self.assertIn("音乐索引暂不可用", json.dumps(self.captures[0], ensure_ascii=False))

    def test_same_request_replays_but_changed_material_conflicts(self):
        request_id = str(uuid4())
        initial = self.turn([self.a["id"]], request_id, text="分析这段音频")
        self.assertEqual(initial.status_code, 202, initial.text)
        view = self.idle()
        self.assertEqual(view["status"], "idle", view)
        repeated = self.turn([self.a["id"]], request_id, text="分析这段音频")
        self.assertEqual(repeated.status_code, 202, repeated.text)
        changed = self.turn([self.a2["id"]], request_id, text="分析这段音频")
        self.assertEqual(changed.status_code, 409, changed.text)
        self.assertEqual(len(self.captures), 1)
        final = self.client.get(f"{self.endpoint}/{self.sid}").json()
        self.assertEqual(sum(item["kind"] == "user" for item in final["entries"]), 1)


if __name__ == "__main__":
    unittest.main()
