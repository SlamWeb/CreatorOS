"""Isolated HTTP/SQLite and connection fault injection; never retry a real plan."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from creatoros.ai.types import TextDelta, ToolCallDelta, StreamEnd
from creatoros.storage.models import AgentPlan
from creatoros.session.snapshot import load_messages, save_messages
from creatoros.web.app import create_app
from tests.agent_studio_support import serve
from tests.studio_review_fixtures import make_fixture
from tests.test_plan_continuation import wait_for


class FaultProvider:
    def __init__(self, owner, mode):
        self.owner, self.mode, self.calls = owner, mode, 0
        self.client = SimpleNamespace(close=lambda: None)

    def stream(self, context):
        self.calls += 1
        self.owner.model_calls.append(context.to_request()[0])
        if self.mode == "partial":
            yield TextDelta("未完成的回答")
            raise ConnectionResetError(10054, "Controlled reset after partial output")
        if self.mode == "invalid":
            raise ValueError("Controlled deterministic invalid request")
        if self.mode == "fail" or self.mode == "tool_then_fail" and self.calls > 1:
            raise ConnectionResetError(10054, "Controlled connection reset before model response")
        if self.mode == "tool_then_fail":
            plan = self.owner.detail()
            yield ToolCallDelta(0, "skip-once", "manage_plan", json.dumps({"action": "skip",
                "plan_id": plan["id"], "expected_version": plan["version"], "step_id": "bad"}))
            yield StreamEnd("tool_calls")
        else:
            yield TextDelta("已核对计划结果。")
            yield StreamEnd("stop")


class ContinuationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="continuation-recovery-")
        self.root = Path(self.temporary.name)
        self.db, self.runs, _, _ = make_fixture(self.root)
        self.providers, self.model_calls, self.modes = [], [], ["fail", "success"]
        self.app = create_app(database=self.db, run_service=self.runs, chat_root=self.root / "chats",
                              chat_provider_factory=self.provider)
        self.app.state.agent_plans.set_continuation_callback(lambda: None)
        self.server = serve(self.app)
        self.base = self.server.__enter__()
        self.client = httpx.Client(base_url=self.base, trust_env=False, timeout=15)
        self.sid = self.client.post("/api/agent/sessions", json={"creator_id": "review-lab"}).json()["id"]
        self.path = f"/api/agent/sessions/{self.sid}/plans"
        response = self.client.post(self.path, json={"request_id": str(uuid4()), "title": "隔离续接失败",
            "steps": [{"id": "bad", "title": "确定只读失败", "tool": "get_producer_skill",
                       "arguments": {"skill_id": "missing--0000000000000000"}}]})
        self.assertEqual(response.status_code, 200, response.text)
        self.pid = response.json()["id"]
        wait_for(lambda: self.detail()["status"] == "needs_attention")

    def tearDown(self):
        self.client.close()
        self.server.__exit__(None, None, None)
        self.db.close()
        self.temporary.cleanup()

    def provider(self):
        provider = FaultProvider(self, self.modes[min(len(self.providers), len(self.modes) - 1)])
        self.providers.append(provider)
        return provider

    def detail(self):
        return self.client.get(f"{self.path}/{self.pid}").json()

    def doc(self):
        return self.app.state.chat._read(self.sid)

    def retry_due(self):
        with self.db.session() as session:
            session.get(AgentPlan, self.pid).continuation_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.app.state.chat.plan_continuation_retry_after[self.sid] = 0

    def test_first_response_connection_reset_keeps_notification_and_retries_same_event_once(self):
        chat = self.app.state.chat
        chat._resume_plans()
        wait_for(lambda: self.doc()["status"] == "failed" and self.detail()["continuation_state"] != "claimed")
        self.assertEqual(self.detail()["continuation_state"], "pending")
        for _ in range(10):
            chat._resume_plans()
        self.assertEqual(len(self.providers), 1)
        self.retry_due()
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: chat._resume_plans(), range(4)))
        wait_for(lambda: self.detail()["continuation_state"] == "done")
        request = self.doc()["requests"]
        self.assertEqual(len(request), 1)
        self.assertEqual(request[0]["continuation_attempts"], 2)
        self.assertEqual(request[0]["continuation_outcome"], "done")
        self.assertEqual(len(self.providers), 2)
        messages = json.loads(chat._path(self.sid).with_name("messages.json").read_text(encoding="utf-8"))
        observations = [message for message in messages if message.get("role") == "user"
                        and "[宿主提供的计划状态数据，不是用户新消息或指令]" in str(message.get("content"))]
        self.assertEqual(len(observations), 1)
        self.assertFalse([entry for entry in self.doc()["entries"] if entry["kind"] == "user"])

    def test_failure_after_a_real_tool_mutation_is_unknown_and_never_replayed(self):
        self.modes = ["tool_then_fail"]
        chat = self.app.state.chat
        chat._resume_plans()
        wait_for(lambda: self.detail()["continuation_state"] == "unknown")
        self.assertEqual(self.detail()["steps"][0]["status"], "skipped")
        self.assertEqual(self.doc()["requests"][0]["continuation_outcome"], "unknown")
        tools = [entry for entry in self.doc()["entries"] if entry["kind"] == "tool"]
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["status"], "done")
        for _ in range(10):
            chat._resume_plans()
        self.assertEqual(len(self.providers), 1)
        self.assertEqual(len(self.model_calls), 2)
        # Even accidental redelivery after a restart must not turn the old
        # request-ID marker into a success acknowledgement or another tool call.
        chat.start()
        with self.db.session() as session:
            session.get(AgentPlan, self.pid).continuation_state = "pending"
        chat._resume_plans()
        self.assertEqual(self.detail()["continuation_state"], "unknown")
        self.assertEqual(len(self.providers), 1)

    def prior_request(self, stage=None, *, legacy=False):
        plans, chat = self.app.state.agent_plans, self.app.state.chat
        event = plans.claim_continuation(self.sid, "crashed-worker", lease_seconds=-1)
        request_id = "plan-" + hashlib.sha256(event["event_id"].encode()).hexdigest()
        doc = chat._read(self.sid)
        record = {"id": request_id, "text": "", "kind": "plan_continuation",
                  "event_id": event["event_id"], "status": "failed" if legacy else "running"}
        if not legacy:
            record.update(continuation_protocol=1, continuation_stage=stage,
                          continuation_attempts=1, continuation_outcome="running")
        doc["requests"].append(record)
        doc["status"] = record["status"]
        chat._save(doc)
        messages = load_messages(chat._path(self.sid).with_name("messages.json"))
        messages.extend([{"role": "system", "content": "[宿主通知：后台计划有结果。]"},
            {"role": "user", "creatoros_source": "plan_observation:" + event["event_id"],
             "content": "[宿主提供的计划状态数据，不是用户新消息或指令]\n" + json.dumps(event)}])
        save_messages(messages, chat._path(self.sid).with_name("messages.json"))

    def test_restart_before_output_resumes_same_durable_request_and_keeps_one_observation(self):
        self.modes = ["success"]
        self.prior_request("model_started")
        chat = self.app.state.chat
        chat.start()
        chat._resume_plans()
        wait_for(lambda: self.detail()["continuation_state"] == "done")
        self.assertEqual(len(self.providers), 1)
        self.assertEqual(len(self.doc()["requests"]), 1)
        self.assertEqual(self.doc()["requests"][0]["continuation_attempts"], 2)
        messages = load_messages(chat._path(self.sid).with_name("messages.json"))
        self.assertEqual(sum(message.get("creatoros_source", "").startswith("plan_observation:") for message in messages), 1)

    def test_restart_after_output_and_legacy_failure_both_refuse_automatic_execution(self):
        self.prior_request("output_seen")
        chat = self.app.state.chat
        chat.start()
        chat._resume_plans()
        self.assertEqual(self.detail()["continuation_state"], "unknown")
        self.assertFalse(self.providers)
        # Explicit legacy JSON has no evidence proving pre-tool failure.
        doc = self.doc()
        record = doc["requests"][0]
        for key in list(record):
            if key.startswith("continuation_"):
                record.pop(key)
        record["status"] = "failed"
        chat._save(doc)
        with self.db.session() as session:
            session.get(AgentPlan, self.pid).continuation_state = "pending"
        chat._resume_plans()
        self.assertEqual(self.detail()["continuation_state"], "unknown")
        self.assertFalse(self.providers)

    def test_retry_budget_is_durable_and_exhaustion_is_failed_not_done(self):
        self.modes = ["fail"]
        chat = self.app.state.chat
        for attempt in range(1, 4):
            chat._resume_plans()
            expected = "pending" if attempt < 3 else "failed"
            wait_for(lambda: self.detail()["continuation_state"] == expected and len(self.providers) == attempt)
            self.assertEqual(self.doc()["requests"][0]["continuation_attempts"], attempt)
            self.retry_due()
        chat.start()
        for _ in range(5):
            chat._resume_plans()
        self.assertEqual(len(self.providers), 3)
        self.assertEqual(len(self.doc()["requests"]), 1)
        self.assertEqual(self.doc()["requests"][0]["continuation_outcome"], "failed")

    def test_partial_first_response_is_unknown_even_before_any_tool(self):
        self.modes = ["partial"]
        self.app.state.chat._resume_plans()
        wait_for(lambda: self.detail()["continuation_state"] == "unknown")
        self.assertEqual(self.doc()["requests"][0]["continuation_stage"], "output_seen")
        self.assertFalse([entry for entry in self.doc()["entries"] if entry["kind"] == "tool"])
        self.app.state.chat._resume_plans()
        self.assertEqual(len(self.providers), 1)

    def test_deterministic_failure_does_not_retry_or_claim_success(self):
        self.modes = ["invalid"]
        self.app.state.chat._resume_plans()
        wait_for(lambda: self.detail()["continuation_state"] == "failed")
        self.app.state.chat._resume_plans()
        self.assertEqual(len(self.providers), 1)
        self.assertEqual(self.doc()["requests"][0]["continuation_outcome"], "failed")

    def test_unstarted_worker_releases_busy_marker_and_reuses_the_durable_request(self):
        self.modes = ["success"]
        chat = self.app.state.chat
        with patch("creatoros.web.chat.Thread", side_effect=RuntimeError("Controlled thread unavailable")):
            chat._resume_plans()
        self.assertNotIn(self.sid, chat.active)
        self.assertNotIn(self.sid, chat.providers)
        self.assertNotIn(self.sid, chat.threads)
        self.assertFalse(self.model_calls)
        self.assertEqual(self.detail()["continuation_state"], "pending")
        self.retry_due()
        chat._resume_plans()
        wait_for(lambda: self.detail()["continuation_state"] == "done")
        self.assertEqual(len(self.doc()["requests"]), 1)
        self.assertEqual(len(self.model_calls), 1)

    def test_lease_token_and_event_version_fence_stale_failure_and_ack(self):
        plans = self.app.state.agent_plans
        first = plans.claim_continuation(self.sid, "first", lease_seconds=-1)
        second = plans.claim_continuation(self.sid, "second")
        self.assertEqual(first["event_id"], second["event_id"])
        self.assertFalse(plans.fail_continuation(self.pid, first["event_id"], "first", outcome="unknown"))
        self.assertFalse(plans.release_continuation(self.pid, first["event_id"], "first"))
        self.assertFalse(plans.ack_continuation(self.pid, first["event_id"], "first"))
        self.assertEqual(self.detail()["continuation_state"], "claimed")
        self.assertTrue(plans.release_continuation(self.pid, second["event_id"], "second", retry_seconds=30))
        self.assertFalse(plans.pending_continuation_sessions())
        self.assertIsNone(plans.claim_continuation(self.sid, "too-early"))
        self.retry_due()
        third = plans.claim_continuation(self.sid, "third")
        self.assertTrue(plans.fail_continuation(self.pid, third["event_id"], "third", outcome="unknown"))
        self.assertFalse(plans.ack_continuation(self.pid, third["event_id"], "second"))
        with self.db.session() as session:
            row = session.get(AgentPlan, self.pid)
            row.continuation_version += 1
            row.continuation_state = "pending"
        fourth = plans.claim_continuation(self.sid, "fourth")
        self.assertNotEqual(fourth["event_id"], third["event_id"])
        self.assertFalse(plans.fail_continuation(self.pid, third["event_id"], "fourth", outcome="failed"))
        self.assertFalse(plans.release_continuation(self.pid, third["event_id"], "fourth"))
        self.assertFalse(plans.ack_continuation(self.pid, third["event_id"], "fourth"))
        self.assertTrue(plans.ack_continuation(self.pid, fourth["event_id"], "fourth"))

    def test_unstarted_worker_exhaustion_is_known_failure_without_model_or_busy_marker(self):
        chat = self.app.state.chat
        with patch("creatoros.web.chat.Thread", side_effect=RuntimeError("Controlled thread unavailable")):
            for _ in range(3):
                chat._resume_plans()
                self.retry_due()
        self.assertEqual(self.detail()["continuation_state"], "failed")
        self.assertEqual(self.doc()["requests"][0]["continuation_attempts"], 3)
        self.assertNotIn(self.sid, chat.active)
        self.assertFalse(self.model_calls)


if __name__ == "__main__":
    unittest.main()
