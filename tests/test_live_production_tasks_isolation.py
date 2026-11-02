"""Zero-model checks for the opt-in production-task fixture and browser config."""
from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

import httpx

from tests.test_isolated_dev import REPO, free_ports, wait_ready
from tests.live_production_tasks_server import fixture_write_allowed


class ProductionTasksIsolationTests(unittest.TestCase):
    def test_live_write_gate_preserves_agent_submission_but_denies_other_business_writes(self):
        headers = {"x-creatoros-origin": "agent", "x-creatoros-agent-session": "isolated-session"}
        task = "/api/topics/tasks-topic/production-tasks"
        self.assertTrue(fixture_write_allowed("POST", task, True, headers))
        self.assertTrue(fixture_write_allowed("POST", "/api/agent/sessions", True, {}))
        self.assertTrue(fixture_write_allowed("POST", "/api/agent/sessions/session/turns", True, {}))
        self.assertFalse(fixture_write_allowed("POST", task, False, headers))
        self.assertFalse(fixture_write_allowed("POST", task, True, {}))
        for path in ("/api/topics/other/production-tasks", task + "/task/resume", "/api/runs",
                     "/api/runs/run/approve", "/api/publications", "/api/producer-skills/install"):
            self.assertFalse(fixture_write_allowed("POST", path, True, headers))
        self.assertFalse(fixture_write_allowed("DELETE", "/api/agent/sessions", True, headers))

    def setUp(self):
        (REPO / "tmp").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="tasks-isolation-proof-", dir=REPO / "tmp")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root, self.dist = self.base / "server", self.base / "dist"
        self.dist.mkdir()
        (self.dist / "index.html").write_text("isolated production preparation", encoding="utf-8")
        self.port, = free_ports(1)
        self.env = os.environ.copy()
        self.env.update(DEEPSEEK_API_KEY="", OPENAI_API_KEY="", CODEX_API_KEY="", PYTHON_DOTENV_DISABLED="1")
        for key in list(self.env):
            if key.startswith("CREATOROS_TASKS_"):
                self.env.pop(key)

    def command(self, *extra):
        return [sys.executable, "-m", "tests.live_production_tasks_server", "--root", str(self.root),
                "--dist", str(self.dist), "--port", str(self.port), *extra]

    def test_rejects_production_paths_ports_existing_roots_and_live_without_credentials(self):
        (self.base / "old").mkdir()
        marker = self.base / "old" / "preserve.txt"
        marker.write_text("unchanged", encoding="utf-8")
        cases = [("--port", "8765"), ("--port", "0"), ("--root", str(self.base / "old")),
                 ("--root", str(REPO / "data" / "must-not-create")),
                 ("--dist", str(REPO / "web" / "dist")), ("--dist", str(self.base / "missing"))]
        for name, value in cases:
            command = self.command()
            command[command.index(name) + 1] = value
            result = subprocess.run(command, cwd=REPO, env=self.env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20)
            self.assertNotEqual(result.returncode, 0, result.stdout.decode(errors="replace"))
            self.assertFalse(self.root.exists())
        denied = subprocess.run(self.command("--live-models"), cwd=REPO, env=self.env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20)
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn(b"DEEPSEEK_API_KEY", denied.stdout)
        self.assertFalse(self.root.exists())
        self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")

    def test_prepare_only_real_http_never_starts_workers_or_accepts_writes(self):
        # Even inherited credentials must not turn default preparation into live mode.
        self.env.update(DEEPSEEK_API_KEY="must-not-be-used", OPENAI_API_KEY="must-not-be-used",
                        CODEX_HOME=str(self.base / "foreign-codex-home"),
                        DATABASE_URL=f"sqlite:///{(self.base / 'foreign.db').as_posix()}")
        with (self.base / "server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(self.command(), cwd=REPO, env=self.env, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT)
            try:
                url = f"http://127.0.0.1:{self.port}"
                wait_ready(process, url + "/test/production-tasks-fixture")
                with httpx.Client(base_url=url, trust_env=False, timeout=10) as client:
                    fixture = client.get("/test/production-tasks-fixture").json()
                    self.assertEqual(fixture["mode"], "prepare-only")
                    self.assertFalse(fixture["workers_started"])
                    self.assertEqual(Path(fixture["root"]), self.root)
                    self.assertEqual(Path(fixture["temp"]), self.root / "temp")
                    self.assertEqual(Path(fixture["codex_home"]), self.root / "codex-home")
                    self.assertEqual(client.get("/").text, "isolated production preparation")
                    self.assertEqual(client.get("/api/topics/tasks-topic/production-tasks").json()["items"], [])
                    for endpoint in ("/api/agent/sessions", "/api/topics/tasks-topic/production-tasks",
                                     "/api/creators", "/api/runs/anything/recover"):
                        self.assertEqual(client.post(endpoint, json={}).status_code, 403)
                with closing(sqlite3.connect(self.root / "studio.db")) as database:
                    for table in ("production_tasks", "content_runs", "content_revisions", "content_attempts"):
                        self.assertEqual(database.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)
                env = {**self.env, "CREATOROS_TASKS_LIVE_ROOT": str(self.root),
                       "CREATOROS_TASKS_LIVE_URL": url, "CREATOROS_TASKS_LIVE_MODELS": "0",
                       "CREATOROS_TASKS_LIVE_OUTPUT": str(self.base / "worker-results")}
                checked = subprocess.run(["node", "node_modules/@playwright/test/cli.js", "test", "--config",
                                          "playwright.production-tasks-live.config.ts"], cwd=REPO / "web", env=env,
                                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
                self.assertEqual(checked.returncode, 0, checked.stdout.decode(errors="replace"))
                self.assertIn(b"1 passed", checked.stdout)
                self.assertFalse((self.base / "foreign.db").exists())
                self.assertFalse((self.base / "foreign-codex-home").exists())
                self.assertEqual(json.loads((self.root / "setup.json").read_text(encoding="utf-8")),
                                 {key: value for key, value in fixture.items() if key != "workers_started"})
            except Exception:
                log.flush()
                print((self.base / "server.log").read_text(encoding="utf-8", errors="replace"))
                raise
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=15)

    def test_browser_listing_requires_explicit_instance_and_unused_results(self):
        # Synthetic manifest only: --list parses config and specs, never starts a browser/model/server.
        self.root.mkdir()
        (self.root / "temp").mkdir()
        setup = dict(root=str(self.root), temp=str(self.root / "temp"), fixture_id="local-config-proof",
                     base_url=f"http://127.0.0.1:{self.port}", mode="live-models")
        (self.root / "setup.json").write_text(json.dumps(setup), encoding="utf-8")
        report = self.base / "listing.json"
        env = {**self.env, "CREATOROS_TASKS_LIVE_ROOT": str(self.root),
               "CREATOROS_TASKS_LIVE_URL": setup["base_url"], "CREATOROS_TASKS_LIVE_MODELS": "1",
               "CREATOROS_TASKS_LIVE_OUTPUT": str(self.base / "fresh-results"),
               "PLAYWRIGHT_JSON_OUTPUT_FILE": str(report)}
        command = ["node", "node_modules/@playwright/test/cli.js", "test", "--config",
                   "playwright.production-tasks-live.config.ts", "--list", "--reporter=json"]
        result = subprocess.run(command, cwd=REPO / "web", env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout.decode(errors="replace"))
        self.assertEqual(Path(json.loads(report.read_text(encoding="utf-8"))["config"]["projects"][0]["outputDir"]),
                         self.base / "fresh-results")
        self.assertFalse((self.root / "live-submission.json").exists())
        self.assertFalse((self.base / "fresh-results").exists())
        existing = self.base / "existing-results"
        existing.mkdir()
        marker = existing / "preserve.txt"
        marker.write_text("unchanged", encoding="utf-8")
        for key, value in (("CREATOROS_TASKS_LIVE_ROOT", None), ("CREATOROS_TASKS_LIVE_MODELS", None),
                           ("CREATOROS_TASKS_LIVE_URL", "http://127.0.0.1:8765"),
                           ("CREATOROS_TASKS_LIVE_URL", "http://example.com:8790"),
                           ("CREATOROS_TASKS_LIVE_OUTPUT", str(existing)),
                           ("CREATOROS_TASKS_LIVE_OUTPUT", str(REPO / "outputs" / "must-not-create")),
                           ("CREATOROS_TASKS_EXISTING_SESSION", "old-session")):
            variant = env.copy()
            if value is None:
                variant.pop(key)
            else:
                variant[key] = value
            rejected = subprocess.run(command, cwd=REPO / "web", env=variant, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
            self.assertNotEqual(rejected.returncode, 0, rejected.stdout.decode(errors="replace"))
        self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")
        repeated = subprocess.run(command, cwd=REPO / "web", env=env, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        self.assertNotEqual(repeated.returncode, 0)
        self.assertIn(b"EEXIST", repeated.stdout)


if __name__ == "__main__":
    unittest.main()
