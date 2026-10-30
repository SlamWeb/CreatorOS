"""Transport fault injection; real model probe lives in live_dynamic_submission."""
import asyncio
import json
import threading
import unittest
from time import monotonic
from types import SimpleNamespace

from creatoros.integrations.codex import CodexDynamicTool, CodexProducerError, _DynamicToolClient


class DynamicToolTransportTests(unittest.IsolatedAsyncioTestCase):
    def adapter(self, callback, *, cancel=None, deadline=None):
        previous = lambda method, params: {"original": method}
        sync = SimpleNamespace(_approval_handler=previous)
        self.client = SimpleNamespace(_client=SimpleNamespace(_sync=sync))
        tool = CodexDynamicTool("submit_artifacts", "submit", {"type": "object"}, callback)
        adapter = _DynamicToolClient(self.client, [tool], deadline or monotonic() + 5, cancel)
        adapter.thread_id = "thread-1"
        return adapter

    def request(self, **overrides):
        return {"tool": "submit_artifacts", "threadId": "thread-1", "turnId": "turn-1",
                "callId": "call-1", "arguments": {"path": "ready.png"}, **overrides}

    async def invoke(self, adapter, **overrides):
        return await asyncio.to_thread(adapter._handle, "item/tool/call", self.request(**overrides))

    async def test_validation_error_returns_to_model_then_corrected_call_succeeds(self):
        seen = []
        def submit(args):
            seen.append(args)
            return {"accepted": args["path"] == "ready.png"}
        adapter = self.adapter(submit)
        rejected = await self.invoke(adapter, arguments={"path": "missing.png"})
        accepted = await self.invoke(adapter)
        self.assertFalse(rejected["success"])
        self.assertTrue(accepted["success"])
        self.assertEqual(len(seen), 2)

    async def test_async_callback_allows_queued_event_to_finish(self):
        received = asyncio.Event()
        async def submit(args):
            await received.wait()
            return {"accepted": True}
        adapter = self.adapter(submit)
        pending = asyncio.create_task(self.invoke(adapter))
        await asyncio.sleep(0.01)
        received.set()
        self.assertTrue((await pending)["success"])

    async def test_unknown_tool_thread_namespace_or_invalid_arguments_are_rejected(self):
        def forbidden(args):
            self.fail("invalid request reached handler")
        adapter = self.adapter(forbidden)
        for overrides in ({"tool": "other"}, {"threadId": "other"}, {"namespace": "other"},
                          {"arguments": "{}"}, {"turnId": ""}, {"callId": ""}):
            with self.subTest(overrides=overrides):
                response = await self.invoke(adapter, **overrides)
                self.assertFalse(response["success"])
                self.assertEqual(json.loads(response["contentItems"][0]["text"])["error"], "invalid_tool_request")

    async def test_host_failure_is_unknown_not_safe_to_repeat(self):
        def failure(args):
            raise OSError("receipt write failed")
        result = await self.invoke(self.adapter(failure))
        body = json.loads(result["contentItems"][0]["text"])
        self.assertFalse(result["success"])
        self.assertTrue(body["outcome_unknown"])
        self.assertFalse(body["retryable"])

    async def test_callback_timeout_is_not_confused_with_wait_timeout(self):
        async def failure(args):
            raise TimeoutError("storage timeout")
        result = await asyncio.wait_for(self.invoke(self.adapter(failure)), timeout=1)
        self.assertFalse(result["success"])
        self.assertIn("storage timeout", result["contentItems"][0]["text"])

    async def test_other_server_requests_preserve_original_policy(self):
        adapter = self.adapter(lambda args: {"accepted": True})
        self.assertEqual(adapter._handle("item/fileChange/requestApproval", {}),
                         {"original": "item/fileChange/requestApproval"})
        adapter.close_handler()
        self.assertIs(self.client._client._sync._approval_handler, adapter.previous)

    async def test_missing_sdk_hook_is_explicit_error(self):
        with self.assertRaises(CodexProducerError) as error:
            _DynamicToolClient(object(), [CodexDynamicTool("x", "x", {}, lambda _: {})], monotonic()+5, None)
        self.assertEqual(error.exception.error_type, "codex_dynamic_tools_unsupported")

    async def test_cancel_returns_unknown_without_hanging_reader(self):
        cancel = threading.Event()
        cancel.set()
        def forbidden(args):
            self.fail("Cancelled request must not execute callback")
        adapter = self.adapter(forbidden, cancel=cancel)
        result = await self.invoke(adapter)
        self.assertFalse(result["success"])

    async def test_thread_start_sends_dynamic_tools_and_resume_keeps_definitions(self):
        adapter = self.adapter(lambda args: {"accepted": True})
        wire = []
        async def request(method, params, **kwargs):
            wire.append((method, params))
            return SimpleNamespace(thread=SimpleNamespace(id="new-thread"))
        async def resume(thread_id, **kwargs):
            wire.append(("resume", kwargs))
            return SimpleNamespace(id=thread_id)
        self.client._client.request = request
        self.client.thread_resume = resume
        started = await adapter.thread_start(cwd="D:/fixture", model="probe")
        self.assertEqual(started.id, "new-thread")
        self.assertEqual(wire[0][1]["dynamicTools"][0]["name"], "submit_artifacts")
        self.assertEqual(wire[0][1]["approvalPolicy"], "never")
        resumed = await adapter.thread_resume(started.id, cwd="D:/fixture")
        self.assertEqual(resumed.id, started.id)
        self.assertNotIn("dynamicTools", wire[1][1])


if __name__ == "__main__":
    unittest.main()
