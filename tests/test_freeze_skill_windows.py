"""Windows directory publication with real, non-delete-sharing file handles."""
import ctypes
import os
import shutil
import threading
import time
import unittest
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from creatoros.integrations import producer_skills


class LockedCopy:
    """Hold a real handle on a copied file until the test's retry barrier."""

    def __init__(self):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateFileW.argtypes = (
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        )
        self.kernel.CreateFileW.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.handle = None
        self.staging = None
        self.probe_error = None

    def copytree(self, source, target, *args, **kwargs):
        result = self.original_copytree(source, target, *args, **kwargs)
        if Path(target).name != "copy":
            return result
        self.staging = Path(target)
        # Permit reading and writing, but intentionally deny FILE_SHARE_DELETE.
        self.handle = self.kernel.CreateFileW(
            str(self.staging / "SKILL.md"), 0x80, 0x1 | 0x2, None, 3, 0, None,
        )
        if self.handle == wintypes.HANDLE(-1).value:
            self.handle = None
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            self.staging.rename(self.staging.parent / "probe")
        except OSError as error:
            self.probe_error = error
        else:
            raise AssertionError("The real Windows handle did not block the directory rename")
        return result

    def close(self):
        if self.handle is not None:
            handle, self.handle = self.handle, None
            if not self.kernel.CloseHandle(handle):
                raise ctypes.WinError(ctypes.get_last_error())

    @contextmanager
    def install(self):
        self.original_copytree = shutil.copytree
        with patch.object(producer_skills.shutil, "copytree", self.copytree):
            try:
                yield self
            finally:
                self.close()


