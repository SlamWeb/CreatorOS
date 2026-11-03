"""Delegated SDK process isolation using only synthetic canary credentials."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
from time import monotonic
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from creatoros.integrations import codex_shell


class CodexSecretOverridesTests(unittest.TestCase):
    def test_target_and_existing_casing_are_blank_without_copying_other_values(self):
        source = {
            "DEEPSEEK_API_KEY": "fake-upper",
            "deepseek_api_key": "fake-lower",
            "DeepSeek_Api_Key": "fake-mixed",
            "OPENAI_API_KEY": "fake-codex-auth",
            "PATH": "fake-path",
            "HTTP_PROXY": "fake-proxy",
        }
        original = source.copy()
        self.assertEqual(codex_shell.codex_secret_overrides(source), {
            "DEEPSEEK_API_KEY": "", "deepseek_api_key": "", "DeepSeek_Api_Key": "",
        })
        self.assertEqual(source, original)

    def test_canonical_name_is_blank_even_if_not_present_yet(self):
        self.assertEqual(codex_shell.codex_secret_overrides({}), {"DEEPSEEK_API_KEY": ""})

    def test_posix_merge_blanks_each_existing_casing_and_preserves_other_keys(self):
        parent = {"DEEPSEEK_API_KEY": "fake-upper", "deepseek_api_key": "fake-lower",
                  "PATH": "/usr/bin", "OPENAI_API_KEY": "fake-auth", "HTTP_PROXY": "fake-proxy"}
        with patch.object(codex_shell.os, "name", "posix"):
            overrides = codex_shell.codex_subprocess_env(parent)
        child = {**parent, **overrides}
        self.assertEqual(child["DEEPSEEK_API_KEY"], "")
        self.assertEqual(child["deepseek_api_key"], "")
        self.assertEqual(child["PATH"], "/usr/bin")
        self.assertEqual(child["OPENAI_API_KEY"], "fake-auth")
        self.assertEqual(child["HTTP_PROXY"], "fake-proxy")
        self.assertEqual(parent["DEEPSEEK_API_KEY"], "fake-upper")

    def test_shell_launch_probe_also_blanks_key_and_preserves_other_environment(self):
        source = {"DEEPSEEK_API_KEY": "fake-deepseek", "DeepSeek_Api_Key": "fake-mixed",
                  "OPENAI_API_KEY": "fake-codex-auth", "HTTP_PROXY": "fake-proxy", "PATH": "fake-path"}
        with patch.object(codex_shell.os, "environ", source), \
             patch.object(codex_shell.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
            self.assertTrue(codex_shell._launches(Path("pwsh.exe")))
        child = run.call_args.kwargs["env"]
        self.assertEqual(child["DEEPSEEK_API_KEY"], "")
        self.assertEqual(child["DeepSeek_Api_Key"], "")
        self.assertEqual(child["OPENAI_API_KEY"], "fake-codex-auth")
        self.assertEqual(child["HTTP_PROXY"], "fake-proxy")
        self.assertEqual(child["PATH"], "fake-path")

    def test_production_client_supplies_the_sanitized_overrides_to_sdk(self):
        from creatoros.integrations.codex import _production_client

        configs = []
        class FakeClient:
            def __init__(self, config):
                configs.append(config)
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return None

        async def open_client():
            async with _production_client(monotonic() + 10) as client:
                self.assertIsInstance(client, FakeClient)

        with patch("creatoros.integrations.codex_shell.codex_subprocess_env", return_value={"DEEPSEEK_API_KEY": ""}), \
             patch("openai_codex.AsyncCodex", FakeClient):
            asyncio.run(open_client())
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0].env, {"DEEPSEEK_API_KEY": ""})

    def test_recovery_history_read_supplies_same_secret_override_to_sdk(self):
        from creatoros.integrations.native_recovery import verify_history

        configs = []
        class FakeClient:
            def __init__(self, config):
                configs.append(config)
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return None

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            response_file = directory / "production_response.txt"
            response_file.write_text("saved reply", encoding="utf-8")
            checkpoint = SimpleNamespace(thread_id="controlled-thread", repair_attempted=False)
            item = SimpleNamespace(type="agentMessage", text="saved reply")
            turn = SimpleNamespace(id="controlled-turn", status="completed", items=[item])
            thread = SimpleNamespace(id="controlled-thread", cwd=str(directory / "work"), turns=[turn])
            class FakeThread:
                def __init__(self, client, thread_id):
                    self.thread_id = thread_id
                async def read(self, *, include_turns):
                    self_assert = include_turns and self.thread_id == "controlled-thread"
                    if not self_assert:
                        raise AssertionError("wrong thread lookup")
                    return SimpleNamespace(thread=thread)
            with patch("creatoros.integrations.native_recovery.load_checkpoint", return_value=checkpoint), \
                 patch("creatoros.integrations.native_recovery.safe_file", return_value=response_file), \
                 patch("creatoros.integrations.codex_shell.codex_secret_overrides", return_value={"DEEPSEEK_API_KEY": ""}), \
                 patch("openai_codex.AsyncCodex", FakeClient), patch("openai_codex.AsyncThread", FakeThread):
                result = asyncio.run(verify_history(directory))
        self.assertEqual(result["verification"], "sdk_thread_read_no_inference")
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0].env, {"DEEPSEEK_API_KEY": ""})

    def test_sdk_really_launches_child_without_canary_but_preserves_auth_and_proxy(self):
        # The outer test process receives an explicit, non-secret environment.
        # The SDK then launches an actual child; neither process calls a model.
        repo = Path(__file__).resolve().parents[1]
        child = textwrap.dedent('''
            import json, os, sys
            report = {
                "secret_empty": os.environ.get("DEEPSEEK_API_KEY") == "",
                "lowercase_empty": os.environ.get("deepseek_api_key", "") == "",
                "auth_retained": os.environ.get("OPENAI_API_KEY") == "fake-codex-auth",
                "codex_api_key_retained": os.environ.get("CODEX_API_KEY") == "fake-codex-key",
                "codex_home_retained": os.environ.get("CODEX_HOME") == "fake-codex-home",
                "proxy_retained": os.environ.get("HTTP_PROXY") == "http://127.0.0.1:19999",
                "https_proxy_retained": os.environ.get("HTTPS_PROXY") == "http://127.0.0.1:19998",
                "path_retained": "creatoros-path-canary" in os.environ.get("PATH", ""),
                "sdk_path_added": "creatoros-sdk-bin-canary" in os.environ.get("PATH", ""),
            }
            with open(sys.argv[1], "w", encoding="utf-8") as output:
                json.dump(report, output)
        ''')
        outer = textwrap.dedent('''
            import json, os, sys
            from pathlib import Path
            from unittest.mock import patch
            from creatoros.integrations import codex_shell
            import openai_codex.client as sdk

            real_popen = sdk.subprocess.Popen

            recovery = getattr(codex_shell, "codex_secret_overrides", lambda: None)
            for name, prepare in (("production", codex_shell.codex_subprocess_env), ("recovery", recovery)):
                # A synthetic shell selection keeps this test independent of installed
                # PowerShell while exercising the SDK's real Popen env merge.
                with patch("creatoros.integrations.codex_shell._usable_executable", return_value=True), \\
                     patch("creatoros.integrations.codex_shell._launches", return_value=True):
                    overrides = prepare()
                path = os.path.join(sys.argv[1], name + ".json")
                config = sdk.CodexConfig(env=overrides)
                client = sdk.CodexClient(config)
                def start_canary(_args, **kwargs):
                    return real_popen((sys.executable, "-c", sys.argv[2], path), **kwargs)
                try:
                    # Keep the SDK's normal startup/merge/PATH path, replacing
                    # only the executable with a harmless real child process.
                    with patch.object(sdk, "_resolve_codex_bin", return_value=Path(sys.executable)), \\
                         patch.object(sdk, "_installed_codex_path_dirs", return_value=(Path(sys.argv[1]) / "creatoros-sdk-bin-canary",)), \\
                         patch.object(sdk.subprocess, "Popen", side_effect=start_canary):
                        client.start()
                    code = client._proc.wait(timeout=15)
                    if code:
                        raise RuntimeError("controlled child exited unsuccessfully")
                finally:
                    client.close()
                with open(path, encoding="utf-8") as source:
                    report = json.load(source)
                if not all(report.values()):
                    raise AssertionError(name + ": " + str(report))
            print("controlled SDK child environment passed")
        ''')
        with tempfile.TemporaryDirectory() as temporary:
            fake_path = str(Path(temporary) / "creatoros-path-canary")
            env = {
                "PATH": fake_path,
                "DEEPSEEK_API_KEY": "fake-deepseek-key",
                "deepseek_api_key": "fake-lowercase-key",
                "OPENAI_API_KEY": "fake-codex-auth",
                "CODEX_API_KEY": "fake-codex-key",
                "CODEX_HOME": "fake-codex-home",
                "HTTP_PROXY": "http://127.0.0.1:19999",
                "HTTPS_PROXY": "http://127.0.0.1:19998",
                "TMP": temporary,
                "TEMP": temporary,
                "CREATOROS_POWERSHELL_EXECUTABLE": str(Path(temporary) / "pwsh.exe"),
            }
            if os.name == "nt":
                env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", r"C:\Windows")
            result = subprocess.run([sys.executable, "-c", outer, temporary, child], cwd=repo,
                                    env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("controlled SDK child environment passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
