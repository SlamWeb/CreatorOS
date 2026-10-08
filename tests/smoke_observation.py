"""Read-only HTTP/SQLite/local-file observation; no worker or model execution."""
import hashlib
from contextlib import ExitStack
import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from creatoros.session.context_trace import ContextTrace
from creatoros.session.request_trace import RequestSnapshots
from creatoros.storage import (ContentAttempt, ContentAttemptStatus, ContentRepository,
    ContentRevision, ContentRun, ContentRunStatus, CreatorPlatform, Database, Series, TopicSource, upgrade_database)
from creatoros.web.chat import AgentChatService
from creatoros.web.agent_scope import AgentScopeGuard
from creatoros.web.observation import ObservationService, encode, clean, safe_path
from creatoros.web.observation_routes import observation_routes


def save(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def hashes(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


def main():
    with TemporaryDirectory() as temporary, ExitStack() as cleanup:
        root = Path(temporary)
        url = f"sqlite:///{(root / 'test.db').as_posix()}"
        upgrade_database(url)
        db = Database(url)
        cleanup.callback(db.close)
        repository = ContentRepository(db)
        repository.create_creator(creator_id="creator-a", display_name="观察测试账号", platform=CreatorPlatform.XIAOHONGSHU)
        repository.create_creator(creator_id="creator-b", display_name="另一账号", platform=CreatorPlatform.XIAOHONGSHU)
        repository.create_series(series_id="series-a", creator_id="creator-a", name="测试栏目", description="说明", audience="读者", skill_name="knowledge-to-carousel")
        repository.add_topic(topic_id="topic-a", series_id="series-a", title="真实 SQLite 选题", source=TopicSource.MANUAL)
        run_id, revision_id, attempt_id = [str(uuid4()) for _ in range(3)]
        frozen = {"creator_id": "creator-a", "series_id": "series-a", "topic_id": "topic-a"}
        directory = root / "outputs" / "creator-a" / "series-a" / run_id / "revision-001" / "attempt-001"
        with db.session() as session:
            session.add(ContentRun(id=run_id, topic_id="topic-a", idempotency_key="test-run", input_snapshot_json=frozen))
            session.flush()
            session.add(ContentRevision(id=revision_id, content_run_id=run_id, revision_number=1, production_input_json=frozen))
            session.flush()
            session.add(ContentAttempt(id=attempt_id, revision_id=revision_id, attempt_number=1,
                status=ContentAttemptStatus.SUCCEEDED, producer_thread_id="sdk-thread", output_directory=str(directory)))
        save(directory / "worker_receipt.json", {"protocol": "creatoros-worker-v1", "thread_id": "sdk-thread", "turns": [{"id": "sdk-turn", "status": "completed", "phase": "production"}]})
        save(directory / "worker_task.json", {"protocol": "creatoros-worker-v1", "scope": frozen, "kind": "production"})
        long_output = "完整公开输出" * 20000
        events = [
            {"schema_version": 1, "method": "item/completed", "thread_id": "sdk-thread", "turn_id": "sdk-turn", "item_id": "cmd-1", "item_type": "commandExecution", "status": "completed", "payload": {"item": {"id": "cmd-1", "type": "commandExecution", "command": "example", "aggregated_output": long_output, "data": [1, 2, 3]}}},
            {"schema_version": 1, "method": "item/completed", "thread_id": "sdk-thread", "turn_id": "sdk-turn", "item_id": "hidden", "item_type": "reasoning", "payload": {"item": {"type": "reasoning", "text": "NEVER-EXPOSE-REASONING"}}},
            {"schema_version": 1, "method": "item/completed", "thread_id": "different-thread", "turn_id": "sdk-turn", "item_id": "wrong", "payload": {"item": {"type": "agentMessage", "text": "NEVER-CROSS-THREAD"}}},
        ]
        (directory / "codex_public_events.jsonl").write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + '\n{"partial":', encoding="utf-8")
        (directory / "production_request.txt").write_text("已保存生产实际输入", encoding="utf-8")
        chat = AgentChatService(root / "chats", provider_factory=lambda: (_ for _ in ()).throw(AssertionError("must not run model")), creator_lookup=repository.get_creator)
        sid = chat.create("creator-a")["id"]
        turn_id, request_id = str(uuid4()), uuid4().hex
        view = chat._read(sid)
        view["requests"] = [{"id": turn_id, "text": "生产这条选题", "status": "idle"}]
        save(chat._path(sid), view)
        session_file = root / "chats" / sid / "messages.json"
        ContextTrace(session_file).append({"request_id": request_id, "turn_id": turn_id, "status": "succeeded", "event": "finished"})
        RequestSnapshots(session_file).write(request_id, {"schema_version": 1, "request_id": request_id, "turn_id": turn_id,
            "context": {"messages": [{"role": "user", "content": "生产这条选题"}], "tools": []},
            "response": {"role": "assistant", "content": "准备提交任务", "tool_calls": [{"id": "call-1", "name": "start_content_run", "arguments": '{"topic_id":"topic-a"}'}]},
            "tool_results": [{"tool_call_id": "call-1", "name": "start_content_run", "content": json.dumps({"run_id": run_id, "data": ["preserved"]}), "is_error": False}]})
        research = SimpleNamespace(root=root / "research")
        discussions = SimpleNamespace(root=root / "discussions")
        extractions = SimpleNamespace(root=root / "extractions")
        batch_id = uuid4().hex
        save(research.root / "batches" / f"{batch_id}.json", {"id": batch_id, "series_id": "series-a", "snapshot": {"creator": {"id": "creator-a"}}, "attempt": 1, "attempts": [], "status": "failed", "instructions": "历史调研"})
        (research.root / f"{batch_id}-error.txt").write_text("真实已保存的启动错误\napi_key=raw-secret", encoding="utf-8")
        save(research.root / "work" / batch_id / "1" / "worker_receipt.json", {"thread_id": "old-thread", "turns": []})
        (research.root / "work" / batch_id / "1" / "codex_trace.jsonl").write_text(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "旧摘要"}}) + "\n", encoding="utf-8")
        extraction_id, action_id = "e" * 64, "f" * 64
        extraction_dir = extractions.root / "jobs" / extraction_id
        save(extraction_dir / "job.json", {"id": extraction_id, "status": "ready", "operation": "revise", "thread_id": "revised-thread",
            "progress_directory": f"revisions/{action_id}", "actions": {action_id: {"operation": "revise", "value": "明确改稿要求"}}, "trials": []})
        (extraction_dir / "codex_trace.jsonl").write_text(json.dumps({"type": "thread.started", "thread_id": "initial-thread"}) + "\n", encoding="utf-8")
        save(extraction_dir / "public_events/index.json", {"items": [{"id": 1}]})
        save(extraction_dir / "public_events/1.json", {"id": 1, "item_type": "commandExecution", "text": "EXISTING_FULL_TOOL_RESULT", "status": "completed"})
        revision_dir = extraction_dir / "revisions" / action_id
        revision_dir.mkdir(parents=True)
        (revision_dir / "codex_public_events.jsonl").write_text(json.dumps({"method": "capture/started", "turn_id": "live-turn", "thread_id": "revised-thread", "status": "running", "payload": {}}) + "\n", encoding="utf-8")
        service = ObservationService(db, chat=chat, research=research, discussions=discussions,
                                     extractions=extractions, runs=SimpleNamespace(output_root=root / "outputs"))
        app = FastAPI()
        app.include_router(observation_routes(service))
        guard = AgentScopeGuard(db, chat, research, discussions)
        @app.middleware("http")
        async def scoped(request, call_next):
            denied = await guard.check(request)
            return denied if denied is not None else await call_next(request)
        before = hashes(root)
        with TestClient(app) as client:
            def tree(kind=None, *refs, **params):
                response = client.get("/api/observation/tree", params={**({"parent": encode(kind, *refs)} if kind else {}), **params})
                assert response.status_code == 200, response.text
                assert response.headers["cache-control"] == "no-store"
                return response.json()
            def detail(kind, *refs):
                response = client.get("/api/observation/detail", params={"node_id": encode(kind, *refs)})
                assert response.status_code == 200, response.text
                return response.json()
            assert tree(limit=1)["has_more"]
            assert tree(offset=999)["items"] == []
            assert {n["kind"] for n in tree("creator", "creator-a")["items"]} == {"series", "sessions"}
            assert tree("topic", "topic-a")["items"][0]["id"] == encode("run", run_id)
            assert tree("session", sid)["items"][0]["id"] == encode("user_turn", sid, turn_id)
            assert tree("request", sid, turn_id, request_id)["items"][0]["label"] == "start_content_run"
            task = detail("run", run_id)
            assert any(item["kind"] == "codex_item" for item in task["timeline"])
            assert any(item["label"] == "当前业务状态" for item in task["timeline"])
            assert next(item for item in task["timeline"] if item["label"] == "CreatorOS 接收生产任务")["status"] is None
            assert next(item for item in task["timeline"] if "执行开始" in item["label"])["status"] is None
            assert any(link["node_id"] == encode("tool_call", sid, turn_id, request_id, "call-1") for link in task["links"] if "node_id" in link)
            tool = detail("tool_call", sid, turn_id, request_id, "call-1")
            assert [item["kind"] for item in tool["timeline"]] == ["user", "model_request", "assistant", "tool"]
            assert any(link["node_id"] == encode("run", run_id) for link in tool["links"])
            assert "preserved" in json.dumps(tool)
            assert [a["kind"] for a in tool["ancestors"]] == ["creator", "sessions", "session", "user_turn", "request"]
            worker = detail("worker", "attempt", attempt_id)
            assert any("输入" in s["title"] for s in worker["sections"])
            assert any("损坏" in warning for warning in worker["warnings"])
            items = tree("worker_turn", "attempt", attempt_id, "sdk-turn")["items"]
            assert len(items) == 2  # A reasoning lifecycle is visible, its private body is not.
            hidden = detail("worker_item", "attempt", attempt_id, "sdk-turn", "hidden")
            assert "NEVER-EXPOSE-REASONING" not in json.dumps(hidden)
            item = detail("worker_item", "attempt", attempt_id, "sdk-turn", "cmd-1")
            assert item["sections"][-1]["content"][0]["payload"]["item"]["aggregated_output"] == long_output
            assert item["sections"][-1]["content"][0]["payload"]["item"]["data"] == [1, 2, 3]
            assert "NEVER-" not in json.dumps(item)
            legacy = detail("worker", "research_attempt", batch_id, "1")
            assert any("旧摘要" in warning for warning in legacy["warnings"])
            assert tree("worker", "research_attempt", batch_id, "1")["items"][0]["status"] == "partial"
            research_detail = detail("research", batch_id)
            assert any(item["label"] == "已保存错误诊断" and "真实已保存的启动错误" in item["content"] for item in research_detail["timeline"])
            assert "raw-secret" not in json.dumps(research_detail)
            extraction_nodes = tree("extraction", extraction_id)["items"]
            assert len(extraction_nodes) == 2
            original_extraction = detail("extraction_execution", extraction_id, "initial")
            assert "EXISTING_FULL_TOOL_RESULT" in json.dumps(original_extraction), "legacy detail must coexist with codex metadata"
            live_worker = detail("worker", "extraction_execution", extraction_id, "revise", action_id)
            assert live_worker["active"]
            for invalid in ("not-base64", encode("worker", "attempt", "../../private"), encode("request", sid, str(uuid4()), request_id), encode("worker_item", "attempt", attempt_id, "other-turn", "cmd-1")):
                assert client.get("/api/observation/detail", params={"node_id": invalid}).status_code == 404
            assert client.get("/api/observation/tree", params={"limit": 101}).status_code == 422
            assert client.post("/api/observation/tree").status_code == 405
            assert client.get("/api/observation/tree", headers={"x-creatoros-agent-session": sid}).status_code == 403
            assert hashes(root) == before, "observation GET changed persisted state"
            # A crashed SDK can leave running evidence after the host has stopped.
            save(directory / "worker_receipt.json", {"thread_id": "sdk-thread", "turns": [{"id": "sdk-turn", "status": "running"}]})
            stale_events = [{"method": "item/started", "thread_id": "sdk-thread", "turn_id": "sdk-turn", "item_id": "stale-call", "item_type": "commandExecution", "status": "running", "payload": {"item": {"id": "stale-call", "type": "commandExecution"}}}]
            (directory / "codex_public_events.jsonl").write_text(json.dumps(stale_events[0]) + "\n", encoding="utf-8")
            with db.session() as session:
                session.get(ContentRun, run_id).status = ContentRunStatus.FAILED
                session.get(ContentAttempt, attempt_id).status = ContentAttemptStatus.RUNNING
            stale_before = hashes(root)
            for selection in (("worker", "attempt", attempt_id), ("worker_turn", "attempt", attempt_id, "sdk-turn"),
                              ("worker_item", "attempt", attempt_id, "sdk-turn", "stale-call"), ("attempt", attempt_id)):
                stopped = detail(*selection)
                assert not stopped["active"], selection
                assert any("最后保存状态" in note for note in stopped["warnings"]), selection
                if selection[0] != "worker":
                    assert stopped["status"] == "unknown", selection
            assert tree("worker", "attempt", attempt_id)["items"][0]["status"] == "unknown"
            assert tree("worker_turn", "attempt", attempt_id, "sdk-turn")["items"][0]["status"] == "unknown"
            assert hashes(root) == stale_before, "liveness projection rewrote evidence"
            # A later research attempt cannot make an earlier attempt live.
            research_doc = json.loads((research.root / "batches" / f"{batch_id}.json").read_text(encoding="utf-8"))
            research_doc.update(status="researching", attempt=2)
            save(research.root / "batches" / f"{batch_id}.json", research_doc)
            save(research.root / "work" / batch_id / "1" / "worker_receipt.json", {"thread_id": "old-thread", "turns": [{"id": "old-turn", "status": "running"}]})
            assert not detail("worker", "research_attempt", batch_id, "1")["active"]
            assert detail("worker_turn", "research_attempt", batch_id, "1", "old-turn")["status"] == "unknown"
            assert detail("worker", "research_attempt", batch_id, "2")["active"]
            # Current revise work cannot make the original extraction live.
            save(extraction_dir / "worker_receipt.json", {"thread_id": "initial-thread", "turns": [{"id": "initial-turn", "status": "running"}]})
            assert not detail("worker", "extraction_execution", extraction_id, "initial")["active"]
            assert detail("worker_turn", "extraction_execution", extraction_id, "initial", "initial-turn")["status"] == "unknown"
            assert detail("worker", "extraction_execution", extraction_id, "revise", action_id)["active"]
            with db.session() as session:
                session.get(Series, "series-a").creator_id = "creator-b"
            assert tree("topic", "topic-a")["items"] == []
            assert tree("group", "historical")["items"][0]["id"] == encode("run", run_id)
            assert tree("researches", "series-a")["items"] == []
            outside = root / "private.json"
            save(outside, {"secret": "not-for-worker"})
            (directory / "worker_task.json").unlink()
            try:
                os.symlink(outside, directory / "worker_task.json")
            except OSError:
                pass  # Windows may not grant unprivileged link creation.
            else:
                assert "not-for-worker" not in json.dumps(detail("worker", "attempt", attempt_id))
            if os.name == "nt":
                target, junction = root / "junction-target", root / "internal-junction"
                target.mkdir()
                made = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(target)],
                                      capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                assert made.returncode == 0, "could not create isolated junction fixture"
                try:
                    for managed_root, candidate in ((root, junction / "missing.json"), (junction, junction / "missing.json")):
                        try:
                            safe_path(managed_root, candidate)
                        except ValueError:
                            pass
                        else:
                            raise AssertionError("same-root Windows junction must be rejected")
                finally:
                    junction.rmdir()
        assert clean({"data": [1], "token": "hidden", "reasoning_content": "private"}) == {"data": [1], "token": "[REDACTED]", "reasoning_content": "[OMITTED]"}
    print("observation_smoke=passed http=real sqlite=real files=isolated model_calls=0 state_writes=0")


if __name__ == "__main__":
    main()
