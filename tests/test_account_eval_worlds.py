"""Local real-storage/HTTP controls, not DeepSeek or Codex quality scores.

No model is faked into success: provider creation raises if a test accidentally
submits a new turn. E09 is explicitly declared fault injection only.
"""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from time import monotonic, sleep

import httpx
from fastapi.testclient import TestClient

from creatoros.context import RuntimeContext
from creatoros.evaluation.fixture import E01Fixture
from creatoros.integrations.topic_research import CodexTopicResearcher
from creatoros.session.snapshot import load_messages, save_messages
from creatoros.tools.builtins import read_file, read_tool_result
from creatoros.tools.studio import queue_topics
from tests.agent_studio_support import serve


def no_model():
    raise AssertionError("纯本地夹具测试不得创建或伪造模型。")


class AccountEvalWorldTests(unittest.TestCase):
    def test_all_12_worlds_persist_in_explicit_isolated_root(self):
        with TemporaryDirectory() as temporary:
            for index in range(1, 13):
                with self.subTest(case=index):
                    f = E01Fixture(Path(temporary) / f"E{index:02}", no_model, case_id=f"E{index:02}")
                    try:
                        self.assertTrue((f.root / "studio.db").is_file())
                        self.assertEqual(f.variables()["series_a_id"], f.expected_series[0]["id"])
                        self.assertTrue(all(p.resolve().is_relative_to(f.root) for p in f.business_roots))
                        self.assertEqual(f.external_attempts, [])
                        if index not in {1, 2, 10}:
                            self.assertIn(f"e{index:02}", f.oracle())
                    finally:
                        f.close()

    def test_e03_injection_is_real_candidate_data_and_external_write_is_rejected(self):
        with TemporaryDirectory() as temporary:
            f = E01Fixture(Path(temporary) / "world", no_model, case_id="E03")
            try:
                with TestClient(f.app) as client:
                    sid = client.post("/api/agent/sessions", json={"creator_id": f.creator_a}).json()["id"]
                    headers = {"x-creatoros-agent-session": sid}
                    oracle = f.oracle()["e03"]
                    before = f.state()
                    data = client.get("/api/topic-research/" + oracle["batch_id"], headers=headers).json()
                    self.assertEqual(len(data["candidates"]), 4)
                    self.assertIn(oracle["injection_text"], data["candidates"][1]["angle"])
                    denied = client.post(f"/api/series/{f.variables()['series_b_id']}/queue", headers=headers,
                        json={"request_id": "eval-denied-write", "topics": [{"title": "不应落盘"}]})
                    self.assertEqual(denied.status_code, 403)
                    self.assertEqual(before, f.state())
            finally:
                f.close()

    def test_e04_candidate_order_is_persisted_and_not_a_tool_mock(self):
        with TemporaryDirectory() as temporary:
            f = E01Fixture(Path(temporary) / "world", no_model, case_id="E04")
            try:
                with TestClient(f.app) as client:
                    data = client.get(f"/api/series/{f.variables()['series_a_id']}/topic-library?state=pending").json()
                    self.assertEqual([r["title"] for r in data["items"]],
                                     [r["title"] for r in f.oracle()["e04"]["candidates"]])
                    self.assertEqual(data["page"]["total"], 4)
            finally:
                f.close()

    def test_e05_actual_sibling_archive_cannot_be_read_in_new_session(self):
        with TemporaryDirectory() as temporary:
            f = E01Fixture(Path(temporary) / "world", no_model, case_id="E05")
            try:
                oracle = f.oracle()["e05"]
                sid = f.app.state.chat.create(f.creator_a)["id"]
                context = RuntimeContext(project_root=f.root, session_file=f.root / "sessions" / sid / "messages.json",
                    archive_only_reads=True, creator_id=f.creator_a, agent_session_id=sid)
                missing = read_tool_result(oracle["result_ref"], context=context)
                denied = read_file(oracle["archive_path"], context=context)
                self.assertTrue(missing.is_error)
                self.assertEqual(denied.error_type, "path_out_of_scope")
                self.assertNotIn(oracle["marker"], missing.content + denied.content)
                for relative, digest in oracle["source_hashes"].items():
                    self.assertEqual(hashlib.sha256((f.root / relative).read_bytes()).hexdigest(), digest)
            finally:
                f.close()

    def test_e06_commit_then_undelivered_result_repairs_and_replay_does_not_execute(self):
        with TemporaryDirectory() as temporary:
            f = E01Fixture(Path(temporary) / "world", no_model, case_id="E06")
            try:
                with serve(f.app) as base:
                    event = f.controls.execute("prepare_interruption", base_url=base)
                    oracle = f.oracle()["e06"]
                    self.assertEqual(len(event["actual_receipt"]["topic_ids"]), 1)
                    sid = event["seeded_session_id"]
                    path = f.root / "sessions" / sid / "messages.json"
                    self.assertFalse(any(r.get("role") == "tool" for r in load_messages(path)))
                    f.app.state.chat.start()
                    doc = f.app.state.chat.get(sid)
                    self.assertEqual(doc["status"], "interrupted")
                    self.assertEqual(doc["entries"][-1]["status"], "unknown")
                    self.assertIn("结果未知", load_messages(path)[-1]["content"])
                    before = f.state()
                    replay = f.controls.execute("replay_same_chat_request", session_id=sid, base_url=base)
                    self.assertEqual(replay["replay"]["status"], 202)
                    self.assertEqual(replay["mismatched"]["status"], 409)
                    self.assertEqual(before, f.state())
                    self.assertEqual(len([r for r in before["database"]["topics"]
                                          if r["series_id"] == oracle["series_id"]]), 1)
            finally:
                f.close()

    def test_e07_controller_changes_only_audience_and_leaves_queue_for_ui(self):
        with TemporaryDirectory() as temporary:
            f = E01Fixture(Path(temporary) / "world", no_model, case_id="E07")
            try:
                before = f.state()
                f.record_request_tree("before-request")
                f.controls.execute("change_current_data")
                after = f.state()
                self.assertEqual(after, f.oracle()["expected_after"])
                self.assertEqual(before["database"]["topics"], after["database"]["topics"])
                f.record_request_tree("after-request")
                trees = f.oracle()["request_trees"]
                self.assertEqual(trees["before-request"]["series"][0]["audience"], "英语学习者")
                self.assertEqual(trees["after-request"]["series"][0]["audience"], "大学英语考试学习者")
            finally:
                f.close()

    def test_e08_only_real_sdk_research_is_enabled_without_running_it(self):
        with TemporaryDirectory() as temporary:
            f = E01Fixture(Path(temporary) / "world", no_model, case_id="E08")
            try:
                self.assertIsInstance(f.research.researcher, CodexTopicResearcher)
                self.assertEqual(f.research.submit.__func__.__name__, "submit")
                self.assertEqual(f.controls.research_results()["batches"], [])
                self.assertEqual(f.oracle()["e08"]["candidates"], [])
                self.assertTrue(f.oracle()["e08"]["codex_real"])
            finally:
                f.close()

    def test_e09_two_declared_fault_variants_persist_without_sdk_or_success_answers(self):
        with TemporaryDirectory() as temporary:
            for variant in ("failed", "unknown"):
                f = E01Fixture(Path(temporary) / variant, no_model, case_id="E09")
                try:
                    f.controls.arm_research_failure(variant)
                    with TestClient(f.app) as client:
                        response = client.post(f"/api/series/{f.variables()['series_a_id']}/topic-research",
                                               json={"count": 10, "instructions": "常用常考"})
                        self.assertEqual(response.status_code, 202)
                        batch = response.json()
                        self.assertEqual(batch["status"], variant)
                        self.assertEqual(batch["candidates"], [])
                        self.assertEqual(f.research._load(batch["id"])["status"], variant)
                        record = f.oracle()["e09"]["variants"][variant]
                        self.assertFalse(record["codex_real"])
                        self.assertTrue(record["fault_injection"])
                        self.assertIsNone(f.research.worker)
                finally:
                    f.close()

    def test_research_diagnostic_allowlist_does_not_allow_arbitrary_changed_files(self):
        from creatoros.evaluation.grader_suite import _research_state
        with TemporaryDirectory() as temporary:
            f = E01Fixture(Path(temporary) / "world", no_model, case_id="E09")
            try:
                before = f.state()
                with TestClient(f.app) as client:
                    response = client.post(f"/api/series/{f.variables()['series_a_id']}/topic-research",
                                           json={"count": 10, "instructions": "常用常考"})
                    self.assertEqual(response.status_code, 202)
                    batch_id = response.json()["id"]
                    research = f.controls.research_results()
                    evidence = {"before": before, "after": f.state(), "oracle": f.oracle(),
                                "research_records": research["research_records"]}
                    self.assertEqual(_research_state(evidence, f.oracle()["e09"])[0], "passed")
                    artifacts = research["research_records"][0]["artifact_paths"]
                    self.assertTrue(any(path.endswith(batch_id + "-error.txt") for path in artifacts))
                    illegal = f.research.root / "unrelated-diagnostic.txt"
                    illegal.write_text("不是已记录batch的合法诊断", encoding="utf-8")
                    evidence["after"] = f.state()
                    evidence["research_records"] = f.controls.research_results()["research_records"]
                    self.assertEqual(_research_state(evidence, f.oracle()["e09"])[0], "failed")
            finally:
                f.close()

    def test_e06_real_host_async_events_release_http_lock_and_keep_capture_start(self):
        from creatoros.evaluation.browser import BrowserEvaluation
        with TemporaryDirectory() as temporary:
            host = BrowserEvaluation("E06", temporary)
            try:
                with serve(host.app) as base, httpx.Client(base_url=base, timeout=15, trust_env=False) as client:
                    def event(name, session_id=None):
                        accepted = client.post("/__live_eval__/event", json={"name": name, "session_id": session_id})
                        self.assertEqual(accepted.status_code, 202)
                        url = "/__live_eval__/event-result/" + accepted.json()["event_id"]
                        deadline = monotonic() + 10
                        while monotonic() < deadline:
                            response = client.get(url)
                            if response.status_code != 202:
                                self.assertEqual(response.status_code, 200, response.text)
                                return response.json()
                            sleep(0.05)
                        self.fail("控制器nested HTTP死锁/超时")
                    prepared = event("prepare_interruption")
                    sid = prepared["seeded_session_id"]
                    reloaded = event("reload_service", sid)
                    self.assertFalse(reloaded["process_restart"])
                    self.assertEqual(reloaded["session"]["status"], "interrupted")
                    path = host.fixture.root / "sessions" / sid / "messages.json"
                    self.assertEqual(host.capture_message_start, len(load_messages(path)))
                    replay = event("replay_request", sid)
                    self.assertEqual(replay["mismatched"]["status"], 409)
                    self.assertIsNone(host.captured)
                    self.assertFalse(host.app.state.chat.active)
                    posts = [row for row in host.network if row["method"] == "POST" and row["path"].endswith("/turns")]
                    self.assertEqual(len(posts), 2)
                    self.assertTrue(all(row["controller"] for row in posts))
            finally:
                host.fixture.close()

    def test_e11_real_queue_receipt_replay_uses_ledger_and_marker_exactly(self):
        with TemporaryDirectory() as temporary:
            f = E01Fixture(Path(temporary) / "world", no_model, case_id="E11")
            try:
                with serve(f.app) as base:
                    doc = f.app.state.chat.create(f.creator_a)
                    path = f.root / "sessions" / doc["id"] / "messages.json"
                    context = RuntimeContext(project_root=f.root, studio_url=base, session_file=path,
                        creator_id=f.creator_a, agent_session_id=doc["id"], user_request_id="local-controlled-turn")
                    oracle = f.oracle()["e11"]
                    arguments = {"series_id": oracle["series_id"], "topics": oracle["expected_topics"]}
                    result = queue_topics(**arguments, context=context)
                    self.assertFalse(result.is_error, result.content)
                    save_messages(load_messages(path) + [
                        {"role": "assistant", "content": "", "tool_calls": [{"id": "local-call",
                            "name": "queue_topics", "arguments": json.dumps(arguments)}]},
                        {"role": "tool", "tool_call_id": "local-call", "content": result.content}], path)
                    before = f.state()
                    replay = f.controls.execute("replay_queue_receipt", session_id=doc["id"], base_url=base)
                    self.assertEqual(replay["replay"]["status"], 201)
                    self.assertTrue(replay["replay"]["body"]["deduplicated"])
                    self.assertEqual(replay["replay"]["body"]["topic_ids"], json.loads(result.content)["topic_ids"])
                    self.assertEqual(before, f.state())
            finally:
                f.close()

    def test_e12_legal_concurrent_cas_edit_rejects_old_digest_and_preserves_history(self):
        with TemporaryDirectory() as temporary:
            f = E01Fixture(Path(temporary) / "world", no_model, case_id="E12")
            try:
                with TestClient(f.app) as client:
                    oracle = f.oracle()["e12"]
                    f.controls.on_skill_read(oracle["skill_id"])
                    event = f.controls.before_skill_update(oracle["skill_id"])
                    self.assertEqual(f.state(), f.oracle()["expected_after"])
                    response = client.put(f"/api/producer-skills/{oracle['skill_id']}/files/content", json={
                        "path": "SKILL.md", "expected_digest": oracle["current_digest"],
                        "content": oracle["current_text"].replace("解释只用英文", "解释使用中英双语")})
                    self.assertEqual(response.status_code, 409)
                    content = f.catalog.read_skill_file(oracle["skill_id"], "SKILL.md")
                    self.assertIn(oracle["concurrent_paragraph"], content["content"])
                    self.assertEqual(content["digest"], event["receipt"]["digest"])
                    self.assertIsNone(f.controls.before_skill_update(oracle["skill_id"]))
                    from creatoros.storage import ContentRun
                    with f.database.session() as session:
                        run = session.get(ContentRun, oracle["historical_run_id"])
                    digest = hashlib.sha256(json.dumps(run.input_snapshot_json, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                    self.assertEqual(digest, oracle["frozen_input_sha256"])
            finally:
                f.close()

    def test_e12_browser_host_hook_requires_real_scoped_read_and_returns_actual_cas_conflict(self):
        from creatoros.evaluation.browser import BrowserEvaluation
        with TemporaryDirectory() as temporary:
            host = BrowserEvaluation("E12", temporary)
            try:
                f = host.fixture
                oracle = f.oracle()["e12"]
                with serve(host.app) as base, httpx.Client(base_url=base, timeout=15, trust_env=False) as client:
                    sid = client.post("/api/agent/sessions", json={"creator_id": f.creator_a}).json()["id"]
                    endpoint = f"/api/producer-skills/{oracle['skill_id']}/files/text"
                    self.assertEqual(client.get(endpoint, params={"path": "SKILL.md"}).status_code, 200)
                    self.assertFalse(f.controls.skill_read_seen)
                    headers = {"x-creatoros-agent-session": sid}
                    current = client.get(endpoint, params={"path": "SKILL.md"}, headers=headers)
                    self.assertEqual(current.status_code, 200)
                    self.assertTrue(f.controls.skill_read_seen)
                    proposed = current.json()["content"].replace("解释只用英文", "解释采用中英双语")
                    rejected = client.put(f"/api/producer-skills/{oracle['skill_id']}/files/content", headers=headers,
                        json={"path": "SKILL.md", "content": proposed, "expected_digest": current.json()["digest"]})
                    self.assertEqual(rejected.status_code, 409)
                    self.assertEqual(rejected.json()["error"]["code"], "skill_digest_conflict")
                    self.assertEqual(rejected.json()["error"]["current_digest"], oracle["concurrent_digest"])
                    self.assertEqual(f.state(), f.oracle()["expected_after"])
                    self.assertEqual(len(f.controls.hook_events), 1)
                    self.assertIsNone(host.captured)
                    self.assertEqual(f.external_attempts, [])
            finally:
                host.fixture.close()


if __name__ == "__main__":
    unittest.main()
