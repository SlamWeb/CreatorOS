"""Isolated regressions for chat view publication and side-effect boundaries."""
import ctypes
import errno
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from creatoros.ai.types import StreamEnd, TextDelta, ToolCallDelta
from creatoros.integrations import atomic_file
from creatoros.session.snapshot import load_messages
from creatoros.tools.results import ToolResult
from creatoros.web import chat


def sharing_error():
    error = PermissionError("controlled Windows sharing violation")
    error.winerror = 5
    return error


class ViewFileTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="chat-view-atomic-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "view.json"
        self.path.write_text('{"old":true}', encoding="utf-8")

    def test_temporary_names_are_independent_even_when_writers_overlap(self):
        legacy = self.path.with_suffix(".tmp")
        legacy.write_bytes(b"other writer")
        replacements = []
        replace = atomic_file.os.replace

        def record(source, target):
            replacements.append(Path(source))
            replace(source, target)

        with patch.object(atomic_file.os, "replace", side_effect=record):
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda n: chat._write(self.path, {"number": n}), range(8)))
        self.assertIn(json.loads(self.path.read_text(encoding="utf-8"))["number"], range(8))
        self.assertEqual(legacy.read_bytes(), b"other writer")
        self.assertEqual(len(set(replacements)), 8)
        self.assertTrue(all(item.name.startswith(".view.json.") for item in replacements))
        self.assertFalse(any(item.exists() for item in replacements))

    def test_disk_full_fails_immediately_preserving_old_view(self):
        before = self.path.read_bytes()
        error = OSError(errno.ENOSPC, "controlled disk full")
        with patch.object(atomic_file.os, "replace", side_effect=error) as replace, \
             patch.object(atomic_file, "sleep") as sleep:
            with self.assertRaises(OSError) as raised:
                chat._write(self.path, {"new": True})
        self.assertIs(raised.exception, error)
        self.assertEqual(replace.call_count, 1)
        sleep.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.root.glob(".view.json.*.tmp")), [])

    def test_sharing_conflict_exhaustion_preserves_old_view(self):
        before = self.path.read_bytes()
        with patch.object(atomic_file.os, "replace", side_effect=sharing_error()) as replace, \
             patch.object(atomic_file, "sleep") as sleep:
            with self.assertRaises(PermissionError):
                chat._write(self.path, {"new": True})
        self.assertEqual(replace.call_count, len(atomic_file.RETRY_DELAYS) + 1)
        self.assertEqual(sleep.call_count, len(atomic_file.RETRY_DELAYS))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.root.glob(".view.json.*.tmp")), [])

    def test_transient_windows_sharing_errors_retry_only_publication(self):
        for code in (5, 32, 33):
            with self.subTest(winerror=code):
                attempts, replace = [], atomic_file.os.replace
                def fail_once(source, destination):
                    attempts.append(Path(source))
                    if len(attempts) == 1:
                        error = sharing_error()
                        error.winerror = code
                        raise error
                    return replace(source, destination)
                with patch.object(atomic_file.os, "replace", side_effect=fail_once), \
                     patch.object(atomic_file, "sleep") as sleep:
                    chat._write(self.path, {"winerror": code})
                self.assertEqual(len(attempts), 2)
                self.assertEqual(attempts[0], attempts[1])
                sleep.assert_called_once()
                self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"winerror": code})


@unittest.skipUnless(os.name == "nt", "Requires real Windows sharing semantics")
class ViewWindowsTests(ViewFileTests):
    def test_real_windows_reader_releases_during_bounded_retry(self):
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.CreateFileW(str(self.path), 0x80000000, 0x1 | 0x2, None, 3, 0x80, None)
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        closed = []

        def release(_delay):
            if not closed:
                self.assertTrue(kernel.CloseHandle(handle))
                closed.append(True)

        try:
            with patch.object(atomic_file, "sleep", side_effect=release) as sleep:
                chat._write(self.path, {"new": True})
            self.assertTrue(closed)
            self.assertEqual(sleep.call_count, 1)
            self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"new": True})
        finally:
            if not closed:
                kernel.CloseHandle(handle)


class ViewLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="chat-view-lifecycle-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.providers = []
        self.continue_provider = Event()
        owner = self

        class Provider:
            def __init__(self):
                self.calls = 0
                self.closed = False
                self.client = SimpleNamespace(close=self.close)
                owner.providers.append(self)

            def close(self):
                self.closed = True

            def stream(self, context):
                self.calls += 1
                if not owner.continue_provider.wait(10):
                    raise RuntimeError("test provider was not released")
                if self.calls == 1:
                    yield ToolCallDelta(0, "call-1", "manage_topics", '{"action":"list","series_id":"local"}')
                    yield StreamEnd("tool_calls")
                else:
                    yield TextDelta("done")
                    yield StreamEnd("stop")

        self.host = chat.AgentChatService(self.root, provider_factory=Provider)
        self.sid = self.host.create()["id"]
        self.view_path = self.host._path(self.sid)
        self.history_path = self.view_path.with_name("messages.json")
        self.addCleanup(self.host.shutdown)

    def assert_clean(self):
        self.assertNotIn(self.sid, self.host.active)
        self.assertNotIn(self.sid, self.host.providers)
        self.assertNotIn(self.sid, self.host.threads)
        self.assertTrue(self.providers[0].closed)

    def start_continuation_and_wait(self, case, responses):
        ready = Event()
        original_factory = case.chat.provider_factory
        def factory():
            provider = original_factory()
            stream = provider.stream
            def paused_stream(context):
                if not ready.wait(10):
                    raise RuntimeError("test continuation was not released")
                yield from stream(context)
            provider.stream = paused_stream
            return provider
        case.chat.provider_factory = factory
        case.responses = responses
        case.chat._resume_plans()
        thread = case.chat.threads[case.sid]
        ready.set()
        thread.join(15)
        self.assertFalse(thread.is_alive())
        self.assertNotIn(case.sid, case.chat.active)
        return case.chat._read(case.sid)

    def test_failure_before_submit_starts_no_worker_and_keeps_old_files(self):
        view, history = self.view_path.read_bytes(), self.history_path.read_bytes()
        with patch.object(chat, "_write", side_effect=sharing_error()):
            with self.assertRaises(HTTPException) as raised:
                self.host.submit(self.sid, "req-1", "hello", 0, "http://127.0.0.1:1")
        self.assertEqual(raised.exception.status_code, 503)
        self.assertIn("未开始", raised.exception.detail)
        self.assert_clean()
        self.assertEqual(self.providers[0].calls, 0)
        self.assertEqual(self.view_path.read_bytes(), view)
        self.assertEqual(self.history_path.read_bytes(), history)
        effects = []
        def tool(*_args, **_kwargs):
            effects.append("executed")
            return ToolResult(content='{"ok":true}')
        # Only an explicit request after the failure can start the work.
        with patch("creatoros.agent.loop.execute_tool_call", side_effect=tool):
            self.host.submit(self.sid, "req-1", "hello", 0, "http://127.0.0.1:1")
            thread = self.host.threads[self.sid]
            self.continue_provider.set()
            thread.join(15)
        self.assertFalse(thread.is_alive())
        self.assertEqual(effects, ["executed"])
        self.assertEqual(len(self.providers), 2)

    def test_initial_failure_http_receipt_proves_the_worker_did_not_start(self):
        from creatoros.web.chat_routes import chat_routes
        app = FastAPI()
        app.include_router(chat_routes(self.host))
        before = self.view_path.read_bytes()
        with TestClient(app, base_url="http://127.0.0.1:8876") as client, \
             patch.object(chat, "_write", side_effect=sharing_error()):
            response = client.post(f"/api/agent/sessions/{self.sid}/turns", json={
                "request_id": str(uuid4()), "text": "hello", "expected_version": 0,
            })
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "chat_view_not_started")
        self.assertIn("未开始", response.json()["error"]["message"])
        self.assert_clean()
        self.assertEqual(self.providers[0].calls, 0)
        self.assertEqual(self.view_path.read_bytes(), before)

    def assert_failed_projection_does_not_replay(self, after_tool):
        effects, last_saved = [], []
        write = chat._write

        def fail(path, doc):
            trigger = (any(item.get("kind") == "tool" and item.get("status") == "done" for item in doc["entries"])
                       if after_tool else any(item.get("kind") == "assistant" and item.get("model_request_id")
                                              for item in doc["entries"]))
            if trigger:
                if not last_saved:
                    last_saved.append(path.read_bytes())
                raise sharing_error()
            return write(path, doc)

        def tool(*_args, **_kwargs):
            effects.append("executed")
            return ToolResult(content='{"ok":true}')

        with patch.object(chat, "_write", side_effect=fail), \
             patch("creatoros.agent.loop.execute_tool_call", side_effect=tool):
            self.host.submit(self.sid, "req-1", "hello", 0, "http://127.0.0.1:1")
            thread = self.host.threads[self.sid]
            self.continue_provider.set()
            thread.join(15)
            self.assertFalse(thread.is_alive())
            self.assert_clean()
            self.assertEqual(effects, ["executed"] if after_tool else [])
            self.assertEqual(self.providers[0].calls, 1)
            self.assertEqual(self.view_path.read_bytes(), last_saved[0])
            self.assertEqual(json.loads(self.view_path.read_text(encoding="utf-8"))["status"], "running")
            view = self.host.get(self.sid)
            self.assertEqual(view["status"], "interrupted")
            self.assertIn("保存失败", view["error"])
            self.assertIn("核对", view["error"])
            with self.assertRaises(HTTPException) as duplicate:
                self.host.submit(self.sid, "req-1", "hello", 0, "http://127.0.0.1:1")
            self.assertEqual(duplicate.exception.status_code, 503)
            with self.assertRaises(HTTPException):
                self.host.submit(self.sid, "req-2", "hello again", view["version"], "http://127.0.0.1:1")
            self.assertEqual(effects, ["executed"] if after_tool else [])

        # A new host repairs only the status and message pairing, not the tool.
        restarted = chat.AgentChatService(self.root, provider_factory=lambda: self.fail("unexpected provider"))
        restarted.start()
        self.assertEqual(restarted.get(self.sid)["status"], "interrupted")
        self.assertEqual(effects, ["executed"] if after_tool else [])
        self.assertTrue(any(m.get("role") == "tool" and "结果未知" in m.get("content", "")
                            for m in load_messages(self.history_path)))

    def test_failure_after_model_response_before_tool_dispatch_does_not_replay(self):
        self.assert_failed_projection_does_not_replay(False)

    def test_failure_after_tool_effect_does_not_replay(self):
        self.assert_failed_projection_does_not_replay(True)

    def test_transient_view_sharing_failure_after_effect_only_retries_file_write(self):
        effects, errors = [], []
        replace = atomic_file.os.replace

        def fail_once(source, destination):
            if Path(destination) == self.view_path and not errors:
                payload = json.loads(Path(source).read_text(encoding="utf-8"))
                if any(item.get("kind") == "tool" and item.get("status") == "done" for item in payload["entries"]):
                    errors.append(True)
                    raise sharing_error()
            return replace(source, destination)

        def tool(*_args, **_kwargs):
            effects.append("executed")
            return ToolResult(content='{"ok":true}')

        with patch.object(atomic_file.os, "replace", side_effect=fail_once), \
             patch.object(atomic_file, "sleep"), \
             patch("creatoros.agent.loop.execute_tool_call", side_effect=tool):
            self.host.submit(self.sid, "req-1", "hello", 0, "http://127.0.0.1:1")
            thread = self.host.threads[self.sid]
            self.continue_provider.set()
            thread.join(15)
        self.assertFalse(thread.is_alive())
        self.assert_clean()
        self.assertEqual(errors, [True])
        self.assertEqual(effects, ["executed"])
        self.assertEqual(self.providers[0].calls, 2)
        self.assertEqual(self.host.get(self.sid)["status"], "idle")
        self.assertTrue(any(m.get("role") == "tool" and '"ok"' in m.get("content", "")
                            for m in load_messages(self.history_path)))

    def test_failed_view_blocks_only_its_own_session(self):
        other = self.host.create()["id"]
        effects, write = [], chat._write
        def fail_one(path, doc):
            if path == self.view_path and any(entry.get("kind") == "tool" and entry.get("status") == "done"
                                              for entry in doc["entries"]):
                raise sharing_error()
            return write(path, doc)
        def tool(*_args, **kwargs):
            effects.append(kwargs["context"].agent_session_id)
            return ToolResult(content='{"ok":true}')
        with patch.object(chat, "_write", side_effect=fail_one), \
             patch("creatoros.agent.loop.execute_tool_call", side_effect=tool):
            self.host.submit(self.sid, "req-1", "hello", 0, "http://127.0.0.1:1")
            first = self.host.threads[self.sid]
            self.continue_provider.set()
            first.join(15)
            self.assertFalse(first.is_alive())
            self.continue_provider.clear()
            self.host.submit(other, "req-2", "independent", 0, "http://127.0.0.1:1")
            second = self.host.threads[other]
            self.continue_provider.set()
            second.join(15)
            self.assertFalse(second.is_alive())
            self.assertEqual(effects, [self.sid, other])
            self.assertEqual(self.host.get(self.sid)["status"], "interrupted")
            other_view = self.host.get(other)
            self.assertEqual(other_view["status"], "idle")
            self.assertFalse(other_view.get("view_persistence_pending", False))
            self.assertIsNone(other_view["error"])

    def test_start_isolates_one_persistently_unwritable_running_view(self):
        other = self.host.create()["id"]
        for session_id in (self.sid, other):
            doc = self.host._read(session_id)
            doc["status"] = "running"
            doc["requests"].append({"id": "old-request", "text": "old", "status": "running"})
            self.host._save(doc)
        before = self.view_path.read_bytes()
        write = chat._write
        def deny_one(path, doc):
            if path == self.view_path:
                raise sharing_error()
            return write(path, doc)
        restarted = chat.AgentChatService(self.root, provider_factory=self.host.provider_factory)
        with patch.object(chat, "_write", side_effect=deny_one):
            restarted.start()
            failed = restarted.get(self.sid)
            self.assertEqual(failed["status"], "interrupted")
            self.assertTrue(failed["view_persistence_pending"])
            self.assertIn("核对", failed["error"])
            self.assertEqual(self.view_path.read_bytes(), before)
            with self.assertRaises(HTTPException) as blocked:
                restarted.submit(self.sid, "new-request", "hello", failed["version"], "http://127.0.0.1:1")
            self.assertEqual(blocked.exception.status_code, 503)
            other_view = restarted.get(other)
            self.assertEqual(other_view["status"], "interrupted")
            self.assertFalse(other_view.get("view_persistence_pending", False))
            self.assertEqual(json.loads(restarted._path(other).read_text(encoding="utf-8"))["status"], "interrupted")
            self.assertEqual(self.providers, [])
            effects = []
            def tool(*_args, **_kwargs):
                effects.append("other session")
                return ToolResult(content='{"ok":true}')
            with patch("creatoros.agent.loop.execute_tool_call", side_effect=tool):
                restarted.submit(other, "other-new", "continue", other_view["version"], "http://127.0.0.1:1")
                thread = restarted.threads[other]
                self.continue_provider.set()
                thread.join(15)
            self.assertFalse(thread.is_alive())
            self.assertEqual(effects, ["other session"])
            self.assertEqual(restarted.get(other)["status"], "idle")

    def test_plan_continuation_with_persist_failure_stays_unknown(self):
        from tests import test_plan_continuation_unknown_write as fixture
        case = fixture.ContinuationUnknownWriteTests("runTest")
        case.setUp()
        try:
            case.mode = "commit_success"
            write = chat._write
            saved = []

            def deny_after_effect(path, doc):
                if case.committed:
                    if not saved:
                        saved.append(path.read_bytes())
                    raise sharing_error()
                return write(path, doc)

            with patch.object(chat, "_write", side_effect=deny_after_effect):
                doc = self.start_continuation_and_wait(case, [[case.plan_call("resume", expected_version=case.version)], []])
                self.assertEqual(case.committed, 1)
                self.assertEqual(case.providers[0].calls, 1)
                self.assertEqual(doc["status"], "interrupted")
                self.assertEqual(doc["requests"][0]["continuation_outcome"], "unknown")
                self.assertEqual(case.plans.get(case.sid, case.pid)["continuation_state"], "unknown")
                case.chat._resume_plans()
                self.assertEqual(len(case.providers), 1)
                self.assertEqual(case.committed, 1)
                self.assertEqual(case.chat._path(case.sid).read_bytes(), saved[0])
        finally:
            case.doCleanups()

    def test_plan_complete_answer_with_failed_final_save_is_not_acknowledged(self):
        from tests import test_plan_continuation_unknown_write as fixture
        case = fixture.ContinuationUnknownWriteTests("runTest")
        case.setUp()
        try:
            write = chat._write
            def deny_terminal(path, doc):
                if doc.get("status") != "running":
                    raise sharing_error()
                return write(path, doc)
            with patch.object(chat, "_write", side_effect=deny_terminal):
                doc = self.start_continuation_and_wait(case, [[]])
                saved = json.loads(case.chat._path(case.sid).read_text(encoding="utf-8"))
                self.assertTrue(any(e.get("kind") == "assistant" and e.get("complete") for e in saved["entries"]))
                self.assertEqual(saved["status"], "running")
                self.assertEqual(doc["status"], "interrupted")
                self.assertEqual(doc["requests"][0]["continuation_outcome"], "unknown")
                self.assertEqual(case.plans.get(case.sid, case.pid)["continuation_state"], "unknown")
                case.chat._resume_plans()
                self.assertEqual(len(case.providers), 1)
        finally:
            case.doCleanups()

    def test_plan_continuation_initial_save_failure_starts_nothing(self):
        from creatoros.agent.plans import PlanService
        from tests import test_plan_continuation_unknown_write as fixture
        case = fixture.ContinuationUnknownWriteTests("runTest")
        case.setUp()
        try:
            before = case.chat._path(case.sid).read_bytes()
            with patch.object(chat, "_write", side_effect=sharing_error()):
                case.chat._resume_plans()
            self.assertEqual(len(case.providers), 1)
            self.assertEqual(case.providers[0].calls, 0)
            self.assertNotIn(case.sid, case.chat.active)
            self.assertNotIn(case.sid, case.chat.providers)
            self.assertNotIn(case.sid, case.chat.threads)
            self.assertEqual(case.chat.get(case.sid)["status"], "interrupted")
            self.assertIn("未开始", case.chat.get(case.sid)["error"])
            self.assertEqual(case.chat._path(case.sid).read_bytes(), before)
            self.assertEqual(case.plans.get(case.sid, case.pid)["continuation_state"], "failed")
            case.chat._resume_plans()
            self.assertEqual(len(case.providers), 1)
            # The failed continuation never reached disk: a fresh page after
            # restart sees the previous idle view, and the plan stays failed.
            restarted = chat.AgentChatService(case.root / "chats", provider_factory=lambda: self.fail("unexpected provider"),
                                              creator_lookup=lambda _: SimpleNamespace(is_active=True))
            plans = PlanService(case.db, restarted)
            restarted.bind_plan_service(plans)
            restarted.start()
            self.assertEqual(restarted.get(case.sid)["status"], "idle")
            restarted._resume_plans()
            self.assertEqual(plans.get(case.sid, case.pid)["continuation_state"], "failed")
            self.assertEqual(case.providers[0].calls, 0)
        finally:
            case.doCleanups()

    def test_failed_plan_receipt_after_tool_is_recovered_as_unknown_on_restart(self):
        from creatoros.agent.plans import PlanService
        from tests import test_plan_continuation_unknown_write as fixture
        case = fixture.ContinuationUnknownWriteTests("runTest")
        case.setUp()
        try:
            case.mode = "commit_success"
            write = chat._write
            thread_errors = []

            def deny_after_effect(path, doc):
                if case.committed:
                    raise sharing_error()
                return write(path, doc)

            with patch.object(chat, "_write", side_effect=deny_after_effect), \
                 patch.object(case.plans, "fail_continuation", side_effect=OSError(errno.ENOSPC, "database unavailable")), \
                 patch.object(threading, "excepthook", side_effect=lambda args: thread_errors.append(args.exc_value)):
                self.start_continuation_and_wait(case, [[case.plan_call("resume", expected_version=case.version)], []])
                self.assertEqual(case.committed, 1)
                self.assertEqual(case.plans.get(case.sid, case.pid)["continuation_state"], "executing")
                case.chat._resume_plans()
                self.assertEqual(len(case.providers), 1)
            self.assertEqual(len(thread_errors), 1)
            # A second boot with the disk fault still present must keep this
            # session blocked and must not dispatch its executing plan.
            still_broken = chat.AgentChatService(case.root / "chats", provider_factory=lambda: self.fail("unexpected provider"),
                                                 creator_lookup=lambda _: SimpleNamespace(is_active=True))
            blocked_plans = PlanService(case.db, still_broken)
            still_broken.bind_plan_service(blocked_plans)
            with patch.object(chat, "_write", side_effect=sharing_error()):
                still_broken.start()
                self.assertTrue(still_broken.get(case.sid)["view_persistence_pending"])
                still_broken._resume_plans()
                self.assertEqual(blocked_plans.get(case.sid, case.pid)["continuation_state"], "executing")
                self.assertEqual(case.committed, 1)
            restarted = chat.AgentChatService(case.root / "chats", provider_factory=lambda: self.fail("unexpected provider"),
                                              creator_lookup=lambda _: SimpleNamespace(is_active=True))
            plans = PlanService(case.db, restarted)
            restarted.bind_plan_service(plans)
            restarted.start()
            restarted._resume_plans()
            self.assertEqual(plans.get(case.sid, case.pid)["continuation_state"], "unknown")
            self.assertEqual(restarted.get(case.sid)["status"], "interrupted")
            self.assertEqual(case.committed, 1)
        finally:
            case.doCleanups()
