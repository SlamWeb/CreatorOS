"""Chat snapshot publication on isolated files; never touches a live session."""
import ctypes
import errno
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from creatoros.integrations import atomic_file
from creatoros.session.snapshot import load_messages, save_messages


class SessionSnapshotIOTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="session-snapshot-io-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "messages.json"
        self.old = [{"role": "system", "content": "system"}, {"role": "user", "content": "original"}]
        self.new = self.old + [{"role": "assistant", "content": "new reply"}]
        save_messages(self.old, self.path)

    def test_independent_temp_files_preserve_other_sibling_and_round_trip(self):
        sibling = self.path.with_suffix(".tmp")
        sibling.write_bytes(b"someone else's temporary data")
        replacements, original = [], atomic_file.os.replace
        def track(source, target):
            replacements.append(Path(source))
            original(source, target)
        with patch.object(atomic_file.os, "replace", side_effect=track):
            save_messages(self.new, self.path)
            save_messages(self.old, self.path)
        self.assertEqual(load_messages(self.path), self.old)
        self.assertEqual(sibling.read_bytes(), b"someone else's temporary data")
        self.assertEqual(len(set(replacements)), 2)
        self.assertTrue(all(path.name.startswith(".messages.json.") for path in replacements))
        self.assertFalse(any(path.exists() for path in replacements))

    def test_nonsharing_error_is_not_retried_and_old_bytes_remain(self):
        before = self.path.read_bytes()
        failure = OSError(errno.ENOSPC, "controlled disk full")
        with patch.object(atomic_file.os, "replace", side_effect=failure) as replace, \
             patch.object(atomic_file, "sleep") as sleep:
            with self.assertRaises(OSError) as raised:
                save_messages(self.new, self.path)
        self.assertIs(raised.exception, failure)
        self.assertEqual(replace.call_count, 1)
        sleep.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.root.glob(".messages.json.*.tmp")), [])

    def test_session_and_integration_import_orders_do_not_cycle(self):
        scripts = (
            "import sys; import creatoros.session.snapshot; "
            "assert 'creatoros.integrations' not in sys.modules; import creatoros.integrations.extraction_activity",
            "import creatoros.integrations.extraction_activity; import creatoros.session.snapshot",
        )
        for script in scripts:
            with self.subTest(script=script):
                result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_existing_read_error_propagates_without_changing_old_bytes(self):
        before = self.path.read_bytes()
        error = PermissionError("controlled read failure")
        original = Path.read_text
        def deny(path, *args, **kwargs):
            if path == self.path:
                raise error
            return original(path, *args, **kwargs)
        with patch.object(Path, "read_text", deny):
            with self.assertRaises(PermissionError) as raised:
                load_messages(self.path)
        self.assertIs(raised.exception, error)
        self.assertEqual(self.path.read_bytes(), before)

    def test_malformed_existing_history_raises_without_changing_its_bytes(self):
        for raw in (b"not json", b'{"role": "not a message list"}', b"\xff"):
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                with self.assertRaises((ValueError, UnicodeError)):
                    load_messages(self.path)
                self.assertEqual(self.path.read_bytes(), raw)

    def test_missing_file_initializes_without_replacing_an_existing_history(self):
        before = self.path.read_bytes()
        missing = self.root / "missing.json"
        messages = load_messages(missing)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "system")
        self.assertFalse(missing.exists())
        self.assertEqual(self.path.read_bytes(), before)

    def test_new_cli_default_session_can_initialize_and_save(self):
        from creatoros.session import snapshot
        missing = self.root / "new-cli.json"
        with patch.object(snapshot, "SESSION_FILE", missing):
            messages = snapshot.load_messages()
            snapshot.save_messages(messages)
        self.assertEqual(snapshot.load_messages(missing), messages)
        self.assertTrue(missing.exists())


