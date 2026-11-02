"""Public all-suite plan must classify every Playwright spec and exclude live work."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parent.parent
WEB = REPO / "web"


def plan(*args, env=None):
    return subprocess.run(["node", "e2e-all.mjs", "--plan", *args], cwd=WEB, env=env,
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)


class AllBrowserSuitePlanTest(unittest.TestCase):
    def test_a_skipped_or_flaky_group_is_not_reported_as_full_success(self):
        script = '''
import { classifyGroup } from './e2e-results.mjs';
const clean = { exitCode: 0, expected: 2, unexpected: 0, skipped: 0, flaky: 0,
  missing_specs: [], unexpected_specs: [], errors: [] };
const result = [classifyGroup(clean), classifyGroup({...clean, skipped: 1}),
  classifyGroup({...clean, flaky: 1}), classifyGroup({...clean, expected: 0}),
  classifyGroup({...clean, unexpected: 1}), classifyGroup({...clean, missing_specs: ['x']})];
if (JSON.stringify(result) !== JSON.stringify(['passed', 'incomplete', 'flaky', 'incomplete', 'failed', 'failed'])) process.exit(1);
'''
        result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=WEB,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_plan_accounts_for_every_spec_and_labels_live_as_not_run(self):
        env = os.environ.copy()
        env.update(CREATOROS_LIVE_REFERENCE_ROOT="must-not-be-read", AGENT_BASELINE="1")
        result = plan(env=env)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        value = json.loads(result.stdout)
        selected = [name for group in value["groups"] if group.get("directory", "e2e") == "e2e" for name in group["specs"]]
        live = value["excluded_live_specs"]
        actual = sorted(path.name for path in (WEB / "e2e").glob("*.spec.ts"))
        self.assertEqual(sorted(selected + live), actual)
        self.assertEqual(len(selected + live), len(set(selected + live)))
        self.assertEqual(live, ["live-topic-references.spec.ts"])
        self.assertEqual(sorted(value["spec_digests"]), actual)
        self.assertTrue(all(len(digest) == 64 for digest in value["spec_digests"].values()))
        groups = {group["name"]: group for group in value["groups"]}
        self.assertEqual(groups["agent-plan"]["specs"], ["agent-plan.spec.ts"])
        self.assertEqual(sorted(groups["staged"]["specs"]), ["production-tasks.spec.ts", "staged-production.spec.ts"])
        self.assertEqual(groups["media"]["directory"], "media-tests")
        media = sorted(path.name for path in (WEB / "media-tests").glob("*.spec.ts"))
        self.assertEqual(sorted(groups["media"]["specs"]), media)
        self.assertEqual(sorted(value["additional_spec_digests"]), [f"media-tests/{name}" for name in media])
        self.assertNotIn("must-not-be-read", result.stdout.decode())

    def test_an_unclassified_spec_refuses_to_run_even_in_plan(self):
        with tempfile.TemporaryDirectory(prefix="creatoros-e2e-manifest-") as temporary:
            for path in (WEB / "e2e").glob("*.spec.ts"):
                (Path(temporary) / path.name).touch()
            (Path(temporary) / "new-unreviewed.spec.ts").touch()
            result = plan("--spec-dir", temporary)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("new-unreviewed.spec.ts", result.stderr.decode(errors="replace"))

    def test_an_unclassified_media_spec_refuses_to_run(self):
        with tempfile.TemporaryDirectory(prefix="creatoros-media-manifest-") as temporary:
            for path in (WEB / "media-tests").glob("*.spec.ts"):
                (Path(temporary) / path.name).touch()
            (Path(temporary) / "new-media.spec.ts").touch()
            result = plan("--media-spec-dir", temporary)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("new-media.spec.ts", result.stderr.decode(errors="replace"))

    def test_media_prepare_only_creates_new_controlled_fixture_without_browser(self):
        with tempfile.TemporaryDirectory(prefix="creatoros-all-media-proof-") as temporary:
            env = os.environ.copy()
            env.update(TEMP=temporary, TMP=temporary, TMPDIR=temporary, CREATOROS_PYTHON=os.sys.executable,
                       CREATOROS_E2E_PORT="8765", MEDIA_SOURCE_DB="must-not-be-read",
                       MEDIA_OUTPUT_ROOT="must-not-be-read", MEDIA_TEST_ROOT="must-not-be-used")
            result = subprocess.run(["node", "e2e-all.mjs", "--group", "media", "--prepare-only"],
                                    cwd=WEB, env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
            self.assertEqual(result.returncode, 0, result.stdout.decode("utf-8", errors="replace"))
            summaries = list(Path(temporary).glob("creatoros-e2e-all-*/summary.json"))
            self.assertEqual(len(summaries), 1)
            summary = json.loads(summaries[0].read_text(encoding="utf-8"))
            media = summary["groups"]["media"]
            self.assertEqual(summary["status"], "prepared_not_run")
            self.assertEqual(media["status"], "prepared")
            self.assertTrue(summary["prepare_only"])
            self.assertEqual(media["expected"], 0)
            self.assertEqual(media["tests_not_run"], media["specs"])
            self.assertNotEqual(media["port"], 8765)
            self.assertEqual(summary["groups"]["general"]["status"], "not_run")
            source = media["fixture"]
            workspace = Path(source["workspace"]).resolve(strict=True)
            self.assertEqual(workspace.parent, (REPO / "tmp").resolve())
            self.assertTrue(workspace.name.startswith("media-e2e-all-"))
            try:
                self.assertEqual(source["kind"], "deterministic-local")
                self.assertEqual(Path(source["source_db"]), workspace / "source" / "review.db")
                self.assertEqual(Path(source["output_root"]), workspace / "source" / "outputs")
                self.assertTrue(Path(source["source_db"]).is_file())
                self.assertEqual(len(list(Path(source["output_root"]).rglob("*.png"))), 4)
                self.assertTrue(all(source["checks"].values()))
                self.assertFalse(Path(source["server_root"]).exists())
                self.assertFalse(Path(source["test_output"]).exists())
                self.assertNotIn("must-not-be", json.dumps(summary))
            finally:
                shutil.rmtree(workspace)

    def test_controlled_environment_discards_live_and_external_configuration(self):
        script = """
