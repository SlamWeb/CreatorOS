import concurrent.futures
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from creatoros.evaluation.registry import EvalRegistry, RegistryError
from creatoros.web.eval_registry_routes import eval_registry_routes


class EvalRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "shared" / "records.sqlite"
        self.registry = EvalRegistry(self.path)
        self.round = {"id": "R1", "title": "Tool change", "code_version": "abc123"}
        self.run = {"id": "RUN1", "round_id": "R1", "suite_id": "tool-calling",
                    "suite_version": "v1", "planned_cases": ["C01", "C02"],
                    "metadata": {"model": "test", "rule_version": "v1"}}

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_read_does_not_create_database(self):
        self.assertEqual(self.registry.rounds(), {"items": []})
        self.assertFalse(self.path.exists())

    def test_formal_lifecycle_keeps_partial_and_unknown(self):
        self.registry.register_round(self.round)
        registered = self.registry.register_run(self.run)
        self.assertEqual(registered["status"], "registered")
        self.assertEqual(registered["planned_cases"], ["C01", "C02"])
        self.registry.transition("RUN1", "freezing")
        self.registry.transition("RUN1", "running")
        result = {"case_id": "C01", "status": "needs_review", "summary": "missing evidence"}
        self.registry.add_case_result("RUN1", result)
        self.registry.transition("RUN1", "unknown", "connection lost")
        run = self.registry.get_run("RUN1")
        self.assertEqual(run["status"], "unknown")
        self.assertEqual(run["results"][0]["status"], "needs_review")
        self.assertEqual(run["planned_cases"], ["C01", "C02"])
        self.assertEqual([event["status"] for event in run["events"]], ["registered", "freezing", "running", "unknown"])
        with self.assertRaises(RegistryError):
            self.registry.transition("RUN1", "running")

    def test_duplicate_idempotency_and_conflicting_payload(self):
        self.assertEqual(self.registry.register_round(self.round), self.registry.register_round(self.round))
        self.registry.register_run(self.run)
        self.registry.register_run(self.run)
        with self.assertRaises(RegistryError) as error:
            self.registry.register_run({**self.run, "planned_cases": ["C01"]})
        self.assertEqual(error.exception.status, 409)
        self.registry.transition("RUN1", "freezing")
        self.registry.transition("RUN1", "running")
        result = {"case_id": "C01", "status": "failed", "summary": "failed"}
        self.registry.add_case_result("RUN1", result)
        self.registry.add_case_result("RUN1", result)
        with self.assertRaises(RegistryError):
            self.registry.add_case_result("RUN1", {**result, "status": "passed"})
        with self.assertRaises(RegistryError):
            self.registry.add_case_result("RUN1", {**result, "case_id": "C03"})

    def test_merge_event_keeps_original_registration(self):
        self.registry.register_round({**self.round, "merge_status": "developing"})
        self.registry.set_merge_status("R1", "merged")
        round_data = self.registry.round_detail("R1")["round"]
        self.assertEqual(round_data["merge_status"], "merged")
        self.assertEqual(len(round_data["merge_events"]), 1)
        self.registry.register_round({**self.round, "merge_status": "developing"})
        self.assertEqual(self.registry.round_detail("R1")["round"]["merge_status"], "merged")
        with self.assertRaises(RegistryError):
            self.registry.set_merge_status("R1", "developing")

    def test_concurrent_worktrees_cannot_overwrite(self):
        self.registry.register_round(self.round)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            outputs = list(pool.map(lambda _: EvalRegistry(self.path).register_run(self.run), range(16)))
        self.assertEqual(len(outputs), 16)
        self.assertEqual(len(self.registry.round_detail("R1")["runs"]), 1)

    def test_round_attempt_is_per_case_and_counts_formal_registrations(self):
        self.registry.register_round(self.round)
        first = self.registry.register_run({**self.run, "planned_cases": ["C01"]})
        self.assertEqual(first["case_attempts"], {"C01": 1})
        self.registry.register_run({**self.run, "planned_cases": ["C01"]})
        self.registry.transition("RUN1", "failed", "freeze failed")
        second = self.registry.register_run({**self.run, "id": "RUN2"})
        self.assertEqual(second["case_attempts"], {"C01": 2, "C02": 1})
        self.registry.claim_execution("RUN2")
        self.registry.transition("RUN2", "running")
        self.registry.add_case_result("RUN2", {"case_id": "C01", "status": "passed"})
        self.assertEqual(self.registry.get_run("RUN2")["results"][0]["round_attempt"], 2)
        with self.assertRaises(RegistryError):
            self.registry.add_case_result("RUN2", {"case_id": "C02", "status": "passed", "round_attempt": 1})

    def test_concurrent_attempt_numbers_and_claim_are_unique(self):
        self.registry.register_round(self.round)
        def register(index):
            return EvalRegistry(self.path).register_run({**self.run, "id": f"RUN{index}"})["case_attempts"]["C01"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sorted(pool.map(register, range(1, 9))), list(range(1, 9)))
        def claim(_):
            try:
                EvalRegistry(self.path).claim_execution("RUN1")
                return True
            except RegistryError:
                return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(claim, range(8))), 1)
        self.assertEqual([e["status"] for e in self.registry.get_run("RUN1")["events"]], ["registered", "freezing"])

    def test_heartbeat_distinguishes_live_and_unknown_without_rewriting_history(self):
        self.registry.register_round(self.round)
        self.registry.register_run(self.run)
        self.registry.transition("RUN1", "freezing")
        self.assertEqual(self.registry.get_run("RUN1")["display_status"], "unknown")
        self.registry.heartbeat("RUN1")
        self.assertEqual(self.registry.get_run("RUN1")["liveness"], "live")
        self.registry.transition("RUN1", "running")
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE eval_heartbeats SET last_seen_at=? WHERE run_id=?", ("2000-01-01T00:00:00+00:00", "RUN1"))
        run = self.registry.get_run("RUN1")
        self.assertEqual(run["display_status"], "unknown")
        self.assertEqual(run["status"], "running")
        self.assertEqual(run["events"][-1]["status"], "running")
        self.registry.transition("RUN1", "unknown", "producer disconnected")
        with self.assertRaises(RegistryError):
            self.registry.heartbeat("RUN1")

    def test_recovery_only_appends_verified_terminal_result(self):
        self.registry.register_round(self.round)
        result = {"case_id": "C01", "status": "passed", "summary": "verified record"}
        provenance = {"source_batch": "RUN1", "manifest_sha256": "a" * 64, "record_sha256": "b" * 64}
        self.registry.register_run(self.run)
        with self.assertRaises(RegistryError):
            self.registry.recover_case_result("RUN1", result, **provenance)
        self.registry.claim_execution("RUN1")
        self.registry.bind_manifest("RUN1", "a" * 64)
        self.registry.transition("RUN1", "running")
        self.registry.add_case_result("RUN1", {"case_id": "C02", "status": "failed"})
        self.registry.transition("RUN1", "unknown", "disconnected")
        with self.assertRaises(RegistryError):
            self.registry.recover_case_result("RUN1", result, **{**provenance, "manifest_sha256": "invalid"})
        with self.assertRaises(RegistryError):
            self.registry.recover_case_result("RUN1", result, **{**provenance, "source_batch": "other"})
        with self.assertRaises(RegistryError):
            self.registry.recover_case_result("RUN1", {"case_id": "C03", "status": "passed"}, **provenance)
        self.registry.recover_case_result("RUN1", result, **provenance)
        self.registry.recover_case_result("RUN1", result, **provenance)
        with self.assertRaises(RegistryError):
            self.registry.recover_case_result("RUN1", {**result, "status": "failed"}, **provenance)
        with self.assertRaises(RegistryError):
            self.registry.recover_case_result("RUN1", result, **{**provenance, "record_sha256": "c" * 64})
        with self.assertRaises(RegistryError):
            self.registry.recover_case_result("RUN1", {"case_id": "C02", "status": "passed"}, **provenance)
        run = self.registry.get_run("RUN1")
        self.assertEqual(run["status"], "unknown")
        self.assertEqual(len(run["recoveries"]), 1)
        self.assertEqual(run["recoveries"][0]["manifest_sha256"], "a" * 64)
        self.assertEqual(len(run["results"]), 2)

    def test_recovery_accepts_failed_interrupted_unknown_but_not_completed(self):
        self.registry.register_round(self.round)
        for status in ("failed", "interrupted", "unknown", "completed"):
            run_id = "RUN-" + status
            self.registry.register_run({**self.run, "id": run_id})
            self.registry.claim_execution(run_id)
            self.registry.bind_manifest(run_id, "a" * 64)
            if status == "completed":
                self.registry.transition(run_id, "running")
            self.registry.transition(run_id, status)
            kwargs = {"source_batch": run_id, "manifest_sha256": "a" * 64, "record_sha256": "b" * 64}
            if status == "completed":
                with self.assertRaises(RegistryError):
                    self.registry.recover_case_result(run_id, {"case_id": "C01", "status": "failed"}, **kwargs)
            else:
                self.registry.recover_case_result(run_id, {"case_id": "C01", "status": "failed"}, **kwargs)
                self.assertEqual(self.registry.get_run(run_id)["status"], status)

    def test_frozen_manifest_binding_is_required_immutable_and_legacy_readable(self):
        self.registry.register_round(self.round)
        self.registry.register_run({**self.run, "metadata": {"requires_manifest_binding": True}})
        with self.assertRaises(RegistryError):
            self.registry.bind_manifest("RUN1", "a" * 64)
        self.registry.claim_execution("RUN1")
        with self.assertRaises(RegistryError):
            self.registry.transition("RUN1", "running")
        with self.assertRaises(RegistryError):
            self.registry.bind_manifest("RUN1", "INVALID")
        self.registry.bind_manifest("RUN1", "a" * 64)
        self.registry.bind_manifest("RUN1", "a" * 64)
        with self.assertRaises(RegistryError):
            self.registry.bind_manifest("RUN1", "b" * 64)
        self.assertEqual(self.registry.get_run("RUN1")["manifest_sha256"], "a" * 64)
        self.registry.transition("RUN1", "running")
        self.registry.transition("RUN1", "unknown")
        with self.assertRaises(RegistryError):
            self.registry.bind_manifest("RUN1", "a" * 64)
        with self.assertRaises(RegistryError):
            self.registry.recover_case_result("RUN1", {"case_id": "C01", "status": "passed"},
                source_batch="RUN1", manifest_sha256="b" * 64, record_sha256="c" * 64)
        # A database created by the previous version has no binding table.
        with sqlite3.connect(self.path) as db:
            db.execute("DROP TABLE eval_manifests")
        self.assertIsNone(self.registry.get_run("RUN1")["manifest_sha256"])

    def test_recovery_refuses_a_run_with_no_bound_manifest(self):
        self.registry.register_round(self.round)
        self.registry.register_run(self.run)
        self.registry.claim_execution("RUN1")
        self.registry.transition("RUN1", "unknown")
        with self.assertRaises(RegistryError):
            self.registry.recover_case_result("RUN1", {"case_id": "C01", "status": "failed"},
                source_batch="RUN1", manifest_sha256="a" * 64, record_sha256="b" * 64)

    def test_sensitive_fields_and_invalid_ids_rejected(self):
        with self.assertRaises(RegistryError):
            self.registry.register_round({**self.round, "id": "../outside"})
        self.registry.register_round(self.round)
        with self.assertRaises(RegistryError):
            self.registry.register_run({**self.run, "metadata": {"api_key": "do-not-save"}})
        with self.assertRaises(RegistryError):
            self.registry.register_run({**self.run, "metadata": {"output": "Authorization: Bearer leaked-value"}})
        self.assertNotIn("do-not-save", self.path.read_bytes().decode("utf-8", errors="ignore"))

    def test_refuses_to_initialize_over_existing_business_database(self):
        self.path.parent.mkdir(parents=True)
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE drafts (value TEXT)")
            db.execute("INSERT INTO drafts VALUES ('keep')")
        with self.assertRaises(RegistryError):
            self.registry.register_round(self.round)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT value FROM drafts").fetchone()[0], "keep")
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(tables, {"drafts"})

    def test_http_is_read_only_and_exposes_record(self):
        self.registry.register_round(self.round)
        self.registry.register_run(self.run)
        app = FastAPI()
        app.include_router(eval_registry_routes(self.registry))
        with TestClient(app) as client:
            response = client.get("/api/eval/v1/rounds")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["items"][0]["id"], "R1")
            self.assertEqual(client.get("/api/eval/v1/rounds/R1").json()["runs"][0]["status"], "registered")
            self.assertEqual(client.get("/api/eval/v1/rounds/nope").status_code, 404)
            self.assertEqual(client.post("/api/eval/v1/rounds", json={}).status_code, 405)


if __name__ == "__main__":
    unittest.main()