class SessionSnapshotWebTests(unittest.TestCase):
    def setUp(self):
        from creatoros.ai.types import StreamEnd, TextDelta
        from creatoros.web.chat import AgentChatService
        temporary = TemporaryDirectory(prefix="session-history-web-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.providers = []
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
                yield TextDelta("controlled answer")
                yield StreamEnd("stop")
        self.host = AgentChatService(self.root, provider_factory=Provider)
        self.session = self.host.create()
        self.sid = self.session["id"]
        self.path = self.host._path(self.sid).with_name("messages.json")
        self.old = load_messages(self.path) + [{"role": "user", "content": "historical message"}]
        save_messages(self.old, self.path)
        self.addCleanup(self.host.shutdown)

    def submit_and_wait(self):
        self.host.submit(self.sid, "read-failure", "new request", self.session["version"], "http://127.0.0.1:1")
        thread = self.host.threads.get(self.sid)
        if thread is not None:
            thread.join(10)
            self.assertFalse(thread.is_alive())
        self.assertNotIn(self.sid, self.host.active)
        self.assertNotIn(self.sid, self.host.providers)
        self.assertNotIn(self.sid, self.host.threads)
        self.assertEqual(self.providers[0].calls, 0)
        self.assertTrue(self.providers[0].closed)
        view = self.host.get(self.sid)
        self.assertEqual(view["status"], "failed")
        self.assertIn("对话历史", view["error"])

    def test_unreadable_history_fails_request_and_releases_web_session(self):
        before = self.path.read_bytes()
        failure = PermissionError("controlled history read failure")
        original = Path.read_text
        def deny(path, *args, **kwargs):
            if path == self.path:
                raise failure
            return original(path, *args, **kwargs)
        with patch.object(Path, "read_text", deny):
            self.submit_and_wait()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(load_messages(self.path), self.old)

    def test_new_web_session_writes_history_before_view(self):
        from creatoros.web.chat import AgentChatService
        host = AgentChatService(self.root / "new-session")
        original = host._save
        def verify(doc):
            self.assertTrue(host._path(doc["id"]).with_name("messages.json").is_file())
            return original(doc)
        with patch.object(host, "_save", side_effect=verify):
            view = host.create()
        self.assertTrue(host._path(view["id"]).is_file())

    def test_missing_existing_web_history_is_not_reinitialized(self):
        self.assertTrue(self.path.is_file())
        self.path.unlink()
        self.submit_and_wait()
        self.assertFalse(self.path.exists())

    def test_missing_history_at_agent_second_read_does_not_reinitialize(self):
        before = self.path.read_bytes()
        original, reads = Path.read_text, []
        def disappear(path, *args, **kwargs):
            if path == self.path:
                reads.append(1)
                if len(reads) == 2:
                    raise FileNotFoundError("history disappeared after the first read")
            return original(path, *args, **kwargs)
        with patch.object(Path, "read_text", disappear):
            self.submit_and_wait()
        self.assertEqual(len(reads), 2)
        self.assertEqual(self.path.read_bytes(), before)

    def assert_transient_read_does_not_repair(self, fail_at):
        pending = self.old + [{"role": "assistant", "content": None,
                               "tool_calls": [{"id": "unfinished", "name": "lookup", "arguments": "{}"}]}]
        save_messages(pending, self.path)
        before = self.path.read_bytes()
        original, reads = Path.read_text, []
        def fail_once(path, *args, **kwargs):
            if path == self.path:
                reads.append(1)
                if len(reads) == fail_at:
                    raise PermissionError("one temporary read failure")
            return original(path, *args, **kwargs)
        with patch.object(Path, "read_text", fail_once):
            self.submit_and_wait()
        self.assertEqual(len(reads), fail_at)
        self.assertEqual(self.path.read_bytes(), before)

    def test_transient_host_read_failure_does_not_trigger_automatic_repair(self):
        self.assert_transient_read_does_not_repair(1)

    def test_transient_agent_read_failure_does_not_trigger_automatic_repair(self):
        self.assert_transient_read_does_not_repair(2)

    def test_corrupt_history_fails_request_and_releases_web_session(self):
        raw = b'{"incomplete": '
        self.path.write_bytes(raw)
        self.submit_and_wait()
        self.assertEqual(self.path.read_bytes(), raw)

    def test_startup_keeps_corrupt_history_and_recovers_other_session_state(self):
        doc = self.host._read(self.sid)
        doc["status"] = "running"
        doc["requests"].append({"id": "old", "status": "running"})
        self.host._save(doc)
        other = self.host.create()
        other_doc = self.host._read(other["id"])
        other_doc["status"] = "running"
        other_doc["requests"].append({"id": "other", "status": "running"})
        self.host._save(other_doc)
        raw = b"broken historical json"
        self.path.write_bytes(raw)
        self.host.start()
        view = self.host.get(self.sid)
        self.assertEqual(view["status"], "interrupted")
        self.assertIn("对话历史", view["error"])
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(self.host.get(other["id"])["status"], "interrupted")

    def test_continuation_repair_read_failure_is_not_acknowledged(self):
        from tests import test_plan_continuation_unknown_write as fixture
        case = fixture.ContinuationUnknownWriteTests("runTest")
        case.setUp()
        try:
            path = case.chat._path(case.sid).with_name("messages.json")
            original_repair = case.chat._repair
            original_read = Path.read_text
            before = []
            def deny_history(value, *args, **kwargs):
                if value == path:
                    raise PermissionError("controlled repair read failure")
                return original_read(value, *args, **kwargs)
            def fail_actual_repair(session_id):
                before.append(path.read_bytes())
                with patch.object(Path, "read_text", deny_history):
                    return original_repair(session_id)
            with patch.object(case.chat, "_repair", side_effect=fail_actual_repair):
                doc = case.start_continuation([[]])
            self.assertEqual(case.providers[0].calls, 1)
            self.assertEqual(doc["status"], "failed")
            self.assertEqual(doc["requests"][0]["continuation_outcome"], "unknown")
            self.assertEqual(case.plans.get(case.sid, case.pid)["continuation_state"], "unknown")
            self.assertEqual(len(before), 1)
            self.assertEqual(path.read_bytes(), before[0])
            case.chat._resume_plans()
            self.assertEqual(len(case.providers), 1)
        finally:
            case.doCleanups()

    def test_continuation_cannot_start_or_retry_when_history_is_unreadable(self):
        from tests import test_plan_continuation_unknown_write as fixture
        case = fixture.ContinuationUnknownWriteTests("runTest")
        case.setUp()
        try:
            path = case.chat._path(case.sid).with_name("messages.json")
            before = path.read_bytes()
            original = Path.read_text
            def deny_history(value, *args, **kwargs):
                if value == path:
                    raise PermissionError("controlled history read failure")
                return original(value, *args, **kwargs)
            with patch.object(Path, "read_text", deny_history):
                doc = case.start_continuation([[]])
            self.assertEqual(case.providers[0].calls, 0)
            self.assertEqual(doc["status"], "failed")
            self.assertEqual(doc["requests"][0]["continuation_outcome"], "failed")
            self.assertEqual(case.plans.get(case.sid, case.pid)["continuation_state"], "failed")
            self.assertEqual(path.read_bytes(), before)
            case.chat._resume_plans()
            self.assertEqual(len(case.providers), 1)
        finally:
            case.doCleanups()


@unittest.skipUnless(os.name == "nt", "Requires real Windows sharing semantics")
class SessionSnapshotWindowsTests(unittest.TestCase):
    def setUp(self):
        SessionSnapshotIOTests.setUp(self)
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel = kernel
        self.handle = kernel.CreateFileW(str(self.path), 0x80000000, 0x1 | 0x2, None, 3, 0x80, None)
        if self.handle == wintypes.HANDLE(-1).value:
            self.handle = None
            raise ctypes.WinError(ctypes.get_last_error())
        self.addCleanup(self.close_handle)

    def close_handle(self):
        if self.handle is not None:
            handle, self.handle = self.handle, None
            if not self.kernel.CloseHandle(handle):
                raise ctypes.WinError(ctypes.get_last_error())

    def test_transient_real_windows_handle_retries_then_saves(self):
        errors, original = [], atomic_file.os.replace
        def replace(source, destination):
            try:
                return original(source, destination)
            except OSError as error:
                errors.append(error)
                raise
        def release(_delay):
            self.close_handle()
        with patch.object(atomic_file.os, "replace", side_effect=replace), \
             patch.object(atomic_file, "sleep", side_effect=release) as sleep:
            save_messages(self.new, self.path)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].winerror, 5)
        self.assertEqual(sleep.call_count, 1)
        self.assertEqual(load_messages(self.path), self.new)

    def test_persistent_real_windows_handle_fails_closed_and_preserves_history(self):
        before = self.path.read_bytes()
        errors, original = [], atomic_file.os.replace
        def replace(source, destination):
            try:
                return original(source, destination)
            except OSError as error:
                errors.append(error)
                raise
        with patch.object(atomic_file.os, "replace", side_effect=replace), \
             patch.object(atomic_file, "sleep") as sleep:
            with self.assertRaises(PermissionError) as raised:
                save_messages(self.new, self.path)
        self.assertEqual(len(errors), len(atomic_file.RETRY_DELAYS) + 1)
        self.assertIs(raised.exception, errors[-1])
        self.assertEqual(raised.exception.winerror, 5)
        self.assertEqual(sleep.call_count, len(atomic_file.RETRY_DELAYS))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.root.glob(".messages.json.*.tmp")), [])
