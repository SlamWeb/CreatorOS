"""Real local link boundaries for video recovery; no producer or model."""
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from creatoros.delivery.video import VIDEO_CHECKPOINT, VideoCheckpoint
from creatoros.delivery.video_receipt import VideoRecoveryEvidenceConflict, _recover_work


class VideoRecoveryLinkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="video-recovery-links-")
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.outside = root / "outside"
        self.outside.mkdir()
        self.sentinel = self.outside / "user-file.txt"
        self.sentinel.write_bytes(b"independent real file; never modify")
        self.previous = root / "revision" / "attempt-001"
        self.work = self.previous / "work"
        (self.work / "nested").mkdir(parents=True)
        (self.work / "nested" / "normal.txt").write_bytes(b"ordinary work")
        (self.previous / VIDEO_CHECKPOINT).write_text(
            VideoCheckpoint(input_digest="same-input", thread_id="original-thread").model_dump_json(),
            encoding="utf-8")
        self.current = self.previous.parent / "attempt-002"
        (self.current / "work").mkdir(parents=True)

    def assert_rejected_without_copy(self):
        before = self.sentinel.read_bytes()
        with self.assertRaises(VideoRecoveryEvidenceConflict):
            _recover_work(self.current, "same-input")
        self.assertEqual(self.sentinel.read_bytes(), before)
        self.assertEqual(list((self.current / "work").iterdir()), [])

    def test_plain_nested_files_recover_without_changing_source(self):
        checkpoint = _recover_work(self.current, "same-input")
        self.assertEqual(checkpoint.thread_id, "original-thread")
        self.assertEqual((self.current / "work/nested/normal.txt").read_bytes(), b"ordinary work")
        self.assertEqual((self.work / "nested/normal.txt").read_bytes(), b"ordinary work")
        self.assertEqual(self.sentinel.read_bytes(), b"independent real file; never modify")

    def make_symlink(self, link, target, *, directory=False):
        try:
            os.symlink(str(target), str(link), target_is_directory=directory)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"OS cannot create a symbolic link: {error}")
        self.addCleanup(link.unlink)

    def test_file_symlink_to_external_file_is_rejected(self):
        self.make_symlink(self.work / "link.txt", self.sentinel)
        self.assert_rejected_without_copy()

    def test_directory_symlink_to_external_files_is_rejected(self):
        self.make_symlink(self.work / "link", self.outside, directory=True)
        self.assert_rejected_without_copy()

    def test_symlink_appearing_after_precheck_is_not_followed_or_accepted(self):
        from creatoros.delivery import video_receipt
        original_copytree = video_receipt.shutil.copytree
        source_link = self.work / "late-link"
        self.make_symlink(source_link, self.outside, directory=True)
        source_link.unlink()
        target_link = self.current / "work" / "late-link"
        self.addCleanup(lambda: target_link.unlink(missing_ok=True))

        def inject_link(source, destination, *args, **kwargs):
            if Path(source) == self.work:
                os.symlink(str(self.outside), str(source_link), target_is_directory=True)
            return original_copytree(source, destination, *args, **kwargs)

        with patch.object(video_receipt.shutil, "copytree", side_effect=inject_link):
            with self.assertRaises(VideoRecoveryEvidenceConflict):
                _recover_work(self.current, "same-input")
        self.assertTrue(target_link.is_symlink())
        self.assertEqual(self.sentinel.read_bytes(), b"independent real file; never modify")

    def test_destination_directory_symlink_is_rejected_before_copy(self):
        self.make_symlink(self.current / "work" / "nested", self.outside, directory=True)
        before = self.sentinel.read_bytes()
        with self.assertRaises(VideoRecoveryEvidenceConflict):
            _recover_work(self.current, "same-input")
        self.assertEqual(self.sentinel.read_bytes(), before)
        self.assertFalse((self.outside / "normal.txt").exists())

    @unittest.skipUnless(os.name == "nt", "Windows junction test")
    def test_windows_junction_to_external_directory_is_rejected(self):
        link = self.work / "junction"
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(self.outside)],
                                capture_output=True, text=True)
        if result.returncode:
            self.skipTest("OS cannot create a Windows junction")
        self.addCleanup(os.rmdir, link)
        self.assert_rejected_without_copy()

    @unittest.skipUnless(os.name == "nt", "Windows junction test")
    def test_windows_junction_to_internal_directory_is_also_rejected(self):
        link = self.work / "junction"
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(self.work / "nested")],
                                capture_output=True, text=True)
        if result.returncode:
            self.skipTest("OS cannot create a Windows junction")
        self.addCleanup(os.rmdir, link)
        self.assert_rejected_without_copy()


if __name__ == "__main__":
    unittest.main()