import { controlledEnvironment } from './e2e-safety.mjs';
const before = { AGENT_BASELINE: '1', CREATOROS_LIVE_REFERENCE_ROOT: 'danger',
  CREATOROS_INTERNAL_E2E_ROOT: 'foreign', DEEPSEEK_API_KEY: 'secret',
  OTHER_SERVICE_TOKEN: 'other-secret',
  CREATOROS_CODEX_EXECUTABLE: 'real-codex', CREATOROS_XHS_MCP_URL: 'http://127.0.0.1:18060/mcp',
  CREATOROS_MEDIA_DOWNLOADER: 'real-downloader', CREATOROS_PRODUCTION_CONCURRENCY: '99',
  MEDIA_SOURCE_DB: 'foreign-data', MEDIA_INTERNAL_CONFIG_ROOT: 'foreign-run',
  CREATOROS_FFMPEG: 'test-ffmpeg', CREATOROS_PYTHON: 'test-python' };
console.log(JSON.stringify(controlledEnvironment(before, '/isolated', '/isolated/dist')));
"""
        result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=WEB,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        value = json.loads(result.stdout)
        for name in ("AGENT_BASELINE", "CREATOROS_LIVE_REFERENCE_ROOT", "CREATOROS_INTERNAL_E2E_ROOT"):
            self.assertNotIn(name, value)
        self.assertNotIn("MEDIA_SOURCE_DB", value)
        self.assertNotIn("MEDIA_INTERNAL_CONFIG_ROOT", value)
        self.assertEqual(value["DEEPSEEK_API_KEY"], "")
        self.assertNotIn("OTHER_SERVICE_TOKEN", value)
        self.assertEqual(value["PYTHON_DOTENV_DISABLED"], "1")
        self.assertNotEqual(value["CREATOROS_CODEX_EXECUTABLE"], "real-codex")
        self.assertTrue(value["CREATOROS_POWERSHELL_EXECUTABLE"].endswith("no-powershell.exe"))
        self.assertNotEqual(value["CREATOROS_XHS_MCP_URL"], "http://127.0.0.1:18060/mcp")
        self.assertEqual(value["CREATOROS_PYTHON"], "test-python")
        self.assertEqual(value["CREATOROS_FFMPEG"], "test-ffmpeg")
        self.assertTrue(value["CREATOROS_MEDIA_DOWNLOADER"].endswith("no-downloader.exe"))
        self.assertNotIn("CREATOROS_PRODUCTION_CONCURRENCY", value)

    def test_playwright_report_does_not_copy_inherited_secrets(self):
        with tempfile.TemporaryDirectory(prefix="creatoros-report-proof-") as temporary:
            report = Path(temporary) / "report.json"
            env = os.environ.copy()
            env.update(TEMP=temporary, TMP=temporary, TMPDIR=temporary, CREATOROS_E2E_PORT="18992",
                       PLAYWRIGHT_JSON_OUTPUT_FILE=str(report), CUSTOM_SECRET_TOKEN="do-not-write-this-canary")
            result = subprocess.run(["node", "node_modules/@playwright/test/cli.js", "test", "--config",
                                     "playwright.config.ts", "--list", "--reporter=json", "e2e/home-page.spec.ts"],
                                    cwd=WEB, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout.decode("utf-8", errors="replace"))
            content = report.read_text(encoding="utf-8")
            self.assertFalse("do-not-write-this-canary" in content,
                             "JSON report copied an inherited secret")
            server_env = json.loads(content)["config"]["webServer"]["env"]
            self.assertEqual(server_env["PYTHON_DOTENV_DISABLED"], "1")
            self.assertTrue(server_env["CREATOROS_CODEX_EXECUTABLE"].endswith("no-codex.exe"))

    def test_compact_group_executes_and_reports_scope_and_results(self):
        with tempfile.TemporaryDirectory(prefix="creatoros-e2e-all-proof-") as temporary:
            env = os.environ.copy()
            env.update(TEMP=temporary, TMP=temporary, TMPDIR=temporary,
                       CREATOROS_PYTHON=os.sys.executable, CREATOROS_LIVE_REFERENCE_ROOT="must-not-be-read",
                       AGENT_BASELINE="1", CREATOROS_E2E_PORT="8765")
            result = subprocess.run(["node", "e2e-all.mjs", "--group", "compact"], cwd=WEB, env=env,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    timeout=240)
            self.assertEqual(result.returncode, 0, result.stdout.decode("utf-8", errors="replace"))
            summaries = list(Path(temporary).glob("creatoros-e2e-all-*/summary.json"))
            self.assertEqual(len(summaries), 1)
            summary = json.loads(summaries[0].read_text(encoding="utf-8"))
            self.assertEqual(summary["groups"]["compact"]["status"], "passed")
            self.assertGreaterEqual(summary["groups"]["compact"]["expected"], 5)
            self.assertEqual(summary["groups"]["compact"]["missing_specs"], [])
            self.assertEqual(summary["groups"]["compact"]["unexpected_specs"], [])
            self.assertEqual(summary["groups"]["general"]["status"], "not_run")
            self.assertEqual(summary["excluded_live_specs"], ["live-topic-references.spec.ts"])

    def test_live_entry_rejects_production_port_and_lists_only_explicit_probe(self):
        temporary_parent = REPO / "tmp"
        temporary_parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="live-config-proof-", dir=temporary_parent) as temporary:
            root = Path(temporary)
            references = [root / "one.png", root / "two.png"]
            for reference in references:
                reference.write_bytes(b"fixture")
            setup = {"url": "http://127.0.0.1:8765", "series_id": "fixture",
                     "references": [str(reference) for reference in references]}
            (root / "setup.json").write_text(json.dumps(setup), encoding="utf-8")
            env = os.environ.copy()
            env["CREATOROS_LIVE_REFERENCE_ROOT"] = str(root)
            command = ["node", "node_modules/@playwright/test/cli.js", "test", "--config",
                       "playwright.live-reference.config.ts", "--list"]
            rejected = subprocess.run(command, cwd=WEB, env=env, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("8765", rejected.stdout.decode("utf-8", errors="replace"))
            setup["url"] = "http://127.0.0.1:18931"
            (root / "setup.json").write_text(json.dumps(setup), encoding="utf-8")
            accepted = subprocess.run(command, cwd=WEB, env=env, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
            self.assertEqual(accepted.returncode, 0, accepted.stdout.decode("utf-8", errors="replace"))
            self.assertIn("Total: 2 tests in 1 file", accepted.stdout.decode("utf-8", errors="replace"))


if __name__ == "__main__":
    unittest.main()
