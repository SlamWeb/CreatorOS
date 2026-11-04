"""CI runtime preparation; local files and media only, no models."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tests.ci_environment import prepare_temp, guarded_environment, verify_media


class CiEnvironmentTests(unittest.TestCase):
    def test_workspace_temp_is_canonical_and_all_aliases_are_exported(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            env_file = root / "env.txt"
            result = prepare_temp(root, "backend-1", env_file)
            self.assertEqual(result, root.resolve() / "tmp" / "ci-backend-1")
            self.assertTrue(result.is_dir())
            self.assertEqual(env_file.read_text(encoding="utf-8").splitlines(),
                             [f"{key}={result}" for key in ("TEMP", "TMP", "TMPDIR")])

    def test_temp_rejects_escape_and_linked_directory(self):
        with TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                prepare_temp(Path(folder), "../outside")
            root = Path(folder)
            (root / "tmp").mkdir()
            with patch("tests.ci_environment._is_link", return_value=True):
                with self.assertRaisesRegex(ValueError, "linked"):
                    prepare_temp(root, "backend")

    def test_missing_media_never_reports_success(self):
        with TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            with patch("tests.ci_environment.shutil.which", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "ffmpeg"):
                    verify_media(Path(folder))
                failure = json.loads((Path(folder) / "media-preflight.failure.json").read_text(encoding="utf-8"))
                self.assertEqual(failure["status"], "failed")

    def test_job_guards_have_no_keys_or_inherited_codex_home(self):
        with TemporaryDirectory() as folder:
            guards = guarded_environment(folder)
            self.assertEqual(guards["OPENAI_API_KEY"], "")
            self.assertEqual(guards["DEEPSEEK_API_KEY"], "")
            self.assertEqual(guards["CODEX_HOME"], str(Path(folder) / "codex-home"))
            self.assertEqual(guards["OPENAI_BASE_URL"], "http://127.0.0.1:1/v1")

    @unittest.skipUnless(os.name == "nt", "Windows 8.3 alias regression")
    def test_canonical_temp_fixes_real_manifest_short_name_failure(self):
        with TemporaryDirectory(prefix="creatoros-ci-long-path-regression-") as folder:
            # Create the alias only on our own temporary directory, without
            # changing any volume or system setting.
            target = Path(folder) / "creatoros-long-alias-fixture"
            target.mkdir()
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                           wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            kernel.CreateFileW.restype = wintypes.HANDLE
            kernel.SetFileShortNameW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR]
            kernel.SetFileShortNameW.restype = wintypes.BOOL
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.CreateFileW(str(target), 0x00010000, 7, None, 3, 0x02000000, None)
            if handle == wintypes.HANDLE(-1).value:
                self.skipTest(f"Cannot open private directory to set an 8.3 alias: WinError {ctypes.get_last_error()}")
            try:
                if not kernel.SetFileShortNameW(handle, "CIALIA~1"):
                    self.skipTest(f"Cannot set a private 8.3 alias: WinError {ctypes.get_last_error()}")
            finally:
                kernel.CloseHandle(handle)
            buffer = ctypes.create_unicode_buffer(32768)
            length = kernel.GetShortPathNameW(str(target), buffer, len(buffer))
            self.assertGreater(length, 0)
            short = buffer.value
            self.assertNotEqual(os.path.normcase(short), os.path.normcase(str(target.resolve())))
            self.assertTrue(os.path.samefile(short, target), "8.3 alias must identify the same directory")
            command = [sys.executable, "-m", "unittest", "tests.test_benchmark_manifest_v2.BenchmarkManifestTests.test_changed_dataset_rejects_even_when_case_ids_are_unchanged", "-q"]
            env = {**os.environ, "TEMP": short, "TMP": short, "TMPDIR": short}
            old = subprocess.run(command, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
            self.assertNotEqual(old.returncode, 0, old.stdout + old.stderr)
            self.assertIn("Frozen inputs must stay inside", old.stderr)
            canonical = str(prepare_temp(Path(short), "probe"))
            env.update({key: canonical for key in ("TEMP", "TMP", "TMPDIR")})
            fixed = subprocess.run(command, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
            self.assertEqual(fixed.returncode, 0, fixed.stdout + fixed.stderr)
