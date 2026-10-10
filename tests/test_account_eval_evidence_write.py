"""Evidence-file fault regressions; no Provider or business execution."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
import unittest
from unittest.mock import patch

from creatoros.evaluation.run import write_json


class EvidenceWriteTests(unittest.TestCase):
    def test_bounded_windows_sharing_retry_preserves_old_json_on_failure(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "report.json"
            write_json(path, {"old": True})
            error = PermissionError("controlled Windows sharing violation")
            error.winerror = 32
            with patch.object(Path, "replace", side_effect=error) as replace, patch(
                    "creatoros.evaluation.run.sleep") as wait:
                with self.assertRaises(PermissionError):
                    write_json(path, {"new": True})
            self.assertEqual(replace.call_count, 5)
            self.assertEqual([call.args[0] for call in wait.call_args_list], [.01, .02, .04, .08])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"old": True})
            self.assertEqual(list(Path(temporary).glob("*.tmp")), [])

    def test_nonsharing_failure_is_not_retried(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "report.json"
            with patch.object(Path, "replace", side_effect=PermissionError("not a sharing error")) as replace, patch(
                    "creatoros.evaluation.run.sleep") as wait:
                with self.assertRaises(PermissionError):
                    write_json(path, {})
            self.assertEqual(replace.call_count, 1)
            wait.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "actual Windows sharing handle required")
    def test_actual_windows_reader_lock_releases_then_atomic_replace_succeeds(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "report.json"
            write_json(path, {"old": True})
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                          wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            kernel.CreateFileW.restype = wintypes.HANDLE
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle.restype = wintypes.BOOL
            # Read/write sharing allowed, delete sharing denied: the exact transient
            # replacement condition under test, not a mocked model or SDK result.
            handle = kernel.CreateFileW(str(path), 0x80000000, 1 | 2, None, 3, 0x80, None)
            if handle == ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            blocked, errors = Event(), []
            original = Path.replace
            def observed_replace(source, target):
                try:
                    return original(source, target)
                except PermissionError as error:
                    errors.append(getattr(error, "winerror", None))
                    blocked.set()
                    raise
            failures = []
            def write():
                try:
                    write_json(path, {"new": True})
                except Exception as error:
                    failures.append(error)
            thread = Thread(target=write)
            try:
                with patch.object(Path, "replace", observed_replace):
                    thread.start()
                    self.assertTrue(blocked.wait(2), "reader lock did not block replacement")
                    self.assertTrue(kernel.CloseHandle(handle))
                    handle = None
                    thread.join(2)
            finally:
                if handle is not None:
                    kernel.CloseHandle(handle)
                thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(failures, [])
            self.assertTrue(errors and set(errors) <= {5, 32, 33}, errors)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"new": True})
            self.assertEqual(list(Path(temporary).glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
