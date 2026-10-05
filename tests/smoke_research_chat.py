"""Real loopback HTTP/SQLite; controlled models for wait/race/failure injection."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
from uuid import uuid4

import httpx

from creatoros.ai.types import StreamEnd, TextDelta, ToolCallDelta
from creatoros.integrations.codex import CodexUsage
from creatoros.integrations.producer_skills import ProducerSkillCatalog, skills_root_for
from creatoros.integrations.studio import StudioClientError
from creatoros.integrations.topic_research import CodexTopicResearcher, ResearchReceipt, TopicResearchService
from creatoros.storage import ContentRepository
from creatoros.tools.research_wait import observe_research
from creatoros.web.app import create_app
from creatoros.web.chat import AgentChatService
from tests.agent_studio_support import serve
from tests.smoke_web_agent import wait_idle
from tests.studio_review_fixtures import make_fixture


class ControlledResearcher(CodexTopicResearcher):
    def __init__(self):
        self.started, self.release = Event(), Event()
        self.calls = 0
        self.fail = False

    def preflight(self):
        pass

    def research(self, snapshot, count, instructions, workspace, cancel, *, public_observer=None):
        self.calls += 1
        public_observer({"type": "item.completed", "item": {
            "type": "agent_message", "text": "正在检查权威词典，寻找具体例句。"}})
        self.started.set()
        while not self.release.wait(.05):
            if cancel.is_set():
                raise RuntimeError("stopped")
        if self.fail:
            raise RuntimeError("controlled research failure")
        return SimpleNamespace(thread_id="controlled-http-research", usage=CodexUsage(),
            receipt=ResearchReceipt(candidates=[{
                "title": "evoke 与 invoke", "angle": "唤起感受与援引权威的差异", "rationale": "常见近形词",
                "sources": [{"title": "权威词典", "url": "https://dictionary.cambridge.org/"}]
            }], note="Controlled fixture, not a live research claim"))


class ControlledProvider:
    def __init__(self):
        self.client = SimpleNamespace(close=lambda: None)
        self.requests = []

    def stream(self, context):
        messages, _ = context.to_request()
        self.requests.append(messages)
        if messages[-1]["role"] == "user":
            if messages[-1]["content"] == "other conversation":
                yield TextDelta("这是独立的对话。")
                yield StreamEnd("stop")
                return
            yield ToolCallDelta(0, str(uuid4()), "research_series_topics", json.dumps({
                "series_id": "agent-notes", "count": 1, "instructions": "English synonyms"}))
            yield StreamEnd("tool_calls")
            return
        content = messages[-1]["content"]
        result = json.loads(content.split("\n", 1)[-1] if content.startswith("[tool_error") else content)
        assert result["status"] in {"ready", "failed", "unknown"}, result
        assert "progress" not in result
        if result["status"] == "ready":
            assert result["candidates"][0]["title"] == "evoke 与 invoke"
            yield TextDelta("调研完成：evoke 与 invoke；候选未入队。")
        else:
            assert result.get("error")
            yield TextDelta("调研失败，具体原因：" + result["error"])
        yield StreamEnd("stop")


def wait_progress(client, sid):
    deadline = monotonic() + 10
    while monotonic() < deadline:
        doc = client.get(f"/api/agent/sessions/{sid}").json()
        rows = [e for e in doc["entries"] if e.get("name") == "research_series_topics"]
        if rows and rows[-1].get("research", {}).get("progress", {}).get("events"):
            return doc, rows[-1]
        sleep(.05)
    raise AssertionError("no progress in original chat")


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        db, runs, producer, _ = make_fixture(root)
        researcher = ControlledResearcher()
        research = TopicResearchService(db, ProducerSkillCatalog(skills_root_for(db)), researcher)
        providers = []
        def provider_factory():
            provider = ControlledProvider()
            providers.append(provider)
            return provider
        app = create_app(database=db, run_service=runs, chat_root=root / "chats",
            topic_research_service=research, chat_provider_factory=provider_factory)
        try:
            with serve(app) as base, httpx.Client(base_url=base, timeout=15, trust_env=False) as client:
                endpoint = "/api/agent/sessions"
                def new_session():
                    return client.post(endpoint, json={"creator_id": "review-lab"}).json()["id"]
                def send(sid, text="research", version=0, request_id=None):
                    return client.post(f"{endpoint}/{sid}/turns", json={"text": text,
                        "expected_version": version, "request_id": request_id or str(uuid4())})
                a, b, c = new_session(), new_session(), new_session()
                rid = str(uuid4())
                assert send(a, request_id=rid).status_code == 202
                assert researcher.started.wait(10)
                doc_a, row_a = wait_progress(client, a)
                assert doc_a["status"] == "running" and row_a["status"] == "running"
                assert send(a, request_id=rid).status_code == 202
                assert send(a, version=doc_a["version"]).status_code == 409
                assert send(b).status_code == 202
                _, row_b = wait_progress(client, b)
                assert row_a["research"]["id"] == row_b["research"]["id"]
                assert send(c, "other conversation").status_code == 202
                assert wait_idle(client, c)["status"] == "idle"
                assert researcher.calls == 1
                researcher.release.set()
                for sid in (a, b):
                    doc = wait_idle(client, sid)
                    tool = next(e for e in doc["entries"] if e.get("name") == "research_series_topics")
                    assert tool["status"] == "done" and tool["research"]["status"] == "ready"
                    assert any("evoke" in e.get("text", "") for e in doc["entries"] if e["kind"] == "assistant")
                    ledger = json.loads((root / "chats" / sid / "messages.json").read_text(encoding="utf-8"))
                    assert len([m for m in ledger if m["role"] == "tool"]) == 1
                assert len(providers) == 3 and all(len(p.requests) in (1, 2) for p in providers)
                researcher.fail = True
                failed = new_session()
                assert send(failed).status_code == 202
                failure = wait_idle(client, failed)
                tool = next(e for e in failure["entries"] if e.get("name") == "research_series_topics")
                assert tool["status"] == "failed"
                assert "controlled research failure" in tool["research"]["error"]
                assert researcher.calls == 2  # no automatic re-submit after failure
                assert producer.calls == 0 and len(ContentRepository(db).list_topics("agent-notes")) == 2
                assert client.get("/api/health").json()["codex_available"]
                # Service shutdown interrupts observers and persists unknown outcomes,
                # rather than making a paid duplicate on recovery.
                researcher.fail = False
                researcher.started.clear()
                researcher.release.clear()
                interrupted = new_session()
                assert send(interrupted).status_code == 202
                assert researcher.started.wait(10)
                _, interrupted_row = wait_progress(client, interrupted)
                interrupted_batch = interrupted_row["research"]["id"]
            assert not app.state.chat.threads
            assert app.state.chat.get(interrupted)["status"] == "interrupted"
            assert research.get(interrupted_batch)["status"] == "interrupted"
            restored = AgentChatService(root / "chats")
            restored.start()
            assert restored.get(interrupted)["status"] == "interrupted"
            assert researcher.calls == 3
        finally:
            researcher.release.set()
            db.close()
    # Read failure / bounded wait never re-submit a potentially active job.
    batch = {"id": "a" * 32, "status": "researching", "url": "/series/x"}
    calls = []
    def broken_read(method, path, **kwargs):
        calls.append((method, path))
        raise StudioClientError("read disconnected", code="studio_read_failed")
    context = SimpleNamespace(research_progress=lambda data: None, stopping=Event(), research_wait_timeout_seconds=3)
    result = observe_research(SimpleNamespace(request=broken_read), batch, context)
    assert result.is_error and json.loads(result.content)["status"] == "unknown"
    assert calls == [("GET", "/api/topic-research/" + batch["id"])]
    context.research_wait_timeout_seconds = 0
    result = observe_research(SimpleNamespace(request=broken_read), batch, context)
    assert result.error_type == "research_wait_timeout" and len(calls) == 1
    print("research_chat_smoke=passed terminal=passed public_progress=passed parallel_isolation=passed active_dedup=passed failure=passed bounded_read=passed queue=unchanged")


if __name__ == "__main__":
    main()
