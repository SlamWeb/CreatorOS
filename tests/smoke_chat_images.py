"""Real isolated HTTP/SQLite/image files; controlled LLM checks wiring, not vision quality."""
import base64
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from uuid import uuid4

import httpx

from creatoros.ai.types import StreamEnd, TextDelta, ToolCallDelta
from creatoros.session.snapshot import load_messages
from creatoros.web.app import create_app
from creatoros.web.chat import AgentChatService
from tests.agent_studio_support import serve
from tests.smoke_web_agent import wait_idle
from tests.studio_review_fixtures import make_fixture
from tests.test_chat_attachments import image_bytes


class ImageWiringProvider:
    def __init__(self, requests):
        self.client = SimpleNamespace(close=lambda: None)
        self.requests = requests
        self.calls = 0

    def stream(self, context):
        messages, _tools = context.to_request()
        self.requests.append(deepcopy(messages))
        self.calls += 1
        # The scripted second turn checks real HTTP tool execution and protocol ordering.
        latest = messages[-1]
        if isinstance(latest.get("content"), list) and any(
                block.get("type") == "text" and block.get("text") == "controlled-failure"
                for block in latest["content"]):
            raise RuntimeError("PRIVATE-IMAGE-PROVIDER-FAILURE")
        if self.calls == 1 and latest["role"] == "user" and isinstance(latest["content"], str) and latest["content"].startswith("read:"):
            aid = latest["content"].split(":", 1)[1]
            yield ToolCallDelta(0, "read-image-call", "read_image", json.dumps({"attachment_id": aid}))
            yield ToolCallDelta(1, "list-creators-call", "list_creators", "{}")
            yield StreamEnd("tool_calls")
            return
        if self.calls == 2:
            tail = messages[-3:]
            assert [m["role"] for m in tail] == ["tool", "tool", "user"], tail
            assert [m["tool_call_id"] for m in tail[:2]] == ["read-image-call", "list-creators-call"]
            assert tail[-1]["creatoros_source"] == "tool_images"
            assert any(block.get("type") == "image_ref" for block in tail[-1]["content"])
        yield TextDelta("受控接线完成；不代表真实模型已识别图片。")
        yield StreamEnd("stop")


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        db, runs, producer, _ = make_fixture(root)
        requests = []
        chat_root = root / "chats"
        app = create_app(database=db, run_service=runs, chat_root=chat_root,
                         chat_provider_factory=lambda: ImageWiringProvider(requests))
        try:
            with serve(app) as url, httpx.Client(base_url=url, timeout=15, trust_env=False) as client:
                endpoint = "/api/agent/sessions"
                sid = client.post(endpoint, json={"creator_id": "review-lab"}).json()["id"]
                other = client.post(endpoint, json={"creator_id": "review-lab"}).json()["id"]
                scoped = {"x-creatoros-agent-session": sid}

                def upload(color):
                    raw = image_bytes(color=color)
                    response = client.post(f"{endpoint}/{sid}/attachments", json={
                        "name": "reference.png", "data_base64": base64.b64encode(raw).decode()})
                    assert response.status_code == 201, response.text
                    return response.json()

                images = [upload((index * 30, 40, 90)) for index in range(7)]
                ids = [image["id"] for image in images]
                assert len(set(ids)) == 7
                fetched = client.get(images[0]["url"])
                assert fetched.status_code == 200 and fetched.content == image_bytes(color=(0, 40, 90))
                assert fetched.headers["cache-control"] == "no-store"
                assert client.get(f"{endpoint}/{other}/attachments/{ids[0]}").status_code == 404
                assert client.get(f"{endpoint}/{other}/attachments/{ids[0]}", headers=scoped).status_code == 403
                assert client.post(f"{endpoint}/{other}/image-actions/read", headers=scoped,
                                   json={"attachment_id": ids[0]}).status_code == 403

                def send(attachments, text="", version=0, rid=None):
                    return client.post(f"{endpoint}/{sid}/turns", json={
                        "text": text, "attachment_ids": attachments, "expected_version": version,
                        "request_id": rid or str(uuid4())})

                assert send(ids).status_code == 422
                assert send([ids[0], ids[0]]).status_code == 422
                assert send(["0" * 64]).status_code == 404
                assert send([]).status_code == 422
                assert not requests, "Validation errors must not invoke a provider"
                rid = str(uuid4())
                assert send(ids[:6], rid=rid).status_code == 202  # image-only, boundary count
                done = wait_idle(client, sid)
                assert done["status"] == "idle", done
                assert send(ids[:6], rid=rid).status_code == 202
                assert send(ids[1:7], rid=rid).status_code == 409
                assert send(list(reversed(ids[:6])), rid=rid).status_code == 409
                assert len(requests) == 1
                entry = next(e for e in done["entries"] if e["kind"] == "user")
                assert [a["id"] for a in entry["attachments"]] == ids[:6]
                user = next(m for m in reversed(requests[0]) if m["role"] == "user")
                assert [b["attachment"]["id"] for b in user["content"] if b["type"] == "image_ref"] == ids[:6]
                stored = json.loads((chat_root / sid / "view.json").read_text(encoding="utf-8"))
                assert stored["requests"][0]["attachment_ids"] == ids[:6]

                rid2 = str(uuid4())
                assert send([], "read:" + ids[0], done["version"], rid2).status_code == 202
                done = wait_idle(client, sid)
                assert done["status"] == "idle", done
                assert len(requests) == 3, "Read-image plus catalog batch should take two model calls"
                assert any(e.get("name") == "read_image" and e["status"] == "done" for e in done["entries"]), done
                trace = client.get(f"{endpoint}/{sid}/turn-trace/{rid2}")
                assert trace.status_code == 200, trace.text
                assert "base64," not in trace.text
                # Failure preserves the uploaded file, transcript and user intent, without retry.
                before_calls = len(requests)
                assert send([ids[0]], "controlled-failure", done["version"]).status_code == 202
                done = wait_idle(client, sid)
                assert done["status"] == "failed", done
                assert len(requests) == before_calls + 1
                assert "PRIVATE-IMAGE-PROVIDER-FAILURE" not in json.dumps(done)
                assert client.get(images[0]["url"]).status_code == 200
                # Raw ledger and all persisted request/trace JSON keep stable refs only.
                ledger = load_messages(chat_root / sid / "messages.json")
                assert any(isinstance(m.get("content"), list) for m in ledger)
                for path in (chat_root / sid).rglob("*.json*"):
                    assert "base64," not in path.read_text(encoding="utf-8"), path
                assert producer.calls == 0
            recovered = AgentChatService(chat_root)
            recovered.start()
            try:
                assert recovered.get(sid)["status"] == "failed"
                assert recovered.attachments.get(sid, ids[0])["id"] == ids[0]
                assert recovered.attachments.path(sid, ids[0]).read_bytes() == image_bytes(color=(0, 40, 90))
            finally:
                recovered.shutdown()
            print("chat_images_smoke=passed http=real sqlite=isolated provider=controlled vision_quality=not_tested "
                  "references=passed scope=passed limits=passed replay=passed ordering=passed restart=passed trace=passed")
        finally:
            db.close()


if __name__ == "__main__":
    main()
