"""The browser fixture obeys real task-v1 validation without invoking models."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from uuid import uuid4

import httpx

from tests.test_isolated_dev import REPO, free_ports, wait_ready


class ControlledE2EProducerTest(unittest.TestCase):
    def test_batch_image_tasks_freeze_and_validate_independently(self):
        port, = free_ports(1)
        with tempfile.TemporaryDirectory(prefix="creatoros-fixture-contract-") as temporary:
            root = Path(temporary)
            env = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith("CREATOROS_")
                   and not any(secret in key.upper() for secret in
                               ("TOKEN", "SECRET", "PASSWORD", "COOKIE", "CREDENTIAL", "API_KEY"))}
            env.update(CREATOROS_E2E_ROOT=str(root / "data"), CREATOROS_CODEX_EXECUTABLE=str(root / "no-codex"),
                       CREATOROS_POWERSHELL_EXECUTABLE=str(root / "no-powershell"),
                       CODEX_HOME=str(root / "codex-home"), PYTHON_DOTENV_DISABLED="1",
                       CREATOROS_XHS_MCP_URL="http://127.0.0.1:1/mcp", TEMP=temporary, TMP=temporary, TMPDIR=temporary)
            with (root / "server.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen([sys.executable, "-m", "tests.studio_e2e_server", "--port", str(port)],
                                           cwd=REPO, env=env, stdin=subprocess.DEVNULL,
                                           stdout=log, stderr=subprocess.STDOUT)
                try:
                    wait_ready(process, f"http://127.0.0.1:{port}/api/health")
                    with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=15) as client:
                        def post(path, payload):
                            response = client.post(path, json=payload)
                            self.assertTrue(response.is_success, response.text)
                            return response.json()

                        account = post("/api/creators", {"display_name": "isolated contract"})
                        series = post(f"/api/creators/{account['id']}/series", {"name": "fixture contract"})
                        skill = next(item for item in client.get("/api/producer-skills?include_versions=true").json()["items"]
                                     if item["name"] == "knowledge-to-carousel")
                        library = client.get(f"/api/creators/{account['id']}/skills").json()
                        if not any(item["id"] == skill["id"] for item in library["items"]):
                            post(f"/api/creators/{account['id']}/skills", {"skill_id": skill["id"], "request_id": uuid4().hex})
                        pool_url = f"/api/series/{series['id']}/skills"
                        pool = client.get(pool_url).json()
                        response = client.put(pool_url, json={"expected_version": pool["version"], "skill_ids": [skill["id"]],
                            "defaults": {"image": [{"skill_id": skill["id"], "role": "primary"}], "video": []}})
                        self.assertTrue(response.is_success, response.text)
                        queued = post(f"/api/series/{series['id']}/queue", {"request_id": uuid4().hex,
                            "topics": [{"title": "first fixture"}, {"title": "second fixture"}]})
                        result = post("/api/runs/batch", {"topic_ids": queued["topic_ids"]})
                        self.assertEqual(result["accepted_count"], 2, result)
                        run_ids = [item["run_id"] for item in result["items"]]
                        deadline = time.monotonic() + 35
                        while True:
                            runs = [client.get(f"/api/runs/{run_id}").json() for run_id in run_ids]
                            if all(run["status"] in ("awaiting_approval", "failed") for run in runs):
                                break
                            self.assertLess(time.monotonic(), deadline, runs)
                            time.sleep(.25)
                        self.assertEqual([run["status"] for run in runs], ["awaiting_approval"] * 2, runs)
                        for run in runs:
                            self.assertEqual(run["input_snapshot"]["production_protocol"], "task-v1")
                            self.assertEqual(run["card_count"], 3)
                            self.assertEqual(client.get(run["cover_url"]).status_code, 200)
                        specs = list((root / "data" / "outputs").rglob("execution_spec.json"))
                        self.assertEqual(len(specs), 2)
                        for path in specs:
                            spec = json.loads(path.read_text(encoding="utf-8"))
                            self.assertEqual(spec["kind"], "image")
                            self.assertTrue(Path(spec["skills"][0]["path"]).is_relative_to(path.parent))
                            self.assertTrue((path.parent / "native_checkpoint.json").is_file())

                        # The fixture must preserve real receipt validation: altering one frozen
                        # Skill invalidates only its own result, not its sibling's.
                        victim = next(path for path in specs if run_ids[0] in path.parts)
                        frozen = json.loads(victim.read_text(encoding="utf-8"))
                        skill_file = Path(frozen["skills"][0]["path"]) / "SKILL.md"
                        with skill_file.open("a", encoding="utf-8") as output:
                            output.write("\n[controlled evidence corruption]\n")
                        damaged = client.get(f"/api/runs/{run_ids[0]}").json()
                        self.assertFalse(damaged["revisions"][0]["artifact_available"])
                        sibling = client.get(f"/api/runs/{run_ids[1]}").json()
                        self.assertTrue(sibling["revisions"][0]["artifact_available"])
                        self.assertEqual(client.get(sibling["cover_url"]).status_code, 200)

                        split_topic = post(f"/api/series/{series['id']}/queue", {"request_id": uuid4().hex,
                            "topics": [{"title": "split fixture"}]})["topic_ids"][0]
                        split = post(f"/api/topics/{split_topic}/production-tasks", {"request_id": uuid4().hex,
                            "tasks": [{"title": "split A", "skill_ids": [skill["id"]]},
                                      {"title": "split B", "skill_ids": [skill["id"]]}]})
                        deadline = time.monotonic() + 35
                        while True:
                            split_runs = [client.get(f"/api/runs/{item['run_id']}").json() for item in split["items"]]
                            if all(run["status"] in ("awaiting_approval", "failed") for run in split_runs):
                                break
                            self.assertLess(time.monotonic(), deadline, split_runs)
                            time.sleep(.25)
                        self.assertEqual([run["status"] for run in split_runs], ["awaiting_approval"] * 2, split_runs)
                        self.assertTrue(all(run["input_snapshot"]["production_protocol"] == "task-v1" for run in split_runs))

                        legacy_series = post("/api/series", {"creator_id": account["id"], "name": "legacy fixture",
                            "skill_name": "knowledge-to-carousel", "request_id": uuid4().hex})["series"]
                        legacy_topic = post(f"/api/series/{legacy_series['id']}/queue", {"request_id": uuid4().hex,
                            "topics": [{"title": "legacy fixture"}]})["topic_ids"][0]
                        legacy = post("/api/runs", {"topic_id": legacy_topic})
                        self.assertEqual(legacy["input_snapshot"]["production_protocol"], "legacy")
                        post(f"/api/runs/{legacy['id']}/execute", {"expected_version": legacy["version"]})
                        deadline = time.monotonic() + 35
                        while True:
                            legacy = client.get(f"/api/runs/{legacy['id']}").json()
                            if legacy["status"] in ("awaiting_approval", "failed"):
                                break
                            self.assertLess(time.monotonic(), deadline, legacy)
                            time.sleep(.25)
                        self.assertEqual(legacy["status"], "awaiting_approval", legacy)
                        legacy_dir = next((root / "data" / "outputs").rglob(f"{legacy['id']}/revision-001/attempt-001"))
                        self.assertFalse((legacy_dir / "execution_spec.json").exists())
                        self.assertFalse((legacy_dir / "native_checkpoint.json").exists())
                finally:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=15)


if __name__ == "__main__":
    unittest.main()
