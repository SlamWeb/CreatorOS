"""Isolated launch/progress faults; only --version invokes the real Codex runtime."""
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from creatoros.integrations.codex import CodexProducer, CodexProducerError, CodexUsage
from creatoros.integrations.codex_executable import resolve_codex_executable
from creatoros.integrations.producer_skills import ProducerSkillCatalog, skills_root_for, _write
from creatoros.integrations.topic_research import CodexTopicResearcher, ResearchReceipt, TopicResearchService
from creatoros.storage import Database, ContentRepository, upgrade_database
from creatoros.web import create_app
from tests.smoke_topic_research import seed_batch


def main():
    # The user's real defect: the server has no codex in PATH, but the pinned SDK has a runtime.
    old_path = os.environ.get("PATH")
    with patch.dict(os.environ, {"PATH": ""}):
        executable = resolve_codex_executable()
        version = subprocess.run([executable, "--version"], check=True, capture_output=True, text=True, timeout=15)
        assert "codex" in version.stdout.lower()
        # SDK default discovery is left to its pinned runtime, never a PATH probe.
        with patch.dict(os.environ, {"CREATOROS_CODEX_EXECUTABLE": ""}), \
             patch("creatoros.integrations.topic_research.resolve_codex_executable",
                   side_effect=AssertionError("default SDK preflight must not resolve a CLI")):
            sdk_researcher = CodexTopicResearcher()
            sdk_researcher.preflight()
            assert sdk_researcher._sdk_bin is None
        with patch.dict(os.environ, {"CREATOROS_CODEX_EXECUTABLE": "missing-codex-override.exe"}):
            try:
                resolve_codex_executable()
                raise AssertionError("Invalid explicit override must not fall back to the SDK")
            except FileNotFoundError:
                pass
            try:
                CodexTopicResearcher().preflight()
                raise AssertionError("SDK must also reject an invalid explicit executable")
            except CodexProducerError as error:
                assert error.error_type == "codex_not_found"
    with patch.dict(sys.modules, {"openai_codex": None}):
        try:
            CodexTopicResearcher().preflight()
            raise AssertionError("Missing SDK must fail before launching a worker")
        except CodexProducerError as error:
            assert error.error_type == "codex_sdk_not_installed"
    assert os.environ.get("PATH") == old_path
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        url = "sqlite:///" + (root / "test.db").as_posix()
        upgrade_database(url)
        db = Database(url)
        service = TopicResearchService(db, ProducerSkillCatalog(skills_root_for(db)))
        with TestClient(create_app(database=db, topic_research_service=service)) as client:
            creator = client.post("/api/creators", json={"display_name": "isolated research"}).json()
            sid = client.post(f"/api/creators/{creator['id']}/series", json={"name": "test"}).json()["id"]
            service.researcher.executable = "missing-codex-override.exe"
            failed = service.submit(sid)
            assert failed["status"] == "failed" and failed["error_type"] == "codex_not_found"
            assert service.worker is None and failed["attempt"] == 0
            assert failed["progress"]["stage"] == "failed" and not failed["candidates"]
            assert "不是登录或额度" in failed["error"]

            old = seed_batch(service, sid)
            old["status"] = "failed"
            _write(service._path(old["id"]), old)
            error_path = service.root / (old["id"] + "-error.txt")
            error_path.write_text("未找到 codex CLI。", encoding="utf-8")
            before = service._path(old["id"]).read_bytes(), error_path.read_bytes()
            assert service.get(old["id"])["error_type"] == "codex_not_found"
            assert before == (service._path(old["id"]).read_bytes(), error_path.read_bytes())

            started, release = Event(), Event()
            calls = []
            class ObservedResearch(CodexTopicResearcher):
                def research(self, snapshot, count, instructions, workspace, cancel, *, public_observer=None):
                    calls.append(instructions)
                    public_observer({"type": "thread.started", "thread_id": "local-fault"})
                    public_observer({"type": "item.completed", "item": {"type": "reasoning", "text": "PRIVATE_REASONING"}})
                    for i in range(40):
                        public_observer({"type": "item.completed", "item": {"type": "agent_message", "text":
                                         f"step {i} api_key=super-secret\nD:\\private\\secret.json\n/home/private/file " + "x" * 1200}})
                    public_observer({"type": "item.completed", "item": {"type": "web_search", "query": "official source"}})
                    started.set()
                    assert release.wait(5)
                    return SimpleNamespace(receipt=ResearchReceipt(candidates=[], note="empty local fixture"),
                                           thread_id="local-fault", usage=CodexUsage())
            service.researcher = ObservedResearch(project_root=root, generated_images_root=root, executable=executable)
            job = service.submit(sid, 1, "Ａ  topic")
            assert started.wait(5)
            assert service.submit(sid, 1, "a\n topic")["id"] == job["id"]
            active = service.get(job["id"])
            assert active["status"] == "researching" and len(active["progress"]["events"]) == 30
            assert all(len(event["text"]) <= 1000 for event in active["progress"]["events"])
            visible = json.dumps(active["progress"])
            assert "PRIVATE_REASONING" not in visible and "super-secret" not in visible
            assert "private" not in visible and "official source" in visible
            try:
                service.submit(sid, 2, "different")
                raise AssertionError("Distinct active request must be busy")
            except ValueError as error:
                assert "已有选题调研" in str(error)
            release.set()
            service.worker.join(5)
            assert service.get(job["id"])["progress"]["stage"] == "ready"
            other = service.submit(sid, 1, "a topic")
            service.worker.join(5)
            assert other["id"] != job["id"] and len(calls) == 2
            assert not ContentRepository(db).list_topics(sid)
        db.close()

        # A real no-cost subprocess proves the new observer consumes the same stdout
        # once, preserving receipt parsing and usage; no Codex/model call here.
        class JsonlProducer(CodexProducer):
            receipt_model = ResearchReceipt
            def _command(self, *_):
                events = [{"type": "thread.started", "thread_id": "jsonl-test"},
                          {"type": "item.completed", "item": {"type": "reasoning", "text": "private"}},
                          {"type": "item.completed", "item": {"type": "web_search", "query": "source"}},
                          {"type": "item.completed", "item": {"type": "agent_message", "text": '{"candidates":[],"note":"fixture"}'}},
                          {"type": "turn.completed", "usage": {"input_tokens": 12}}]
                script = "import sys,json; sys.stdin.read(); events=json.loads(" + repr(json.dumps(events)) + "); [print(json.dumps(e), flush=True) for e in events]"
                return [sys.executable, "-c", script]
        events = []
        producer = JsonlProducer(project_root=root, generated_images_root=root)
        result = producer._execute("fixture", root, public_observer=events.append)
        assert result.thread_id == "jsonl-test" and result.usage.input_tokens == 12
        assert [event.get("item", {}).get("type") for event in events] == [None, "web_search", "agent_message"]
    print("topic_research_observation=passed bundled_runtime=" + version.stdout.strip() + " pathless=passed override=blocked preflight=failed progress=bounded dedup=passed old_evidence=unchanged")


if __name__ == "__main__":
    main()
