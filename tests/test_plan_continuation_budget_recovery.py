"""Crash recovery distinguishes exhausted zero-output attempts from old records."""
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from creatoros.agent.plans import PlanService
from creatoros.storage import Base, ContentRepository, CreatorPlatform, Database
from creatoros.storage.models import AgentPlan
from creatoros.tools.results import ToolResult
from creatoros.web.chat import AgentChatService


class ContinuationBudgetRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="continuation-budget-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.db = Database("sqlite:///" + (self.root / "isolated.db").as_posix())
        self.addCleanup(self.db.close)
        Base.metadata.create_all(self.db.engine)
        ContentRepository(self.db).create_creator(creator_id="isolated", display_name="isolated",
                                                  platform=CreatorPlatform.XIAOHONGSHU)
        self.provider_calls = []
        self.chat, self.plans = self.new_host()
        self.session_id = self.chat.create("isolated")["id"]
        self.steps = [
            {"id": "review", "title": "review", "tool": "request_review", "arguments": {"question": "check"}},
            {"id": "image", "title": "image", "tool": "start_content_run", "depends_on": ["review"],
             "arguments": {"action": "tasks", "topic_id": "isolated-topic", "tasks": [
                 {"kind": "image", "title": "image", "brief": "draw"}]}},
        ]
        plan = self.plans.create(self.session_id, "request", "plan", self.steps, "http://127.0.0.1:1")
        self.plan_id = plan["id"]
        self.plans.advance()
        event = self.plans.claim_continuation(self.session_id, "setup")
        self.event_id = event["event_id"]
        self.assertTrue(self.plans.release_continuation(self.plan_id, self.event_id, "setup"))
        self.request_id = "plan-" + sha256(self.event_id.encode()).hexdigest()

    def new_host(self):
        def provider():
            self.provider_calls.append(1)
            raise AssertionError("recovery must not make another model request")

        chat = AgentChatService(self.root / "chats", provider_factory=provider,
                                creator_lookup=lambda _: SimpleNamespace(is_active=True))
        plans = PlanService(self.db, chat)
        chat.bind_plan_service(plans)
        return chat, plans

    def save_record(self, **fields):
        doc = self.chat._read(self.session_id)
        doc["requests"].append({"id": self.request_id, "text": "", "kind": "plan_continuation",
                                "event_id": self.event_id, **fields})
        doc["status"] = "running"
        self.chat._save(doc)

    def test_third_zero_output_attempt_crash_recovers_as_failed_without_model_retry(self):
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        self.save_record(status="failed", continuation_outcome="retry_pending", continuation_protocol=1,
                         continuation_stage="model_started", continuation_attempts=2,
                         continuation_model_calls=1, continuation_retry_at=past)
        code = r'''
import os, sys
from pathlib import Path
from types import SimpleNamespace
from creatoros.storage import Database
from creatoros.web.chat import AgentChatService
from creatoros.agent.plans import PlanService
class CrashBeforeOutput:
    client = SimpleNamespace(close=lambda: None)
    def stream(self, context):
        # A concurrent explicit resume may make the plan running while this
        # already-claimed continuation is still waiting for its first output.
        current = plans.get(sys.argv[3], sys.argv[4])
        plans.action(sys.argv[3], sys.argv[4], current["version"], "resume")
        os._exit(73)
        yield  # make this a generator; execution dies before returning any event
db = Database(sys.argv[2])
chat = AgentChatService(Path(sys.argv[1]) / "chats", provider_factory=CrashBeforeOutput,
                        creator_lookup=lambda _: SimpleNamespace(is_active=True))
plans = PlanService(db, chat)
chat.bind_plan_service(plans)
chat._resume_plans()
thread = chat.threads.get(sys.argv[3])
if thread:
    thread.join(20)
os._exit(74)
'''
        child = subprocess.run([sys.executable, "-c", code, str(self.root), str(self.db.engine.url),
                                self.session_id, self.plan_id],
                               cwd=Path(__file__).resolve().parents[1], capture_output=True,
                               text=True, encoding="utf-8", timeout=30)
        self.assertEqual(child.returncode, 73, child.stderr)
        before = self.chat._read(self.session_id)["requests"][0]
        self.assertEqual((before["continuation_attempts"], before["continuation_stage"],
                          before["continuation_outcome"]), (3, "model_started", "running"))
        with self.db.session() as session:
            plan = session.get(AgentPlan, self.plan_id)
            self.assertEqual(plan.continuation_state, "claimed")
            self.assertEqual(plan.status, "running")
            # Simulate the dead worker's lease expiring; do not wait two minutes.
            plan.continuation_until = datetime.now(timezone.utc) - timedelta(seconds=1)

        restarted, plans = self.new_host()
        restarted.start()
        restarted._resume_plans()
        self.assertEqual(plans.get(self.session_id, self.plan_id)["continuation_state"], "failed")
        doc = restarted._read(self.session_id)
        record = doc["requests"][0]
        self.assertEqual((record["continuation_outcome"], record["status"]), ("failed", "failed"))
        self.assertEqual(doc["status"], "failed")
        self.assertIn("远端", doc["error"])
        self.assertIn("用量", doc["error"])
        for _ in range(3):
            restarted._resume_plans()
        self.assertEqual(self.provider_calls, [])

        class InlinePool:
            def submit(self, fn, *args):
                future = Future()
                future.set_result(fn(*args))
                return future

        plans._pool = InlinePool()
        with patch("creatoros.agent.plans.execute_tool_call", return_value=ToolResult(content='{"run_id":"isolated"}')) as execute:
            detail = plans.get(self.session_id, self.plan_id)
            edited = plans.edit(self.session_id, self.plan_id, detail["version"], self.steps)
            self.assertEqual(edited["continuation_state"], "failed")
            for _ in range(3):
                plans.advance()
            self.assertEqual(execute.call_count, 0)
            self.assertEqual(plans.get(self.session_id, self.plan_id)["steps"][1]["status"], "pending")

            paused = plans.action(self.session_id, self.plan_id, edited["version"], "pause")
            resumed = plans.action(self.session_id, self.plan_id, paused["version"], "resume")
            self.assertEqual(resumed["continuation_state"], "none")
            for _ in range(3):
                plans.advance()
            self.assertEqual(execute.call_count, 1)
            self.assertEqual(plans.get(self.session_id, self.plan_id)["steps"][1]["status"], "succeeded")
            record_after = restarted._read(self.session_id)["requests"][0]
            self.assertEqual((record_after["continuation_outcome"], record_after["status"],
                              record_after["continuation_attempts"]), ("failed", "failed", 3))

    def test_legacy_record_without_stage_evidence_remains_unknown(self):
        self.save_record(status="running", continuation_outcome="running", continuation_attempts=3)
        restarted, plans = self.new_host()
        restarted.start()
        restarted._resume_plans()
        self.assertEqual(plans.get(self.session_id, self.plan_id)["continuation_state"], "unknown")
        self.assertEqual(restarted._read(self.session_id)["requests"][0]["continuation_outcome"], "unknown")
        self.assertEqual(self.provider_calls, [])


if __name__ == "__main__":
    unittest.main()
