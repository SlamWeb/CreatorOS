"""Isolated HTTP/SQLite and connection fault injection; never retry a real plan."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from creatoros.ai.types import TextDelta, ToolCallDelta, StreamEnd
from creatoros.storage.models import AgentPlan, AgentPlanStep
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
        self.blocked_releases = []
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
        for release in self.blocked_releases:
            release.set()
        for thread in list(self.app.state.chat.threads.values()):
            thread.join(timeout=10)
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
        chat = self.app.state.chat
        chat.plan_continuation_retry_after[self.sid] = 0
        doc = chat._read(self.sid)
        for record in doc["requests"]:
            if "continuation_retry_at" in record:
                record["continuation_retry_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        chat._save(doc)

    def blocked_tool_provider(self):
        entered, release = Event(), Event()
        owner = self

        class BlockingProvider(FaultProvider):
            def stream(self, context):
                entered.set()
                if not release.wait(90):
                    raise TimeoutError("controlled test did not release provider")
                yield from super().stream(context)

        def factory():
            provider = BlockingProvider(owner, "tool_then_fail")
            owner.providers.append(provider)
            return provider

        self.app.state.chat.provider_factory = factory
        self.blocked_releases.append(release)
        return entered, release

    def second_process(self, code, *, timeout=30):
        return subprocess.run([sys.executable, "-c", code, str(self.root / "chats"),
                               str(self.db.engine.url), self.sid],
                              cwd=Path(__file__).resolve().parents[1], capture_output=True,
                              text=True, encoding="utf-8", timeout=timeout)

    def test_live_host_expired_lease_cannot_be_reclaimed_by_another_process(self):
        entered, release = self.blocked_tool_provider()
        chat = self.app.state.chat
        chat._resume_plans()
        self.assertTrue(entered.wait(10))
        with self.db.session() as session:
            session.get(AgentPlan, self.pid).continuation_until = datetime.now(timezone.utc) - timedelta(seconds=1)
            session.get(AgentPlanStep, self.pid + ":bad").status = "running"
        child = self.second_process('''
import sys
from pathlib import Path
from creatoros.storage import Database
from creatoros.web.chat import AgentChatService
from creatoros.agent.plans import PlanService
calls = []
def provider():
    calls.append(1)
    raise RuntimeError("second host must not start")
db = Database(sys.argv[2])
chat = AgentChatService(Path(sys.argv[1]), provider_factory=provider)
plans = PlanService(db, chat)
chat.bind_plan_service(plans)
chat.start()
plans.start_gate = lambda: False
plans.start()
plans.stop()
chat._resume_plans()
print("SECOND_HOST_CALLS", len(calls))
db.close()
''')
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertIn("SECOND_HOST_CALLS 0", child.stdout)
        saved = json.loads(chat._path(self.sid).read_text(encoding="utf-8"))
        self.assertEqual(saved["status"], "running")  # second host startup did not repair an active turn
        self.assertEqual(self.detail()["continuation_state"], "claimed")
        with self.db.session() as session:
            step = session.get(AgentPlanStep, self.pid + ":bad")
            self.assertEqual(step.status, "running")  # second host did not repair a live plan
            step.status = "failed"
        release.set()
        wait_for(lambda: self.detail()["continuation_state"] == "unknown")
        self.assertEqual(self.detail()["steps"][0]["status"], "skipped")
        self.assertEqual(len(self.providers), 1)
        self.assertEqual(len([e for e in self.doc()["entries"] if e["kind"] == "tool"]), 1)

    def test_token_stolen_before_first_output_fences_the_old_workers_tool(self):
        entered, release = self.blocked_tool_provider()
        chat = self.app.state.chat
        chat._resume_plans()
        self.assertTrue(entered.wait(10))
        with self.db.session() as session:
            session.get(AgentPlan, self.pid).continuation_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        child = self.second_process('''
import sys
from pathlib import Path
from creatoros.storage import Database
from creatoros.web.chat import AgentChatService
from creatoros.agent.plans import PlanService
db = Database(sys.argv[2])
chat = AgentChatService(Path(sys.argv[1]))
plans = PlanService(db, chat)
event = plans.claim_continuation(sys.argv[3], "stolen-token")
print("CLAIMED", bool(event))
db.close()
''')
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertIn("CLAIMED True", child.stdout)
        release.set()
        wait_for(lambda: self.sid not in chat.active)
        self.assertEqual(self.detail()["steps"][0]["status"], "failed")
        self.assertFalse([e for e in self.doc()["entries"] if e["kind"] == "tool"])
        with self.db.session() as session:
            row = session.get(AgentPlan, self.pid)
            self.assertEqual(row.continuation_token, "stolen-token")

    def test_crash_between_database_fence_and_view_save_recovers_unknown(self):
        child = self.second_process('''
import os, sys, time
from pathlib import Path
from types import SimpleNamespace
from creatoros.ai.types import TextDelta
from creatoros.storage import Database
from creatoros.web.chat import AgentChatService
from creatoros.agent.plans import PlanService
class Provider:
    client = SimpleNamespace(close=lambda: None)
    def stream(self, context):
        yield TextDelta("output must not escape before the crash")
db = Database(sys.argv[2])
chat = AgentChatService(Path(sys.argv[1]), provider_factory=Provider)
plans = PlanService(db, chat)
chat.bind_plan_service(plans)
original = chat._save
def crash_before_view_save(doc):
    if any(r.get("continuation_stage") == "output_seen" for r in doc["requests"]):
        os._exit(73)
    original(doc)
chat._save = crash_before_view_save
chat._resume_plans()
time.sleep(15)
os._exit(74)
''')
        self.assertEqual(child.returncode, 73, child.stderr)
        self.assertEqual(self.detail()["continuation_state"], "executing")
        before = self.doc()["requests"][0]
        self.assertEqual(before["continuation_stage"], "model_started")
        self.app.state.chat._resume_plans()
        self.assertEqual(self.detail()["continuation_state"], "unknown")
        self.assertEqual(self.doc()["requests"][0]["continuation_outcome"], "unknown")
        self.assertFalse(self.providers)
        self.app.state.chat._resume_plans()
        self.assertFalse(self.providers)

    def test_process_crash_after_tool_mutation_preserves_receipt_without_replay(self):
        child = self.second_process('''
import json, os, sys, time
from pathlib import Path
from types import SimpleNamespace
from sqlalchemy import select
from creatoros.ai.types import ToolCallDelta, StreamEnd
from creatoros.storage import Database
from creatoros.storage.models import AgentPlan
from creatoros.web.chat import AgentChatService
from creatoros.agent.plans import PlanService
db = Database(sys.argv[2])
with db.session() as session:
    plan = session.scalar(select(AgentPlan).where(AgentPlan.session_id == sys.argv[3]))
    plan_id, version = plan.id, plan.version
class Provider:
    client = SimpleNamespace(close=lambda: None)
    calls = 0
    def stream(self, context):
        self.calls += 1
        if self.calls > 1:
            os._exit(73)
        args = {"action": "skip", "plan_id": plan_id, "expected_version": version, "step_id": "bad"}
        yield ToolCallDelta(0, "once", "manage_plan", json.dumps(args))
        yield StreamEnd("tool_calls")
chat = AgentChatService(Path(sys.argv[1]), provider_factory=Provider)
plans = PlanService(db, chat)
chat.bind_plan_service(plans)
chat._resume_plans()
time.sleep(15)
os._exit(74)
''')
        self.assertEqual(child.returncode, 73, child.stderr)
        self.assertEqual(self.detail()["steps"][0]["status"], "skipped")
        self.assertEqual(self.detail()["continuation_state"], "executing")
        self.assertEqual(self.doc()["requests"][0]["continuation_stage"], "output_seen")
        self.app.state.chat._resume_plans()
        self.assertEqual(self.detail()["continuation_state"], "unknown")
        self.assertEqual(self.detail()["steps"][0]["status"], "skipped")
        self.assertEqual(len([e for e in self.doc()["entries"] if e["kind"] == "tool"]), 1)
        self.app.state.chat._resume_plans()
        self.assertFalse(self.providers)

    def test_new_plan_event_waits_for_current_executing_continuation(self):
        plans = self.app.state.agent_plans
        event = plans.claim_continuation(self.sid, "current")
        self.assertTrue(plans.begin_continuation_output(self.pid, event["event_id"], "current"))
        detail = self.detail()
        steps = [{key: s[key] for key in ("id", "title", "tool", "arguments", "depends_on", "wait_for_completion")}
                 for s in detail["steps"]]
        plans.edit(self.sid, self.pid, detail["version"], steps)
        plans.advance()
        with self.db.session() as session:
            row = session.get(AgentPlan, self.pid)
            self.assertEqual(row.continuation_state, "executing")
            self.assertEqual(f'{row.id}:{row.continuation_version}', event["event_id"])
            self.assertEqual(row.continuation_token, "current")
        self.assertTrue(plans.ack_continuation(self.pid, event["event_id"], "current"))
        plans.advance()
        self.assertEqual(self.detail()["continuation_state"], "pending")
        next_event = plans.claim_continuation(self.sid, "next")
        self.assertNotEqual(event["event_id"], next_event["event_id"])

    def test_unknown_continuation_freezes_plan_until_explicit_resume(self):
        plans = self.app.state.agent_plans
        event = plans.claim_continuation(self.sid, "current")
        self.assertTrue(plans.begin_continuation_output(self.pid, event["event_id"], "current"))
        detail = self.detail()
        steps = [{key: s[key] for key in ("id", "title", "tool", "arguments", "depends_on", "wait_for_completion")}
                 for s in detail["steps"]]
        plans.edit(self.sid, self.pid, detail["version"], steps)
        self.assertTrue(plans.fail_continuation(self.pid, event["event_id"], "current", outcome="unknown"))
        plans.advance()
        detail = self.detail()
        self.assertEqual(detail["status"], "running")
        self.assertEqual(detail["continuation_state"], "unknown")
        paused = plans.action(self.sid, self.pid, detail["version"], "pause")
        plans.action(self.sid, self.pid, paused["version"], "resume")
        plans.advance()
        self.assertEqual(self.detail()["status"], "needs_attention")
        self.assertEqual(self.detail()["continuation_state"], "pending")

    def test_restart_does_not_requeue_running_production_with_unknown_continuation(self):
        from creatoros.agent.plans import PlanService
        plans = self.app.state.agent_plans
        event = plans.claim_continuation(self.sid, "crashed")
        self.assertTrue(plans.begin_continuation_output(self.pid, event["event_id"], "crashed"))
        with self.db.session() as session:
            plan = session.get(AgentPlan, self.pid)
            step = session.get(AgentPlanStep, self.pid + ":bad")
            plan.status = "running"
            step.tool, step.status = "start_content_run", "running"
            step.arguments_json = {"action": "tasks", "tasks": []}
            step.result_json = {"existing_receipt": "preserve-me"}
        restarted = PlanService(self.db, self.app.state.chat)
        restarted.start_gate = lambda: False
        restarted.start()
        try:
            with self.db.session() as session:
                step = session.get(AgentPlanStep, self.pid + ":bad")
                self.assertEqual(step.status, "unknown")
                self.assertEqual(step.result_json, {"existing_receipt": "preserve-me"})
            self.app.state.chat._resume_plans()
            plans.advance()
            self.assertEqual(self.detail()["continuation_state"], "unknown")
            self.assertEqual(self.detail()["steps"][0]["status"], "unknown")
            self.assertFalse(self.providers)
        finally:
            restarted.stop()

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
        self.assertEqual(self.detail()["continuation_state"], "pending")
        self.assertFalse(self.providers)
        self.retry_due()
        chat._resume_plans()
        wait_for(lambda: self.detail()["continuation_state"] == "done")
        self.assertEqual(len(self.providers), 1)
        self.assertEqual(len(self.doc()["requests"]), 1)
        self.assertEqual(self.doc()["requests"][0]["continuation_attempts"], 2)
        messages = load_messages(chat._path(self.sid).with_name("messages.json"))
        self.assertEqual(sum(message.get("creatoros_source", "").startswith("plan_observation:") for message in messages), 1)

    def test_retry_deadline_survives_crash_before_sqlite_release(self):
        self.prior_request("model_started")
        chat = self.app.state.chat
        doc = chat._read(self.sid)
        doc["requests"][0].update(continuation_outcome="retry_pending",
            continuation_retry_at=(datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat())
        chat._save(doc)
        self.modes = ["success"]
        chat._resume_plans()
        self.assertEqual(self.detail()["continuation_state"], "pending")
        self.assertFalse(self.providers)
        with self.db.session() as session:
            deadline = session.get(AgentPlan, self.pid).continuation_until
            self.assertGreater(deadline.replace(tzinfo=timezone.utc), datetime.now(timezone.utc))
        self.retry_due()
        chat._resume_plans()
        wait_for(lambda: self.detail()["continuation_state"] == "done")
        self.assertEqual(len(self.providers), 1)

    def test_restart_after_output_refuses_automatic_execution(self):
        self.prior_request("output_seen")
        chat = self.app.state.chat
        chat.start()
        chat._resume_plans()
        self.assertEqual(self.detail()["continuation_state"], "unknown")
        self.assertFalse(self.providers)

    def test_legacy_idle_or_failed_request_without_completion_evidence_is_unknown(self):
        self.prior_request(legacy=True)
        chat = self.app.state.chat
        doc = self.doc()
        doc["status"] = doc["requests"][0]["status"] = "idle"
        chat._save(doc)
        chat._resume_plans()
        self.assertEqual(self.detail()["continuation_state"], "unknown")
        self.assertEqual(self.doc()["requests"][0]["continuation_outcome"], "unknown")
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

    def test_request_id_collision_does_not_rewrite_an_unrelated_request(self):
        self.prior_request(legacy=True)
        chat = self.app.state.chat
        doc = self.doc()
        doc["requests"][0]["kind"] = "ordinary_user_request"
        before = dict(doc["requests"][0])
        chat._save(doc)
        chat._resume_plans()
        self.assertEqual(self.detail()["continuation_state"], "unknown")
        self.assertEqual(self.doc()["requests"][0], before)
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
