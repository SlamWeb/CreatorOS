"""No model calls: fresh fixture, write/unknown-result guards and config checks."""
from __future__ import annotations

import hashlib
import base64
import json
import os
from pathlib import Path
import socket
import sqlite3
from contextlib import closing
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx

from tests.skill_edit_batch_live import REPO, allowed_write, exclusive_json, make_app
from tests.test_isolated_dev import free_ports, wait_ready


class SkillEditBatchIsolationTests(unittest.TestCase):
    def setUp(self):
        (REPO / "tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="skill-batch-proof-", dir=REPO / "tmp")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root, self.dist = self.base / "server", self.base / "dist"
        self.dist.mkdir()
        (self.dist / "index.html").write_text("isolated Skill batch build", encoding="utf-8")
        self.port, = free_ports(1)
        self.env = {key: value for key, value in os.environ.items()
                    if not key.upper().startswith("CREATOROS_") and not key.upper().startswith("MEDIA_")}
        self.env.update(PYTHON_DOTENV_DISABLED="1", DEEPSEEK_API_KEY="", OPENAI_API_KEY="", CODEX_API_KEY="",
                        DATABASE_URL=f"sqlite:///{(self.base / 'foreign.db').as_posix()}")

    def command(self, *args):
        return [sys.executable, "-m", "tests.skill_edit_batch_live", "--root", str(self.root),
                "--dist", str(self.dist), "--port", str(self.port), *args]

    def run_command(self, command, env=None):
        return subprocess.run(command, cwd=REPO, env=env or self.env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90)

    def test_write_gate_has_no_production_publication_or_extraction_escape(self):
        sid = "a" * 36
        allowed = [("POST", "/api/agent/sessions"), ("POST", f"/api/agent/sessions/{sid}/turns"),
                   ("POST", f"/api/agent/sessions/{sid}/attachments"),
                   ("POST", f"/api/agent/sessions/{sid}/image-actions/skill-asset"),
                   ("POST", f"/api/agent/sessions/{sid}/image-actions/read"),
                   ("PUT", "/api/producer-skills/example/files/content")]
        for method, route in allowed:
            self.assertTrue(allowed_write(method, route, live=True, claimed=True))
            self.assertFalse(allowed_write(method, route, live=False, claimed=True))
            self.assertFalse(allowed_write(method, route, live=True, claimed=False))
        for method, route in [("POST", "/api/runs"), ("POST", "/api/publications"),
                              ("PATCH", "/api/series/anything"), ("DELETE", "/api/producer-skills/example"),
                              ("POST", f"/api/agent/sessions/{sid}/image-actions/extract"),
                              ("POST", f"/api/agent/sessions/{sid}/image-actions/import")]:
            self.assertFalse(allowed_write(method, route, live=True, claimed=True))

    def test_exclusive_marker_is_not_replaced_after_unknown_result(self):
        marker = self.base / "execution-started.json"
        exclusive_json(marker, {"result": "unknown"})
        original = marker.read_bytes()
        with self.assertRaises(FileExistsError):
            exclusive_json(marker, {"result": "retry"})
        self.assertEqual(marker.read_bytes(), original)

    def test_paths_ports_existing_roots_and_missing_live_opt_in_fail_before_writes(self):
        old = self.base / "old"
        old.mkdir()
        (old / "preserve").write_text("unchanged")
        cases = [("--root", str(old)), ("--root", str(REPO / "data" / "must-not-create")),
                 ("--port", "8765"), ("--dist", str(REPO / "web" / "dist")),
                 ("--dist", str(self.base / "missing")), ("--fixture-id", "not-a-fixture")]
        for option, value in cases:
            command = self.command("--prepare-only")
            if option in command:
                command[command.index(option) + 1] = value
            else:
                command += [option, value]
            result = self.run_command(command)
            self.assertNotEqual(result.returncode, 0, result.stdout.decode(errors="replace"))
            self.assertFalse(self.root.exists())
        env = {**self.env, "DEEPSEEK_API_KEY": "must-not-be-used"}
        result = self.run_command(self.command("--serve", "--live-models"), env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"CREATOROS_SKILL_EDIT_LIVE", result.stdout)
        self.assertFalse(self.root.exists())
        no_key = {**self.env, "CREATOROS_SKILL_EDIT_LIVE": "1", "DEEPSEEK_API_KEY": ""}
        result = self.run_command(self.command("--serve", "--live-models"), no_key)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.root.exists())
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", self.port))
            result = self.run_command(self.command("--prepare-only"))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.root.exists())
        self.assertEqual((old / "preserve").read_text(), "unchanged")

    def test_preparation_makes_new_unique_local_assets_without_foreign_db_or_model(self):
        seen = []
        for index in range(2):
            root = self.base / f"prepared-{index}"
            command = self.command("--prepare-only")
            command[command.index("--root") + 1] = str(root)
            env = {**self.env, "DEEPSEEK_API_KEY": "must-not-be-used", "CREATOROS_SKILL_EDIT_LIVE": "1"}
            result = self.run_command(command, env)
            self.assertEqual(result.returncode, 0, result.stdout.decode(errors="replace"))
            setup = json.loads((root / "setup.json").read_text(encoding="utf-8"))
            self.assertFalse(setup["live_models"])
            self.assertEqual(setup["old_reference_count"], 6)
            self.assertEqual(len(setup["input_pngs"]), 2)
            self.assertEqual(len(list((root / "inputs").glob("*.png"))), 8)
            self.assertEqual(len({hashlib.sha256(p.read_bytes()).hexdigest() for p in (root / "inputs").glob("*.png")}), 8)
            self.assertFalse((root / "execution-started.json").exists())
            with closing(sqlite3.connect(root / "studio.db")) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM content_runs").fetchone()[0], 0)
                creators = {row[0] for row in db.execute("SELECT id FROM creators")}
                self.assertEqual(creators, {setup["creator_a"], setup["creator_b"]})
            seen.append(setup)
        self.assertNotEqual(seen[0]["creator_a"], seen[1]["creator_a"])
        self.assertNotEqual(seen[0]["skill_ids"], seen[1]["skill_ids"])
        self.assertFalse((self.base / "foreign.db").exists())

    def test_real_http_preparation_starts_no_workers_and_rejects_all_writes(self):
        with (self.base / "server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(self.command("--serve"), cwd=REPO, env=self.env, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT)
            try:
                url = f"http://127.0.0.1:{self.port}"
                wait_ready(process, url + "/api/skill-assets-probe/evidence")
                with httpx.Client(base_url=url, trust_env=False, timeout=10) as client:
                    self.assertEqual(client.get("/").text, "isolated Skill batch build")
                    view = client.get("/api/skill-assets-probe/evidence").json()
                    self.assertFalse(view["workers_started"])
                    self.assertFalse(view["chat_started"])
                    self.assertEqual(view["forbidden_calls"], [])
                    self.assertEqual(view["runs_count"], 0)
                    before = list(view["files"])
                    for endpoint in ("/test/skill-edit-batch/claim", "/api/agent/sessions", "/api/runs", "/api/publications"):
                        self.assertEqual(client.post(endpoint, json={}).status_code, 403)
                    self.assertEqual(client.get("/api/skill-assets-probe/evidence").json()["files"], before)
                self.assertFalse((self.base / "foreign.db").exists())
            except Exception:
                log.flush()
                print((self.base / "server.log").read_text(encoding="utf-8", errors="replace"))
                raise
            finally:
                process.terminate()
                process.wait(timeout=15)

    def test_live_write_claim_and_unknown_turn_are_exclusive_without_calling_provider(self):
        from fastapi.testclient import TestClient
        self.root.mkdir()
        # This in-process gate test never sends a valid turn. Even if it did,
        # the provider is replaced with a hard failure before entering lifespan.
        with patch.dict(os.environ, {**self.env, "DEEPSEEK_API_KEY": "unused-test-key"}):
            app, db, setup = make_app(self.root, self.dist, self.port, "a" * 32, True)
            providers = []
            def no_provider():
                providers.append("unexpected")
                raise AssertionError("No model calls permitted in isolation tests")
            app.state.chat.provider_factory = no_provider
            try:
                with TestClient(app) as client:
                    self.assertEqual(client.post("/api/agent/sessions", json={"creator_id": setup["creator_a"]}).status_code, 403)
                    self.assertEqual(client.post("/test/skill-edit-batch/claim").status_code, 201)
                    self.assertEqual(client.post("/test/skill-edit-batch/claim").status_code, 409)
                    self.assertEqual(client.post("/api/agent/sessions", json={"creator_id": setup["creator_b"]}).status_code, 403)
                    session = client.post("/api/agent/sessions", json={"creator_id": setup["creator_a"]})
                    self.assertEqual(session.status_code, 201, session.text)
                    self.assertEqual(client.post("/api/agent/sessions", json={"creator_id": setup["creator_a"]}).status_code, 409)
                    sid = session.json()["id"]
                    # A 422 still consumes the marker: acceptance is uncertain
                    # whenever a request crossed the write boundary.
                    self.assertEqual(client.post(f"/api/agent/sessions/{sid}/turns", json={}).status_code, 422)
                    self.assertEqual(client.post(f"/api/agent/sessions/{sid}/turns", json={}).status_code, 409)
                    for endpoint in ("/api/runs", "/api/publications"):
                        self.assertEqual(client.post(endpoint, json={}).status_code, 403)
                self.assertEqual(providers, [])
            finally:
                db.close()

    def test_local_skill_writes_cross_the_same_gate_and_preserve_original_version(self):
        from fastapi.testclient import TestClient
        self.root.mkdir()
        with patch.dict(os.environ, {**self.env, "DEEPSEEK_API_KEY": "unused-test-key"}):
            app, db, setup = make_app(self.root, self.dist, self.port, "c" * 32, True)
            def denied_provider():
                raise AssertionError("Local writes must never call a model")
            app.state.chat.provider_factory = denied_provider
            try:
                with TestClient(app) as client:
                    before = client.get("/api/skill-assets-probe/evidence").json()
                    self.assertEqual(client.post("/test/skill-edit-batch/claim").status_code, 201)
                    sid = client.post("/api/agent/sessions", json={"creator_id": setup["creator_a"]}).json()["id"]
                    headers = {"x-creatoros-agent-session": sid, "x-creatoros-origin": "agent"}
                    original = setup["skill_ids"]["asset-live-main"]
                    original_files = client.get(f"/api/producer-skills/{original}/files").json()
                    original_text = client.get(f"/api/producer-skills/{original}/files/text", params={"path": "SKILL.md"}).json()["content"]
                    body = original_text.split("参考1：")[0] + "\n![新图1](assets/new-one.png)\n![新图2](assets/new-two.png)\n"
                    result = client.put(f"/api/producer-skills/{original}/files/content", headers=headers,
                        json={"path": "SKILL.md", "content": body, "expected_digest": original_files["digest"]})
                    self.assertEqual(result.status_code, 200, result.text)
                    candidate, current_digest = result.json()["skill_id"], result.json()["digest"]
                    attachments = []
                    for name, image in zip(("new-one.png", "new-two.png"), setup["input_pngs"]):
                        raw = Path(image["path"]).read_bytes()
                        attached = client.post(f"/api/agent/sessions/{sid}/attachments",
                            json={"name": name, "data_base64": base64.b64encode(raw).decode()})
                        self.assertEqual(attached.status_code, 201, attached.text)
                        attachments.append(attached.json()["id"])
                    changes = [("add_image", f"assets/{name}", attachment)
                               for name, attachment in zip(("new-one.png", "new-two.png"), attachments)]
                    changes += [("delete_image", f"assets/reference-{n:02d}.png", None) for n in range(1, 7)]
                    for index, (operation, path, attachment) in enumerate(changes):
                        result = client.post(f"/api/agent/sessions/{sid}/image-actions/skill-asset", headers=headers,
                            json={"skill_id": candidate, "path": path, "expected_digest": current_digest,
                                  "operation": operation, "attachment_id": attachment, "request_id": f"local-{index}"})
                        self.assertEqual(result.status_code, 200, result.text)
                        self.assertEqual(result.json()["skill_id"], candidate)
                        current_digest = result.json()["digest"]
                    files = client.get(f"/api/producer-skills/{candidate}/files").json()
                    self.assertEqual(sorted(file["path"] for file in files["files"] if file["kind"] == "image"),
                                     ["assets/new-one.png", "assets/new-two.png"])
                    self.assertEqual(client.get(f"/api/producer-skills/{original}/files").json()["digest"], original_files["digest"])
                    after = client.get("/api/skill-assets-probe/evidence").json()
                    self.assertEqual(after["series"], before["series"])
                    self.assertEqual(after["forbidden_calls"], [])
                    self.assertEqual(after["runs_count"], 0)
                    for file in before["files"]:
                        self.assertIn(file, after["files"])
                    other = setup["skill_ids"]["asset-live-other"]
                    refused = client.put(f"/api/producer-skills/{other}/files/content", headers=headers,
                        json={"path": "SKILL.md", "content": "must not write", "expected_digest": "0" * 64})
                    self.assertEqual(refused.status_code, 403)
            finally:
                db.close()

    def test_browser_config_requires_live_flag_fresh_results_and_only_lists_this_spec(self):
        plan = {"protocol": "skill-edit-batch-v1", "workspace": str(self.base), "fixture_id": "b" * 32,
                "server": str(self.root), "output": str(self.base / "browser-results"), "dist": str(self.dist),
                "url": f"http://127.0.0.1:{self.port}", "python": sys.executable}
        (self.base / "run.json").write_text(json.dumps(plan), encoding="utf-8")
        command = ["node", "node_modules/@playwright/test/cli.js", "test", "--config", "playwright.skill-edit-batch.config.ts", "--list", "--reporter=json"]
        report = self.base / "report.json"
        env = {**self.env, "CREATOROS_SKILL_EDIT_ROOT": str(self.base), "PLAYWRIGHT_JSON_OUTPUT_FILE": str(report),
               "CUSTOM_SECRET_TOKEN": "must-not-be-recorded", "DEEPSEEK_API_KEY": "key-must-not-be-recorded"}
        def check(variant):
            return subprocess.run(command, cwd=REPO / "web", env=variant, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=40)
        rejected = check(env)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn(b"CREATOROS_SKILL_EDIT_LIVE", rejected.stdout)
        self.assertFalse(self.root.exists())
        env["CREATOROS_SKILL_EDIT_LIVE"] = "1"
        plan["url"] = "http://127.0.0.1:8765"
        (self.base / "run.json").write_text(json.dumps(plan))
        self.assertNotEqual(check(env).returncode, 0)
        plan["url"] = f"http://127.0.0.1:{self.port}"
        (self.base / "run.json").write_text(json.dumps(plan))
        output = self.base / "browser-results"
        output.mkdir()
        (output / "preserve").write_text("unchanged")
        self.assertNotEqual(check(env).returncode, 0)
        self.assertEqual((output / "preserve").read_text(), "unchanged")
        (output / "preserve").unlink()
        output.rmdir()
        accepted = check(env)
        self.assertEqual(accepted.returncode, 0, accepted.stdout.decode(errors="replace"))
        value = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual([suite["file"] for suite in value["suites"]], ["skill-edit-batch.spec.ts"])
        self.assertNotIn("must-not-be-recorded", report.read_text(encoding="utf-8"))
        self.assertEqual(value["config"]["webServer"]["reuseExistingServer"], False)
        self.assertEqual(value["config"]["projects"][0]["retries"], 0)
        self.assertFalse(self.root.exists())
        self.assertNotEqual(check(env).returncode, 0, "A second invocation must not reuse evidence")
        legacy = subprocess.run(["node", "node_modules/@playwright/test/cli.js", "test", "--config",
            "playwright.media-agent.config.ts", "--list"], cwd=REPO / "web",
            env={**self.env, "CREATOROS_MEDIA_AGENT_ROOT": str(self.base), "MEDIA_AGENT_OUTPUT": str(self.base / "legacy-list-results")},
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=40)
        self.assertEqual(legacy.returncode, 0, legacy.stdout.decode(errors="replace"))
        self.assertNotIn(b"skill-edit-batch.spec.ts", legacy.stdout)


if __name__ == "__main__":
    unittest.main()
