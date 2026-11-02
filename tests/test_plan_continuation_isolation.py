"""Real HTTP/chat/plan scheduling with a deterministic local provider, never DeepSeek."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from uuid import uuid4

import httpx

from tests.live_plan_continuation_server import FixtureAdmission, safe_steps
from tests.plan_continuation_controlled import initial_steps
from tests.test_isolated_dev import REPO, free_ports, wait_ready


class PlanContinuationIsolationTests(unittest.TestCase):
    def setUp(self):
        (REPO / "tmp").mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="plan-continuation-proof-", dir=REPO / "tmp")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root, self.dist = self.base / "server", self.base / "dist"
        self.dist.mkdir()
        (self.dist / "index.html").write_text("private plan build", encoding="utf-8")
        self.port, = free_ports(1)
        self.env = os.environ.copy()
        self.env.update(DEEPSEEK_API_KEY="", OPENAI_API_KEY="", CODEX_API_KEY="", PYTHON_DOTENV_DISABLED="1")

    def command(self, scenario="review", *extra):
        return [sys.executable, "-m", "tests.live_plan_continuation_server", "--root", str(self.root),
                "--dist", str(self.dist), "--port", str(self.port), "--scenario", scenario, *extra]

    def run_command(self, command, env=None):
        return subprocess.run(command, cwd=REPO, env=env or self.env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90)

    def test_safe_plan_whitelist_and_atomic_unknown_outcome_claim(self):
        self.assertTrue(safe_steps(initial_steps("review")))
        self.assertTrue(safe_steps(initial_steps("recovery")))
        for tool, args in (("start_content_run", {"action": "tasks"}), ("manage_topics", {"action": "create"}),
                           ("manage_material", {"action": "import_link"}), ("manage_media_collection", {"action": "export"}),
                           ("prepare_publication", {}), ("manage_skill_library", {"action": "add"})):
            self.assertFalse(safe_steps([{"tool": tool, "arguments": args}]))
        self.root.mkdir()
        gate = FixtureAdmission(self.root, "fixture")
        token = gate.reserve()["token"]
        self.assertTrue(gate.authorized(token))
        self.assertFalse(gate.authorized("other"))
        with self.assertRaises(FileExistsError):
            gate.reserve()
        def claim():
            try:
                gate.claim("initial-turn", {"request_id": "same", "state": "unknown"})
                return "admitted"
            except FileExistsError:
                return "denied"
        with ThreadPoolExecutor(2) as workers:
            self.assertEqual(sorted(workers.map(lambda _: claim(), range(2))), ["admitted", "denied"])
        before = (self.root / "initial-turn.json").read_bytes()
        with self.assertRaises(FileExistsError):
            FixtureAdmission(self.root, "fixture").claim("initial-turn", {"request_id": "different"})
        self.assertEqual((self.root / "initial-turn.json").read_bytes(), before)

    def test_server_rejects_production_paths_ports_old_root_and_implicit_live(self):
        old = self.base / "old"
        old.mkdir()
        marker = old / "preserve.txt"
        marker.write_text("unchanged", encoding="utf-8")
        for key, value in (("--port", "8765"), ("--port", "0"), ("--root", str(old)),
                           ("--root", str(REPO / "data" / "must-not-create")), ("--dist", str(REPO / "web" / "dist"))):
            command = self.command()
            command[command.index(key) + 1] = value
            self.assertNotEqual(self.run_command(command).returncode, 0)
            self.assertFalse(self.root.exists())
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", self.port))
            listener.listen()
            self.assertNotEqual(self.run_command(self.command()).returncode, 0)
        self.assertNotEqual(self.run_command(self.command("review", "--live-models")).returncode, 0)
        self.assertFalse(self.root.exists())
        self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")

    def test_controlled_checkpoint_adjustment_and_two_automatic_continuations_over_http(self):
        self.exercise("review")

    def test_controlled_known_failure_skip_adjustment_and_continuations_over_http(self):
        self.exercise("recovery")

    def exercise(self, scenario):
        self.env.update(CODEX_HOME=str(self.base / "foreign-codex"),
                        DATABASE_URL=f"sqlite:///{(self.base / 'foreign.db').as_posix()}")
        with (self.base / "server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(self.command(scenario, "--controlled"), cwd=REPO, env=self.env,
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            try:
                url = f"http://127.0.0.1:{self.port}"
                wait_ready(process, url + "/test/plan/fixture")
                with httpx.Client(base_url=url, trust_env=False, timeout=15) as client:
                    fixture = client.get("/test/plan/fixture").json()
                    self.assertEqual(fixture["mode"], "controlled")
                    self.assertFalse(fixture["background_services_started"])
                    self.assertEqual(client.get("/").text, "private plan build")
                    output = self.base / "new-evidence"
                    driver = ["node", f"web/e2e/live-plan-{scenario}.mjs", str(self.root), str(output)]
                    checked = self.run_command([*driver, "--check"])
                    self.assertEqual(checked.returncode, 0, checked.stdout.decode(errors="replace"))
                    self.assertFalse(output.exists())
                    self.assertFalse((self.root / "reservation.json").exists())
                    self.assertNotEqual(self.run_command([*driver, "--live"]).returncode, 0)
                    self.assertFalse(output.exists())
                    old = self.base / "old-evidence"
                    old.mkdir()
                    marker = old / "preserve.txt"
                    marker.write_text("unchanged", encoding="utf-8")
                    self.assertNotEqual(self.run_command([*driver[:-1], str(old), "--check"]).returncode, 0)
                    self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")
                    session_path = f"/api/agent/sessions/{fixture['session_id']}"
                    reservation = client.post("/test/plan/reserve", json={})
                    self.assertEqual(reservation.status_code, 200)
                    self.assertEqual(client.post("/test/plan/reserve", json={}).status_code, 409)
                    headers = {"x-creatoros-plan-token": reservation.json()["token"]}
                    for endpoint in ("/api/runs", "/api/topics/plan-topic/production-tasks", "/api/agent/sessions",
                                     "/api/skill-extractions", "/api/creators/x/materials", "/api/runs/run/approve",
                                     "/api/publications", "/api/producer-skills/install"):
                        self.assertEqual(client.post(endpoint, headers=headers, json={}).status_code, 403)
                    forbidden_plan = client.post(session_path + "/plans", headers=headers,
                        json={"request_id": "forbidden", "title": "must reject", "steps": [{"id": "bad", "title": "bad",
                            "tool": "start_content_run", "arguments": {"action": "start", "topic_ids": ["plan-topic"]}}]})
                    self.assertEqual(forbidden_plan.status_code, 403)
                    self.assertFalse((self.root / "plan-submission.json").exists())
                    turn = {"request_id": str(uuid4()), "expected_version": 0, "text": "controlled plan continuation test"}
                    accepted = client.post(session_path + "/turns", headers=headers, json=turn)
                    self.assertEqual(accepted.status_code, 202, accepted.text)
                    self.assertEqual(client.post(session_path + "/turns", headers=headers, json=turn).status_code, 409)
                    deadline = time.monotonic() + 40
                    injected = False
                    while time.monotonic() < deadline:
                        doc = client.get(session_path).json()
                        if scenario == "recovery" and doc["status"] == "idle" and not injected:
                            response = client.post(session_path + "/plans", headers=headers, json={
                                "request_id": "known-failure", "title": "受控确定失败", "steps": initial_steps("recovery")})
                            self.assertEqual(response.status_code, 200, response.text)
                            injected = True
                        listing = client.get(session_path + "/plans").json()["items"]
                        if listing:
                            plan = client.get(session_path + "/plans/" + listing[0]["id"]).json()
                            if plan["status"] == "completed" and plan["continuation_state"] == "done" and doc["status"] == "idle":
                                break
                        time.sleep(.1)
                    else:
                        self.fail(f"Plan did not settle: {doc!r} {listing!r}")
                    self.assertEqual(len(listing), 1)
                    receipt = json.loads((self.root / "plan-submission.json").read_text(encoding="utf-8"))
                    self.assertEqual(receipt["plan_id"], plan["id"])
                    self.assertIsNone(doc["error"])
                    self.assertEqual([step["tool"] for step in plan["steps"]],
                                     ["request_review" if scenario == "review" else "get_producer_skill", "manage_material"])
                    self.assertEqual(plan["steps"][1]["status"], "succeeded")
                    if scenario == "review":
                        self.assertEqual(plan["steps"][0]["status"], "succeeded")
                    else:
                        self.assertEqual(plan["steps"][0]["status"], "skipped")
                        self.assertTrue(plan["steps"][0]["error"])
                    final = client.get("/test/plan/fixture").json()
                    self.assertEqual(len(final["continuations"]), 2, final)
                    self.assertEqual(len({item["event_id"] for item in final["continuations"]}), 2)
                    self.assertTrue(all(item["status"] == "idle" for item in final["continuations"]))
                    self.assertEqual(final["forbidden_calls"], [])
                    self.assertEqual(client.post(session_path + "/plans", headers=headers,
                        json={"request_id": "different", "title": "repeat", "steps": initial_steps(scenario)}).status_code, 409)
                    self.assertNotEqual(self.run_command([*driver, "--check"]).returncode, 0)
                    before = client.get(session_path).json()
                    for _ in range(3):
                        self.assertEqual(client.get(session_path).json(), before)
                    self.assertEqual(len([item for item in before["entries"] if item["kind"] == "user"]), 1)
                with closing(sqlite3.connect(self.root / "studio.db")) as database:
                    for table in ("content_runs", "production_tasks", "publication_requests"):
                        self.assertEqual(database.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)
                self.assertFalse((self.base / "foreign.db").exists())
                self.assertFalse((self.base / "foreign-codex").exists())
            except Exception:
                log.flush()
                print((self.base / "server.log").read_text(encoding="utf-8", errors="replace"))
                raise
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=20)


if __name__ == "__main__":
    unittest.main()
