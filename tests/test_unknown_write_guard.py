import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from creatoros.agent.unknown_write_guard import UnknownWriteGuard, _is_confirmed_success, _RUN_STATES
from creatoros.ai.types import ToolCall
from creatoros.context import RuntimeContext
from creatoros.tools.results import ToolResult


def call(name, **kwargs):
    return ToolCall("call", name, json.dumps(kwargs, ensure_ascii=False))


def topic(topic_id="a"):
    return call("manage_topics", action="update", topic_id=topic_id,
                changes={"title": "新标题"}, expected_digest="a" * 64)


def task(topic_id="a"):
    return call("start_content_run", action="tasks", topic_id=topic_id,
                tasks=[{"kind": "image", "title": "图", "brief": "画面"}])


class UnknownWriteGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.context = RuntimeContext(self.root, creator_id="account-a", agent_session_id="session-a",
                                      session_file=self.root / "messages.json", user_request_id="turn-1")
        self.guard = UnknownWriteGuard(self.context)

    def unknown(self, tool):
        token, blocked = self.guard.prepare(tool)
        self.assertIsNone(blocked)
        self.guard.mark_dispatched(token)
        self.guard.finish(token, ToolResult('{"error":"studio_outcome_unknown"}', True, "studio_outcome_unknown"))
        return token

    def state(self):
        return json.loads(self.guard.path.read_text(encoding="utf-8"))

    def test_topic_unknown_blocks_same_target_across_turn_and_restart_not_reads_or_other_target(self):
        self.unknown(topic())
        next_guard = UnknownWriteGuard(replace(self.context, user_request_id="turn-2"))
        token, blocked = next_guard.prepare(topic())
        self.assertIsNone(token)
        self.assertEqual(blocked.error_type, "write_outcome_pending")
        self.assertEqual(next_guard.prepare(call("manage_topics", action="get", topic_id="a")), (None, None))
        other, blocked = next_guard.prepare(topic("b"))
        self.assertTrue(other)
        self.assertIsNone(blocked)
        self.assertTrue(self.state()["records"][0]["request_id"])

    def test_prepared_and_dispatched_crash_remain_blocked(self):
        for should_dispatch in (False, True):
            with self.subTest(should_dispatch=should_dispatch), TemporaryDirectory() as directory:
                context = replace(self.context, session_file=Path(directory) / "messages.json")
                guard = UnknownWriteGuard(context)
                token, _ = guard.prepare(task())
                if should_dispatch:
                    guard.mark_dispatched(token)
                restarted = UnknownWriteGuard(replace(context, user_request_id="next"))
                self.assertEqual(restarted.prepare(task())[1].error_type, "write_outcome_pending")

    def test_unrelated_tool_with_invalid_json_keeps_existing_executor_path(self):
        self.assertEqual(self.guard.prepare(ToolCall("call", "get_creator_tasks", "{invalid")), (None, None))

    def test_known_pre_dispatch_rejection_releases_and_bad_success_does_not(self):
        token, _ = self.guard.prepare(topic())
        self.guard.mark_dispatched(token)
        self.guard.finish(token, ToolResult("invalid", True, "invalid_arguments", details={"pre_dispatch": True}))
        self.assertEqual(self.state()["records"], [])
        token, _ = self.guard.prepare(topic())
        self.guard.mark_dispatched(token)
        replacement = self.guard.finish(token, ToolResult('{"status":"ok"}'))
        self.assertTrue(replacement.is_error)
        self.assertEqual(replacement.error_type, "studio_outcome_unknown")
        payload = json.loads(replacement.content)
        self.assertEqual(payload["upstream_raw_content"], '{"status":"ok"}')
        self.assertEqual(payload["topic_id"], "a")
        self.assertTrue(payload["request_id"])
        self.assertEqual(self.state()["records"][0]["phase"], "unknown")

    def test_task_receipt_with_unknown_item_never_confirms_write(self):
        for fields in ({"status": "unknown"}, {"status": "queued", "dispatch_state": "needs_attention"},
                       {"status": "queued", "error_code": "studio_outcome_unknown"}, {}):
            with self.subTest(fields=fields), TemporaryDirectory() as directory:
                guard = UnknownWriteGuard(replace(self.context, session_file=Path(directory) / "messages.json"))
                token, _ = guard.prepare(task())
                guard.mark_dispatched(token)
                response = json.dumps({"topic_id": "a", "items": [{"id": "task-1", **fields}]})
                replacement = guard.finish(token, ToolResult(response))
                self.assertTrue(replacement.is_error)
                self.assertEqual(json.loads(guard.path.read_text(encoding="utf-8"))["records"][0]["phase"], "unknown")

    def test_read_or_connection_failure_after_possible_write_is_unknown(self):
        for code in ("studio_read_failed", "studio_unavailable", "invalid_arguments", "agent_scope_rejected"):
            with self.subTest(code=code), TemporaryDirectory() as directory:
                guard = UnknownWriteGuard(replace(self.context, session_file=Path(directory) / "messages.json"))
                token, _ = guard.prepare(topic())
                guard.mark_dispatched(token)
                marked = guard.finish(token, ToolResult("connection lost", True, code, retryable=True))
                self.assertEqual(marked.error_type, code)
                self.assertTrue(marked.details["outcome_unknown"])
                self.assertFalse(marked.retryable)
                self.assertEqual(json.loads(marked.content)["upstream_raw_content"], "connection lost")
                self.assertTrue(json.loads(marked.content)["outcome_unknown"])
                self.assertEqual(json.loads(guard.path.read_text(encoding="utf-8"))["records"][0]["phase"], "unknown")
                self.assertEqual(guard.prepare(topic())[1].error_type, "write_outcome_pending")

    def test_dispatched_json_error_keeps_original_fields_and_raw_receipt(self):
        token, _ = self.guard.prepare(task())
        self.guard.mark_dispatched(token)
        raw = '{"error":"studio_read_failed","message":"read failed","run_id":"run-x","request_id":"req-x"}'
        result = self.guard.finish(token, ToolResult(raw, True, "studio_read_failed", details={"trace": "ok"}))
        data = json.loads(result.content)
        self.assertEqual(result.error_type, "studio_read_failed")
        self.assertEqual((data["error"], data["run_id"], data["request_id"]),
                         ("studio_read_failed", "run-x", "req-x"))
        self.assertEqual(data["upstream_message"], "read failed")
        self.assertEqual(data["upstream_raw_content"], raw)
        self.assertTrue(data["outcome_unknown"])
        self.assertTrue(result.details["outcome_unknown"])
        self.assertEqual(result.details["trace"], "ok")

    def test_fresh_unknown_exposes_host_request_id_when_upstream_omits_it(self):
        from creatoros.integrations.production_identity import fresh_request_id
        request = call("start_content_run", action="fresh", source_run_id="old", expected_version=1)
        token, _ = self.guard.prepare(request)
        self.guard.mark_dispatched(token)
        raw = '{"error":"studio_unavailable","request_id":null}'
        marked = self.guard.finish(token, ToolResult(raw, True, "studio_unavailable"))
        data = json.loads(marked.content)
        self.assertEqual(data["request_id"], fresh_request_id("session-a", "turn-1", "old"))
        self.assertEqual(data["upstream_raw_content"], raw)

    def test_unknown_production_cannot_be_bypassed_by_changing_entry_point(self):
        fresh = call("start_content_run", action="fresh", source_run_id="old-run", expected_version=1)
        reference = call("start_content_run", action="reference", series_id="series", title="one",
                         attachment_ids=["image"])
        batch = call("start_content_run", action="start", topic_ids=["elsewhere"])
        pairs = ((fresh, task()), (fresh, batch), (reference, task()), (reference, batch),
                 (task(), fresh), (task(), reference), (batch, fresh), (batch, reference),
                 (fresh, reference), (reference, fresh))
        for previous, next_call in pairs:
            with self.subTest(previous=previous.arguments, next=next_call.arguments), TemporaryDirectory() as directory:
                context = replace(self.context, session_file=Path(directory) / "messages.json")
                guard = UnknownWriteGuard(context)
                token, _ = guard.prepare(previous)
                guard.mark_dispatched(token)
                guard.finish(token, ToolResult("lost", True, "studio_unavailable"))
                restarted = UnknownWriteGuard(replace(context, user_request_id="next"))
                self.assertEqual(restarted.prepare(next_call)[1].error_type, "write_outcome_pending")
                self.assertEqual(restarted.prepare(call("manage_topics", action="get", topic_id="a")), (None, None))
                self.assertIsNone(restarted.prepare(topic("another"))[1])

    def test_precise_task_receipt_releases_cross_entry_freeze(self):
        self.unknown(task())
        request_id = self.state()["records"][0]["request_id"]
        fresh = call("start_content_run", action="fresh", source_run_id="run", expected_version=1)
        self.assertEqual(self.guard.prepare(fresh)[1].error_type, "write_outcome_pending")
        read = call("manage_topics", action="get", topic_id="a", production_request_id=request_id)
        result = ToolResult(json.dumps({"production_receipt": {"topic_id": "a", "request_id": request_id,
                                                              "status": "accepted"}}))
        self.assertTrue(self.guard.observe_read(read, result))
        later = UnknownWriteGuard(replace(self.context, user_request_id="turn-2"))
        self.assertIsNone(later.prepare(fresh)[1])

    def test_only_matching_real_receipt_resolves_unknown(self):
        self.unknown(task())
        entry = self.state()["records"][0]
        request_id = entry["request_id"]
        def read(req, status="accepted", topic_id="a"):
            return (call("manage_topics", action="get", topic_id=topic_id, production_request_id=req),
                    ToolResult(json.dumps({"production_receipt": {"topic_id": topic_id,
                                            "request_id": req, "status": status}})))
        self.assertFalse(self.guard.observe_read(*read("wrong")))
        self.assertFalse(self.guard.observe_read(*read(request_id, "unknown")))
        self.assertFalse(self.guard.observe_read(*read(request_id, topic_id="b")))
        self.assertEqual(self.guard.prepare(task())[1].error_type, "write_outcome_pending")
        self.assertTrue(self.guard.observe_read(*read(request_id)))
        self.assertEqual(self.state()["records"][0]["phase"], "confirmed")
        self.assertEqual(self.guard.prepare(task())[1].error_type, "write_outcome_pending")
        later = UnknownWriteGuard(replace(self.context, user_request_id="turn-2"))
        self.assertIsNone(later.prepare(task())[1])

    def test_guard_rejects_corrupt_or_other_account_file_and_never_dispatches(self):
        self.unknown(topic())
        with self.assertRaises(RuntimeError):
            UnknownWriteGuard(replace(self.context, creator_id="other")).prepare(topic())
        self.guard.path.write_text('{"records": []}', encoding="utf-8")
        with self.assertRaises(RuntimeError):
            self.guard.prepare(topic())

    def test_batch_multiset_and_actual_dto_shape(self):
        request = call("start_content_run", action="start", topic_ids=["a", "b", "a"])
        token, _ = self.guard.prepare(request)
        record = self.state()["records"][0]
        self.assertEqual(record["expected_topics"], ["a", "b"])
        def row(topic_id, **overrides):
            return {"topic_id": topic_id, "run_id": "run-" + topic_id,
                    "status": "queued", "accepted": True, **overrides}
        good = {"items": [row("a"), row("b", accepted=False)], "accepted_count": 1}
        self.assertTrue(_is_confirmed_success(record, good))
        for rows, count in (([row("a"), row("a")], 2),
                            ([row("a"), row("b", status="banana")], 2),
                            ([row("a"), {"topic_id": "b", "status": "queued"}], 1),
                            ([row("a"), row("b", run_id=None)], 2),
                            ([row("a"), row("b", error_code="queue_needs_attention")], 2),
                            ([row("a"), row("b", status="rejected", accepted=False,
                                               error_code="execution_ownership")], 1)):
            with self.subTest(rows=rows):
                self.assertFalse(_is_confirmed_success(record, {"items": rows, "accepted_count": count}))
        for state in _RUN_STATES:
            candidate = {"items": [row("a", status=state), row("b")], "accepted_count": 2}
            self.assertTrue(_is_confirmed_success(record, candidate), state)
        rejected = {"items": [row("a"), row("b", status="rejected", accepted=False,
                         run_id=None, error_code="conflict")], "accepted_count": 1}
        self.assertTrue(_is_confirmed_success(record, rejected))
        self.guard.mark_dispatched(token)
        self.assertIsNone(self.guard.finish(token, ToolResult(json.dumps(good))))
        self.assertEqual(self.state()["records"][0]["phase"], "confirmed")

    def test_topic_write_requires_original_request_and_account(self):
        token, _ = self.guard.prepare(topic())
        record = self.state()["records"][0]
        good = {"ok": True, "topic": {"id": "a"}, "request_id": record["request_id"],
                "creator_id": "account-a"}
        self.assertTrue(_is_confirmed_success(record, good))
        for update in ({"request_id": "old"}, {"creator_id": "other"}, {"topic": {"id": "b"}},
                       {"request_id": None}, {"creator_id": None}):
            self.assertFalse(_is_confirmed_success(record, {**good, **update}), update)
        self.guard.mark_dispatched(token)
        self.assertIsNone(self.guard.finish(token, ToolResult(json.dumps(good))))
        self.assertEqual(self.state()["records"][0]["phase"], "confirmed")
        self.assertEqual(self.guard.prepare(topic())[1].error_type, "write_outcome_pending")
        later = UnknownWriteGuard(replace(self.context, user_request_id="turn-2"))
        self.assertIsNone(later.prepare(topic())[1])

    def test_fresh_and_reference_real_status_queue_and_scope(self):
        for request in (call("start_content_run", action="fresh", source_run_id="old", expected_version=1),
                        call("start_content_run", action="reference", series_id="series", title="one",
                             attachment_ids=["attachment"])):
            with self.subTest(action=json.loads(request.arguments)["action"]), TemporaryDirectory() as directory:
                guard = UnknownWriteGuard(replace(self.context, session_file=Path(directory) / "messages.json"))
                token, _ = guard.prepare(request)
                record = json.loads(guard.path.read_text(encoding="utf-8"))["records"][0]
                if record["kind"] == "production_fresh":
                    from creatoros.integrations.production_identity import fresh_request_id
                    self.assertEqual(record["request_id"], fresh_request_id("session-a", "turn-1", "old"))
                good = {"run_id": "new", "status": "queued", "accepted": True, "creator_id": "account-a",
                        "topic_id": "topic", "series_id": "series", "production_queue": {"status": "pending"}}
                for state in _RUN_STATES:
                    self.assertTrue(_is_confirmed_success(record, {**good, "status": state}), state)
                for queue in ({"status": "needs_attention"}, {"status": "unknown"}, {}, [], "pending"):
                    self.assertFalse(_is_confirmed_success(record, {**good, "production_queue": queue}), queue)
                for update in ({"status": "banana"}, {"accepted": "true"}, {"creator_id": "other"},
                               {"topic_id": None}):
                    self.assertFalse(_is_confirmed_success(record, {**good, **update}), update)
                self.assertTrue(_is_confirmed_success(record, {**good, "production_queue": None}))
                guard.mark_dispatched(token)
                pending = guard.finish(token, ToolResult(json.dumps({**good, "production_queue": {"status": "needs_attention"}})))
                self.assertEqual(pending.error_type, "studio_outcome_unknown")
                self.assertEqual(guard.prepare(request)[1].error_type, "write_outcome_pending")


