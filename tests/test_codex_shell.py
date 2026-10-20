from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from creatoros.integrations import codex_shell


@unittest.skipUnless(os.name == "nt", "Windows-specific PATH discovery")
class CodexShellTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.probe_patch = patch.object(codex_shell, "_launches", return_value=True)
        self.probe = self.probe_patch.start()
        self.addCleanup(self.probe_patch.stop)

    def executable(self, relative: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return path

    def test_prefers_native_program_files_and_removes_store_alias_only(self):
        safe = self.executable("programs/PowerShell/7/pwsh.exe")
        alias = self.executable("user/WindowsApps/pwsh.exe")
        runtime = self.executable("WindowsApps/OpenAI.CodexPrimaryRuntime/native/pwsh.exe")
        env = {"ProgramFiles": str(self.root / "programs"),
               "Path": f"{alias.parent};{runtime.parent};D:\\bin", "SECRET": "not-returned"}
        original = env.copy()
        actual = codex_shell.codex_subprocess_env(env)
        self.assertEqual(actual, {"Path": f"{safe.parent};{runtime.parent};D:\\bin"})
        self.assertEqual(env, original)

    def test_bundled_portable_precedes_store(self):
        portable = self.executable("user/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe")
        alias = self.executable("WindowsApps/pwsh.exe")
        actual = codex_shell.codex_subprocess_env({"USERPROFILE": str(self.root / "user"),
                                                 "PATH": str(alias.parent)})
        self.assertEqual(actual, {"PATH": str(portable.parent)})

    def test_path_portable_discovery(self):
        safe = self.executable("portable/pwsh.exe")
        self.assertEqual(codex_shell.codex_subprocess_env({"PATH": str(safe.parent)}),
                         {"PATH": str(safe.parent)})

    def test_explicit_windows_powershell_cannot_be_shadowed_by_pwsh(self):
        native = self.executable("system/powershell.exe")
        shadow = self.executable("portable/pwsh.exe")
        actual = codex_shell.codex_subprocess_env({
            "CREATOROS_POWERSHELL_EXECUTABLE": str(native),
            "PATH": f"{shadow.parent};D:\\bin",
        })
        self.assertEqual(actual, {"PATH": f"{native.parent};D:\\bin"})

    def test_broken_pwsh_falls_back_to_system_and_is_removed(self):
        broken = self.executable("broken/pwsh.exe")
        native = self.executable("windows/System32/WindowsPowerShell/v1.0/powershell.exe")
        self.probe.side_effect = lambda path: path == native
        actual = codex_shell.codex_subprocess_env({
            "SystemRoot": str(self.root / "windows"), "PATH": str(broken.parent)})
        self.assertEqual(actual, {"PATH": str(native.parent)})

    def test_explicit_missing_store_or_wrong_executable_fail_closed(self):
        for value in ("missing/pwsh.exe", "WindowsApps/pwsh.exe", "portable/cmd.exe"):
            path = self.executable(value) if not value.startswith("missing") else self.root / value
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                codex_shell.codex_subprocess_env({"CREATOROS_POWERSHELL_EXECUTABLE": str(path)})

    def test_explicit_probe_failure_does_not_silently_fallback(self):
        safe = self.executable("portable/pwsh.exe")
        self.probe.return_value = False
        with self.assertRaises(RuntimeError):
            codex_shell.codex_subprocess_env({"CREATOROS_POWERSHELL_EXECUTABLE": str(safe)})

    def test_alias_resolving_into_store_is_rejected(self):
        alias = self.executable("alias/pwsh.exe")
        with patch.object(Path, "resolve", return_value=Path(
                r"C:\Program Files\WindowsApps\Microsoft.PowerShell_7\pwsh.exe")):
            self.assertFalse(codex_shell._usable_executable(alias))

    def test_path_case_duplicates_updated_together(self):
        safe = self.executable("portable/pwsh.exe")
        actual = codex_shell.codex_subprocess_env({
            "CREATOROS_POWERSHELL_EXECUTABLE": str(safe), "Path": "D:\\preferred", "PATH": "D:\\stale"})
        self.assertEqual(actual, {"Path": f"{safe.parent};D:\\preferred",
                                  "PATH": f"{safe.parent};D:\\preferred"})

    def test_no_shell_available_fails(self):
        with patch.object(codex_shell, "_usable_executable", return_value=False):
            with self.assertRaises(RuntimeError):
                codex_shell.codex_subprocess_env({})

    def test_real_local_shell_launch_does_not_mutate_parent_environment(self):
        self.probe_patch.stop()
        before = dict(os.environ)
        actual = codex_shell.codex_subprocess_env()
        self.assertEqual(dict(os.environ), before)
        self.assertTrue(actual)
        first = Path(next(iter(actual.values())).split(";")[0])
        safe = first / "pwsh.exe"
        if not safe.is_file():
            safe = first / "powershell.exe"
        self.assertTrue(codex_shell._launches(safe))
        self.assertFalse(codex_shell._store_powershell(safe))


class ShellClassifierTests(unittest.TestCase):
    def test_store_paths_and_valid_runtime_are_distinct(self):
        for path in (r"C:\WindowsApps\Microsoft.PowerShell_7\pwsh.exe",
                     r"C:\Users\a\WindowsApps\pwsh.exe",
                     r"c:/WINDOWSAPPS/Microsoft.PowerShellPreview/pwsh.exe"):
            self.assertTrue(codex_shell._store_powershell(path))
        self.assertFalse(codex_shell._store_powershell(
            r"C:\WindowsApps\OpenAI.CodexPrimaryRuntime\native\powershell\pwsh.exe"))

    def test_posix_returns_no_env_override(self):
        with patch.object(codex_shell.os, "name", "posix"):
            self.assertIsNone(codex_shell.codex_subprocess_env({"PATH": "/bin"}))

    def test_launch_probe_is_bounded_and_quiet(self):
        with patch.object(codex_shell.subprocess, "run",
                          side_effect=subprocess.TimeoutExpired("pwsh", 5)) as run:
            self.assertFalse(codex_shell._launches(Path("pwsh.exe")))
        self.assertEqual(run.call_args.kwargs["timeout"], 5)
        self.assertEqual(run.call_args.kwargs["stdout"], subprocess.DEVNULL)


if __name__ == "__main__":
    unittest.main()
