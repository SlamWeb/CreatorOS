from types import SimpleNamespace
import unittest
from unittest.mock import patch

from creatoros.ai.types import ToolCall
from creatoros.evaluation.benchmark.local_dispatch_guard import DispatchGuard, apply_gate, install, verify_installed
from creatoros.evaluation.benchmark.nonvideo_guard import policy, validate
from creatoros.tools.results import ToolResult


class LocalDispatchGuardTests(unittest.TestCase):
    def test_real_ownership_tools_reach_original_but_paid_or_network_dispatch_does_not(self):
        forwarded = []
        def original(call, context=None, *, model_requested=False):
            forwarded.append((call.name, call.arguments))
            return ToolResult("real product boundary reached")
        guard = DispatchGuard()
        execute = guard.wrap(original)
        context = SimpleNamespace(archive_only_reads=True)
        for name, args in (("manage_topics", '{"action":"get","topic_id":"foreign"}'),
                           ("manage_topics", '{"action":"update","topic_id":"foreign"}'),
                           ("get_producer_skill", '{"skill_id":"local"}')):
            result = execute(ToolCall(id=name + args, name=name, arguments=args), context, model_requested=True)
            self.assertFalse(result.is_error)
        for name in ("start_content_run", "manage_plan", "research_series_topics", "write_file",
                     "manage_material", "prepare_publication", "unknown_future_tool"):
            result = execute(ToolCall(id=name, name=name, arguments="{}"), context, model_requested=True)
            self.assertTrue(result.is_error)
            self.assertEqual(result.error_type, "local_dispatch_guard_denied")
        self.assertEqual(len(forwarded), 3)
        gate = guard.score([])
        self.assertEqual(gate["status"], "failed")
        self.assertEqual(guard.score(None, background_started=True)["status"], "failed")
        scores = apply_gate({"critical_checks": [], "critical_status": "passed"}, gate)
        self.assertEqual(scores["critical_status"], "failed")
        self.assertEqual(scores["critical_checks"][0]["id"], "local_dispatch_guard")

    def test_read_file_only_when_host_has_archive_only_scope(self):
        original = lambda *_a, **_kw: ToolResult("allowed")
        for context in (None, SimpleNamespace(archive_only_reads=False)):
            guard = DispatchGuard()
            self.assertTrue(guard.wrap(original)(ToolCall(id="1", name="read_file", arguments="{}"), context,
                                                  model_requested=True).is_error)
        self.assertFalse(DispatchGuard().wrap(original)(ToolCall(id="1", name="read_file", arguments="{}"),
            SimpleNamespace(archive_only_reads=True), model_requested=True).is_error)

    def test_missing_trace_cannot_pass_guard(self):
        guard = DispatchGuard()
        call = ToolCall(id="read-1", name="manage_topics", arguments="{}")
        guard.wrap(lambda *_a, **_kw: ToolResult("ok"))(call, model_requested=True)
        self.assertEqual(guard.score([])["status"], "needs_review")
        self.assertEqual(guard.score([{"tool_call_id": "read-1"}])["status"], "passed")
        self.assertEqual(guard.score([{"tool_call_id": "read-1"}], background_started=True)["status"], "needs_review")
        self.assertEqual(guard.score(None)["status"], "needs_review")

    def test_new_policy_is_explicit_and_old_policy_unchanged(self):
        self.assertNotIn("local_dispatch", policy(["B06"]))
        with self.assertRaises(ValueError):
            policy(["B31"])
        p = policy(["B31", "B40"], local_dispatch=True)
        self.assertEqual(p["local_dispatch"], "local-dispatch-v3")
        self.assertTrue(validate({"selected_case_ids": ["B31", "B40"], "model_configuration": {"benchmark_guard": p}}))
        old = policy(["B31"], local_dispatch="local-dispatch-v1")
        self.assertTrue(validate({"selected_case_ids": ["B31"], "model_configuration": {"benchmark_guard": old}}))
        with self.assertRaises(ValueError):
            policy(["B47"], local_dispatch=True)
        for version in ("local-dispatch-v1", "local-dispatch-v2"):
            with self.assertRaises(ValueError):
                policy(["B08"], local_dispatch=version)
        self.assertEqual(policy(["B08"], local_dispatch="local-dispatch-v3")["local_dispatch"], "local-dispatch-v3")

    def test_collection_v2_read_only_and_v1_still_denies(self):
        forwarded = []
        def original(call, context=None, *, model_requested=False):
            forwarded.append(call.arguments)
            return ToolResult("ok")
        for version in ("local-dispatch-v1", "local-dispatch-v2", "local-dispatch-v3"):
            for args, safe in (( '{"action":"list"}', True), ('{"action":"get"}', True),
                               ('{"action":"export"}', False), ('{"action":"create"}', False),
                               ('{"action":"update"}', False), ('{"action":"remove"}', False),
                               ('{"action":"future"}', False), ('{}', False), ('[]', False),
                               ('{', False), ('{"action":"list","action":"export"}', False)):
                guard = DispatchGuard(version)
                result = guard.wrap(original)(ToolCall("collection", "manage_media_collection", args), model_requested=True)
                self.assertEqual(not result.is_error, safe and version in {"local-dispatch-v2", "local-dispatch-v3"}, (version, args))
                if result.is_error:
                    self.assertEqual(guard.score(None)["status"], "failed")
        self.assertEqual(len(forwarded), 4)

    def test_receipt_requires_actual_installed_wrapper_and_deferred_state(self):
        import creatoros.agent.loop as loop
        with patch.object(loop, "execute_tool_call", lambda *_a, **_kw: ToolResult("ok")):
            guard = install()
            app = SimpleNamespace(state=SimpleNamespace(background_services_started=False))
            verify_installed(guard, app)
            app.state.background_services_started = True
            with self.assertRaises(RuntimeError):
                verify_installed(guard, app)
            app.state.background_services_started = False
            loop.execute_tool_call = lambda *_a, **_kw: ToolResult("bypassed")
            with self.assertRaises(RuntimeError):
                verify_installed(guard, app)


if __name__ == "__main__":
    unittest.main()