class RealReceiptIntegration(unittest.TestCase):
    from tests import test_production_task_tools as fixtures
    setUp = fixtures.ProductionTaskToolHttpTests.setUp
    skill = fixtures.ProductionTaskToolHttpTests.skill
    header = fixtures.ProductionTaskToolHttpTests.header
    candidate = fixtures.ProductionTaskToolHttpTests.candidate
    context = fixtures.ProductionTaskToolHttpTests.context
    counts = fixtures.ProductionTaskToolHttpTests.counts

    def execute_guarded(self, request, context=None):
        from creatoros.tools.execution import execute_tool_call
        context = context or replace(self.context(), session_file=self.root / "guard-ledger.json")
        guard = UnknownWriteGuard(context)
        token, blocked = guard.prepare(request)
        self.assertIsNone(blocked)
        guard.mark_dispatched(token)
        result = execute_tool_call(request, context, model_requested=True)
        self.assertFalse(result.is_error, result.content)
        replacement = guard.finish(token, result)
        return guard, result, replacement

    def test_real_duplicate_batch_and_tasks_receipts_are_confirmed(self):
        batch = call("start_content_run", action="start", topic_ids=["review-1", "review-1"])
        guard, result, replacement = self.execute_guarded(batch)
        data = json.loads(result.content)
        self.assertEqual([row["topic_id"] for row in data["items"]], ["review-1"])
        self.assertIsNone(replacement, result.content)
        self.assertEqual(json.loads(guard.path.read_text(encoding="utf-8"))["records"][-1]["phase"], "confirmed")
        # The batch may still be changing review-1 asynchronously. Use a
        # separate topic so this case tests a task receipt rather than racing
        # the first operation's production scope snapshot.
        guard, result, replacement = self.execute_guarded(task("review-2"))
        self.assertIsNone(replacement, result.content)
        self.assertEqual(json.loads(guard.path.read_text(encoding="utf-8"))["records"][-1]["phase"], "confirmed")

    def test_real_topic_update_checks_request_identity_and_scope(self):
        response = self.client.get("/api/topics/review-1", headers=self.header())
        self.assertEqual(response.status_code, 200, response.text)
        request = call("manage_topics", action="update", topic_id="review-1", changes={"brief": "已更新"},
                       expected_digest=response.json()["edit_digest"])
        guard, result, replacement = self.execute_guarded(request)
        data = json.loads(result.content)
        self.assertEqual(data["creator_id"], "review-lab")
        self.assertTrue(data["request_id"])
        self.assertIsNone(replacement, result.content)
        self.assertEqual(json.loads(guard.path.read_text(encoding="utf-8"))["records"][-1]["phase"], "confirmed")

    def test_real_stale_topic_digest_is_definite_rejection_not_unknown(self):
        from creatoros.tools.execution import execute_tool_call
        response = self.client.get("/api/topics/review-1", headers=self.header())
        self.assertEqual(response.status_code, 200, response.text)
        stale = response.json()["edit_digest"]
        changed = self.client.patch("/api/topics/review-1", json={"brief": "UI的新内容"})
        self.assertEqual(changed.status_code, 200, changed.text)
        request = call("manage_topics", action="update", topic_id="review-1", changes={"brief": "过期内容"},
                       expected_digest=stale)
        context = replace(self.context(), session_file=self.root / "guard-ledger.json")
        guard = UnknownWriteGuard(context)
        token, _ = guard.prepare(request)
        guard.mark_dispatched(token)
        result = execute_tool_call(request, context, model_requested=True)
        self.assertTrue(result.is_error)
        self.assertEqual((result.error_type, result.details["http_status"]), ("conflict", 409))
        self.assertEqual(json.loads(result.content)["message"], "选题已发生变化，请重新读取后再修改。")
        self.assertIsNone(guard.finish(token, result))
        self.assertEqual(json.loads(guard.path.read_text(encoding="utf-8"))["records"], [])
        self.assertEqual(self.client.get("/api/topics/review-1", headers=self.header()).json()["brief"], "UI的新内容")
        self.assertIsNone(guard.prepare(request)[1])

    def test_real_fresh_receipt_and_injected_queue_needs_attention(self):
        from copy import deepcopy
        from unittest.mock import patch
        from creatoros.integrations.studio import StudioClient
        from creatoros.tools.execution import execute_tool_call
        from creatoros.storage.models import ContentRun, ContentRunStatus
        source = self.runs.create("review-1")
        with self.database.session() as db:
            db.get(ContentRun, source.id).status = ContentRunStatus.FAILED
        source = self.runs.get(source.id)
        request = call("start_content_run", action="fresh", source_run_id=source.id, expected_version=source.version)
        context = replace(self.context(), session_file=self.root / "guard-ledger.json")
        guard = UnknownWriteGuard(context)
        token, blocked = guard.prepare(request)
        self.assertIsNone(blocked)
        guard.mark_dispatched(token)
        record = json.loads(guard.path.read_text(encoding="utf-8"))["records"][0]
        original_request = StudioClient.request
        actual_queues = []
        def attention(client, method, path, **kwargs):
            result = original_request(client, method, path, **kwargs)
            if method == "POST" and path.endswith("/execute"):
                actual_queues.append(deepcopy(result.get("production_queue")))
                result["production_queue"] = {**(result.get("production_queue") or {}), "status": "needs_attention"}
            return result
        with patch.object(StudioClient, "request", new=attention):
            result = execute_tool_call(request, context, model_requested=True)
        self.assertFalse(result.is_error, result.content)
        data = json.loads(result.content)
        self.assertEqual(len(actual_queues), 1)
        self.assertTrue(_is_confirmed_success(record, {**data, "production_queue": actual_queues[0]}), data)
        self.assertEqual(data["production_queue"]["status"], "needs_attention")
        self.assertEqual(guard.finish(token, result).error_type, "studio_outcome_unknown")
        self.assertEqual(guard.prepare(request)[1].error_type, "write_outcome_pending")

    def test_lost_ack_original_receipt_resolves_without_resubmission(self):
        from unittest.mock import patch
        from uuid import uuid4
        from creatoros.integrations.studio import StudioClient, StudioClientError
        from creatoros.tools.execution import execute_tool_call

        context = replace(self.context(), session_file=self.root / "guard-ledger.json")
        guard = UnknownWriteGuard(context)
        request = task("review-1")
        original_request = StudioClient.request
        posts = []
        def lose_ack(client, method, path, **kwargs):
            data = original_request(client, method, path, **kwargs)
            if method == "POST" and path.endswith("/production-tasks"):
                posts.append(kwargs["payload"]["request_id"])
                raise StudioClientError("controlled response loss", "studio_outcome_unknown")
            return data
        before = self.counts()
        token, blocked = guard.prepare(request)
        self.assertIsNone(blocked)
        guard.mark_dispatched(token)
        with patch.object(StudioClient, "request", new=lose_ack):
            result = execute_tool_call(request, context, model_requested=True)
        self.assertTrue(result.is_error)
        self.assertEqual(result.error_type, "studio_outcome_unknown")
        self.assertEqual(json.loads(result.content)["request_id"], posts[0])
        marked = guard.finish(token, result)
        self.assertEqual(marked.error_type, "studio_outcome_unknown")
        self.assertTrue(marked.details["outcome_unknown"])
        after = self.counts()
        self.assertEqual(after, (before[0], before[1] + 1, before[2] + 1))
        self.assertEqual(guard.prepare(request)[1].error_type, "write_outcome_pending")
        read = call("manage_topics", action="get", topic_id="review-1", production_request_id=posts[0])
        read.id = uuid4().hex
        receipt = execute_tool_call(read, context, model_requested=True)
        self.assertFalse(receipt.is_error, receipt.content)
        self.assertTrue(guard.observe_read(read, receipt))
        self.assertEqual(self.counts(), after)
        self.assertEqual(len(posts), 1)


if __name__ == "__main__":
    unittest.main()