@unittest.skipUnless(os.name == "nt", "Requires real Windows handle sharing semantics")
class FreezeSkillWindowsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "SKILL.md").write_bytes(b"original skill\n")
        (self.source / "assets").mkdir()
        (self.source / "assets" / "sample.bin").write_bytes(b"\x00\x01\xff")
        self.target = self.root / "snapshots" / "selected"
        self.expected = producer_skills._digest(self.source)
        self.lock = LockedCopy()
        self.waits = []

    def assert_probe_blocked(self):
        self.assertIsInstance(self.lock.probe_error, PermissionError)
        self.assertEqual(self.lock.probe_error.winerror, 5)

    def release_on_wait(self, seconds):
        self.waits.append(seconds)
        self.lock.close()

    def test_locked_child_then_released_publishes_identical_snapshot(self):
        with self.lock.install(), patch.object(time, "sleep", self.release_on_wait):
            producer_skills.freeze_skill(self.source, self.target, self.expected)
        self.assert_probe_blocked()
        self.assertTrue(self.waits, "The lock must have blocked the real publication attempt")
        self.assertEqual(producer_skills._digest(self.source), self.expected)
        self.assertEqual(producer_skills._digest(self.target), self.expected)
        self.assertEqual((self.target / "SKILL.md").read_bytes(), b"original skill\n")
        self.assertEqual((self.target / "assets" / "sample.bin").read_bytes(), b"\x00\x01\xff")
        self.assertEqual(list(self.target.parent.glob(".skill-copy-*")), [])

    def test_real_handle_released_by_another_thread_during_publication(self):
        denied, released = threading.Event(), threading.Event()
        actual_rename = Path.rename
        observed_errors = []

        def release_handle():
            if denied.wait(3):
                self.lock.close()
                released.set()

        def watch_rename(path, target):
            try:
                return actual_rename(path, target)
            except OSError as error:
                if Path(target) == self.target and getattr(error, "winerror", None) == 5:
                    observed_errors.append(error.winerror)
                    denied.set()
                    if not released.wait(3):
                        raise AssertionError("Windows handle was not released")
                raise

        worker = threading.Thread(target=release_handle)
        with self.lock.install(), patch.object(Path, "rename", watch_rename):
            worker.start()
            try:
                producer_skills.freeze_skill(self.source, self.target, self.expected)
            finally:
                denied.set()
                worker.join(3)
                self.assertFalse(worker.is_alive())
        self.assert_probe_blocked()
        self.assertEqual(observed_errors, [5])
        self.assertEqual(producer_skills._digest(self.target), self.expected)

    def test_persistent_lock_raises_original_windows_error_without_publishing(self):
        def observe_wait(seconds):
            self.waits.append(seconds)
            self.assertLessEqual(len(self.waits), 5, "Retry must be bounded")

        with self.lock.install(), patch.object(time, "sleep", observe_wait):
            with self.assertRaises(PermissionError) as caught:
                producer_skills.freeze_skill(self.source, self.target, self.expected)
        self.assert_probe_blocked()
        self.assertEqual(caught.exception.winerror, 5)
        self.assertEqual(Path(caught.exception.filename).name, "copy")
        self.assertEqual(Path(caught.exception.filename2), self.target)
        self.assertTrue(self.waits)
        self.assertFalse(self.target.exists())
        self.assertEqual(producer_skills._digest(self.source), self.expected)
        self.assertEqual(list(self.target.parent.glob(".skill-copy-*")), [])

    def test_different_target_arriving_during_wait_is_not_overwritten(self):
        def create_conflicting_target(seconds):
            self.release_on_wait(seconds)
            self.target.mkdir()
            (self.target / "SKILL.md").write_bytes(b"existing different snapshot\n")

        with self.lock.install(), patch.object(time, "sleep", create_conflicting_target):
            with self.assertRaisesRegex(ValueError, "已有 Skill 快照与本次输入不一致"):
                producer_skills.freeze_skill(self.source, self.target, self.expected)
        self.assert_probe_blocked()
        self.assertEqual(len(self.waits), 1)
        self.assertEqual((self.target / "SKILL.md").read_bytes(), b"existing different snapshot\n")
        self.assertNotEqual(producer_skills._digest(self.target), self.expected)
        self.assertEqual(producer_skills._digest(self.source), self.expected)

    def test_matching_target_arriving_during_wait_is_reused(self):
        existing_file_id = []

        def create_matching_target(seconds):
            self.release_on_wait(seconds)
            self.lock.original_copytree(self.source, self.target)
            existing_file_id.append((self.target / "SKILL.md").stat().st_ino)

        with self.lock.install(), patch.object(time, "sleep", create_matching_target):
            producer_skills.freeze_skill(self.source, self.target, self.expected)
        self.assert_probe_blocked()
        self.assertEqual(len(self.waits), 1)
        self.assertEqual(producer_skills._digest(self.target), self.expected)
        self.assertEqual((self.target / "SKILL.md").stat().st_ino, existing_file_id[0])

    def test_staging_changed_during_wait_is_rejected(self):
        def alter_staging(seconds):
            self.release_on_wait(seconds)
            (self.lock.staging / "SKILL.md").write_bytes(b"changed during retry\n")

        with self.lock.install(), patch.object(time, "sleep", alter_staging):
            with self.assertRaisesRegex(ValueError, "复制期间 Skill 已变化"):
                producer_skills.freeze_skill(self.source, self.target, self.expected)
        self.assert_probe_blocked()
        self.assertEqual(len(self.waits), 1)
        self.assertFalse(self.target.exists())
        self.assertEqual(producer_skills._digest(self.source), self.expected)

    def test_source_changed_during_wait_is_rejected(self):
        def alter_source(seconds):
            self.release_on_wait(seconds)
            (self.source / "SKILL.md").write_bytes(b"changed source\n")

        with self.lock.install(), patch.object(time, "sleep", alter_source):
            with self.assertRaisesRegex(ValueError, "复制期间 Skill 已变化"):
                producer_skills.freeze_skill(self.source, self.target, self.expected)
        self.assert_probe_blocked()
        self.assertEqual(len(self.waits), 1)
        self.assertFalse(self.target.exists())

    def test_other_windows_errors_are_not_retried(self):
        failure = OSError(13, "simulated sharing violation", "copy", 32, str(self.target))
        self.assertEqual(failure.winerror, 32)
        with patch.object(Path, "rename", side_effect=failure) as rename, \
             patch.object(time, "sleep") as sleep:
            with self.assertRaises(OSError) as caught:
                producer_skills.freeze_skill(self.source, self.target, self.expected)
        self.assertIs(caught.exception, failure)
        self.assertEqual(rename.call_count, 1)
        sleep.assert_not_called()
        self.assertFalse(self.target.exists())

    def test_persistent_access_denial_reraises_its_original_exception(self):
        failure = OSError(13, "simulated permanent access denial", "copy", 5, str(self.target))
        self.assertEqual(failure.winerror, 5)
        with patch.object(Path, "rename", side_effect=failure) as rename, \
             patch.object(time, "sleep") as sleep:
            with self.assertRaises(OSError) as caught:
                producer_skills.freeze_skill(self.source, self.target, self.expected)
        self.assertIs(caught.exception, failure)
        self.assertGreater(rename.call_count, 1)
        self.assertLessEqual(rename.call_count, 4)
        self.assertLessEqual(sum(call.args[0] for call in sleep.call_args_list), 0.351)
        self.assertFalse(self.target.exists())

    def test_windows_error_code_is_not_retried_on_other_platforms(self):
        failure = OSError(13, "simulated access denial", "copy", 5, str(self.target))
        self.assertEqual(failure.winerror, 5)
        with patch.object(producer_skills, "os", SimpleNamespace(name="posix")), \
             patch.object(Path, "rename", side_effect=failure) as rename, \
             patch.object(time, "sleep") as sleep:
            with self.assertRaises(OSError) as caught:
                producer_skills.freeze_skill(self.source, self.target, self.expected)
        self.assertIs(caught.exception, failure)
        self.assertEqual(rename.call_count, 1)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
