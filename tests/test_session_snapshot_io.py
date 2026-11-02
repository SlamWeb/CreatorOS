"""Chat snapshot publication on isolated files; never touches a live session."""
import ctypes
import errno
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
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
