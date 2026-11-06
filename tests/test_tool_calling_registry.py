"""Contract tests for formal tool evaluations without model calls or production data."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from creatoros.evaluation import tool_calling
from creatoros.evaluation.registry import EvalRegistry, RegistryError
from creatoros.evaluation.benchmark import manifest as benchmark_manifest
from creatoros.evaluation.benchmark.nonvideo_guard import evidence as guard_evidence, policy


class ToolCallingRegistryTests(unittest.TestCase):
    @staticmethod
    def write_fake_manifest(batch):
        raw = b'{"fixture":"manifest"}\n'
        (batch / "manifest.json").write_bytes(raw)
        (batch / "manifest.json.sha256").write_text(tool_calling._sha(raw), encoding="ascii")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.registry = EvalRegistry(self.root / "shared.sqlite")
        self.round = {"id": "R01", "title": "tool change", "code_version": "code-hash"}
        self.definition = {"id": "B05", "title": "test", "steps": [{"kind": "user", "text": "查一下"}]}
        self.dataset = {"cases": [self.definition]}
        self.metadata = {"dataset_sha256": "same"}
        self.root_patch = patch.object(tool_calling, "ROOT", self.root)
        self.metadata_patch = patch.object(tool_calling, "suite_and_metadata", return_value=(self.dataset, self.metadata))
        self.root_patch.start()
        self.metadata_patch.start()

    def tearDown(self):
        self.metadata_patch.stop()
        self.root_patch.stop()
        self.tmp.cleanup()

    def test_registers_before_freeze_and_keeps_freeze_failure(self):
        def invoke(args):
            self.assertEqual(self.registry.get_run("run01")["status"], "freezing")
            self.assertEqual(args[3], "freeze")
            return 7
        result = tool_calling.run_registered(self.registry, round_data=self.round, run_id="run01", cases=["B05"],
                                             allow_paid=True, invoke=invoke)
        self.assertEqual(result["stage"], "freeze")
        run = self.registry.get_run("run01")
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["results"], [])
        self.assertEqual([e["status"] for e in run["events"]], ["registered", "freezing", "failed"])

    def test_live_opt_in_and_guarded_selection_prevent_calls(self):
        with self.assertRaises(ValueError):
            tool_calling.run_registered(self.registry, round_data=self.round, run_id="run01", cases=["B05"],
                                        allow_paid=False, invoke=lambda _: self.fail("must not run"))
        self.assertEqual(self.registry.rounds()["items"], [])

    def test_invalid_freeze_receipt_never_starts_live_execution(self):
        batch = self.root / "data/agent-benchmark-v2/run01"
        calls = []
        def invoke(args):
            calls.append(args[3])
            batch.mkdir(parents=True)
            self.write_fake_manifest(batch)
            (batch / "manifest.json.sha256").write_text("0" * 64, encoding="ascii")
            return 0
        with self.assertRaisesRegex(ValueError, "receipt"):
            tool_calling.run_registered(self.registry, round_data=self.round, run_id="run01", cases=["B05"],
                                        allow_paid=True, invoke=invoke)
        self.assertEqual(calls, ["freeze"])
        run = self.registry.get_run("run01")
        self.assertEqual(run["status"], "failed")
        self.assertIsNone(run["manifest_sha256"])

    def test_real_runner_writes_batch_and_manifest_binding_to_claim_and_fallback_report(self):
        from creatoros.evaluation.benchmark import run as benchmark_run
        suite = json.loads(tool_calling.DATASET.read_text(encoding="utf-8"))
        case = next(item for item in suite["cases"] if item["id"] == "B05")
        batch = self.root / "batch01"
        batch.mkdir()
        raw = b"frozen manifest bytes"
        (batch / "manifest.json").write_bytes(raw)
        manifest = {"model_configuration": {"benchmark_run_id": "batch01"}, "dataset_id": tool_calling.SUITE_ID,
                    "git": {"sha": "a" * 40}, "pricing": {"currency": "USD", "models": {}}}
        with patch("creatoros.evaluation.benchmark.manifest.verify_manifest", return_value={"manifest": manifest}), \
             patch("creatoros.evaluation.benchmark.program_evidence_guard.verify_guard_version"), \
             patch.object(benchmark_run.subprocess, "Popen") as popen, \
             patch.object(benchmark_run, "wait_for_trial", side_effect=RuntimeError("startup failed")):
            popen.return_value.poll.return_value = 1
            report = benchmark_run.execute_case(batch, case, 0, manifest, tool_calling.DATASET)
        claim = json.loads((batch / "claims/B05.json").read_text(encoding="utf-8"))
        self.assertEqual(claim["benchmark_batch_id"], "batch01")
        self.assertEqual(claim["benchmark_manifest_sha256"], tool_calling._sha(raw))
        self.assertEqual(report["benchmark_batch_id"], claim["benchmark_batch_id"])
        self.assertEqual(report["benchmark_manifest_sha256"], claim["benchmark_manifest_sha256"])

    def test_imports_case_evidence_and_unknown_metrics_without_fabrication(self):
        batch = self.root / "data/agent-benchmark-v2/run01"
        record = batch / "records" / ("a" * 32)
        def invoke(args):
            if args[3] == "freeze":
                batch.mkdir(parents=True)
                self.write_fake_manifest(batch)
                return 0
            self.assertEqual(self.registry.get_run("run01")["status"], "running")
            record.mkdir(parents=True)
            (record / "report.json").write_text(json.dumps({"case_id": "B05", "execution_status": "completed",
                "elapsed_seconds": 1.5, "metrics": {"agent": {"model_calls": 1, "usage": {"input_tokens": 12, "output_tokens": None}}}}), encoding="utf-8")
            (record / "calls.json").write_text(json.dumps([{"tool": "manage_topics", "arguments": {"action": "list"},
                "raw_result": "must not copy raw content", "is_error": False}]), encoding="utf-8")
            (batch / "summary.json").write_text(json.dumps({"dataset_id": tool_calling.SUITE_ID, "cases": [{
                "case_id": "B05", "status": "passed", "auto_status": "passed", "semantic_status": "passed", "execution_status": "completed",
                "record_paths": ["records/" + "a" * 32 + "/report.json"], "scores": {"critical_status": "passed"},
                "benchmark_guard": {"status": "passed"},
                "trajectory": {"tool_calls": 1}}]}), encoding="utf-8")
            return 0
        result = tool_calling.run_registered(self.registry, round_data=self.round, run_id="run01", cases=["B05"],
                                             allow_paid=True, invoke=invoke)
        self.assertEqual(result["status"], "completed")
        case = self.registry.get_run("run01")["results"][0]
        self.assertEqual(case["metrics"]["input_tokens"], 12)
        self.assertIsNone(case["metrics"]["output_tokens"])
        self.assertEqual(case["metrics"]["usage_scope"], "provider_actual_agent")
        self.assertEqual(case["evidence"]["calls"][0]["tool"], "manage_topics")
        self.assertNotIn("raw_result", case["evidence"]["calls"][0])

    def test_unreadable_or_outside_report_cannot_pass(self):
        batch = self.root / "batch"
        batch.mkdir()
        result = tool_calling.result_from_row(batch, {"status": "passed", "execution_status": "completed", "record_paths": ["../secrets.json"]}, self.definition)
        self.assertEqual(result["status"], "needs_review")
        self.assertIsNone(result["metrics"]["input_tokens"])
        self.assertIsNone(result["evidence"]["actual_user_requests"])

    def test_actual_user_requests_are_corroborated_and_redacted(self):
        batch = self.root / "batch"
        record_id = "a" * 32
        record = batch / "records" / record_id
        record.mkdir(parents=True)
        secret = "my-secret-test-value"
        query = f"查询 bench-english；密钥 {secret}"
        (record / "report.json").write_text(json.dumps({"case_id": "B05", "run_id": record_id,
            "dataset_id": tool_calling.SUITE_ID, "execution_status": "completed"}), encoding="utf-8")
        (record / "messages.json").write_text(json.dumps([
            {"role": "user", "content": "[宿主资料事件] 不应展示"},
            {"role": "assistant", "content": "不应作为用户请求"},
            {"role": "user", "content": [{"type": "text", "text": query}, {"type": "image", "data": "private"}]}
        ], ensure_ascii=False), encoding="utf-8")
        (record / "browser.json").write_text(json.dumps({"case_id": "B05", "turns": [{"query": query}]}, ensure_ascii=False), encoding="utf-8")
        row = {"status": "needs_review", "execution_status": "completed", "record_paths": [f"records/{record_id}/report.json"]}
        with patch.dict(os.environ, {"CREATOROS_API_KEY": secret}):
            result = tool_calling.result_from_row(batch, row, self.definition)
        evidence = result["evidence"]
        self.assertEqual(evidence["user_requests_kind"], "dataset_template")
        self.assertEqual(evidence["user_requests"], ["查一下"])
        self.assertEqual(evidence["actual_user_requests"], ["查询 bench-english；密钥 [REDACTED]"])
        self.assertNotIn("宿主资料", json.dumps(evidence, ensure_ascii=False))

    def test_actual_user_requests_missing_mismatched_and_oversized_are_unknown(self):
        batch = self.root / "batch"
        record_id = "b" * 32
        record = batch / "records" / record_id
        record.mkdir(parents=True)
        report = {"case_id": "B05", "run_id": record_id, "dataset_id": tool_calling.SUITE_ID,
                  "execution_status": "completed"}
        (record / "report.json").write_text(json.dumps(report), encoding="utf-8")
        row = {"status": "needs_review", "execution_status": "completed", "record_paths": [f"records/{record_id}/report.json"]}
        result = tool_calling.result_from_row(batch, row, self.definition)
        self.assertIsNone(result["evidence"]["actual_user_requests"])
        (record / "messages.json").write_text(json.dumps([{"role": "user", "content": "host-only"}]), encoding="utf-8")
        (record / "browser.json").write_text(json.dumps({"case_id": "B05", "turns": [{"query": "actual"}]}), encoding="utf-8")
        result = tool_calling.result_from_row(batch, row, self.definition)
        self.assertIsNone(result["evidence"]["actual_user_requests"])
        query = "x" * 4097
        (record / "messages.json").write_text(json.dumps([{"role": "user", "content": query}]), encoding="utf-8")
        (record / "browser.json").write_text(json.dumps({"case_id": "B05", "turns": [{"query": query}]}), encoding="utf-8")
        result = tool_calling.result_from_row(batch, row, self.definition)
        self.assertIsNone(result["evidence"]["actual_user_requests"])

    def test_launcher_failure_is_not_attributed_to_model(self):
        batch = self.root / "batch"
        batch.mkdir()
        result = tool_calling.result_from_row(batch, {"status": "failed", "execution_status": "failed", "auto_status": "failed",
                                                       "observed_error": "server_start"}, self.definition)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["evidence"]["execution_status"], "failed")
        self.assertEqual(result["evidence"]["observed_error"], "server_start")

    def test_empty_request_aggregate_is_not_zero_actual_usage(self):
        batch = self.root / "batch"
        record = batch / "records" / ("a" * 32)
        record.mkdir(parents=True)
        (record / "report.json").write_text(json.dumps({"case_id": "B05", "execution_status": "failed", "elapsed_seconds": 0,
            "metrics": {"agent": {"model_calls": 0, "usage": {"input_tokens": 0, "output_tokens": 0}}}}), encoding="utf-8")
        result = tool_calling.result_from_row(batch, {"status": "failed", "execution_status": "failed",
            "record_paths": ["records/" + "a" * 32 + "/report.json"]}, self.definition)
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["metrics"]["input_tokens"])
        self.assertIsNone(result["metrics"]["output_tokens"])
        self.assertIsNone(result["metrics"]["elapsed_seconds"])

    def test_framework_report_metrics_and_projection_change_comparability(self):
        framework = self.root / "creatoros/evaluation/benchmark"
        framework.mkdir(parents=True)
        tool_file = self.root / "creatoros/evaluation/tool_calling.py"
        browser_file = self.root / "web/e2e/live-benchmark-v2.mjs"
        browser_file.parent.mkdir(parents=True)
        for file in (framework / "report.py", framework / "metrics.py", tool_file, browser_file):
            file.write_text("original", encoding="utf-8")
        before = tool_calling._hash_files(tool_calling._driver_files())
        for file in (framework / "report.py", framework / "metrics.py", tool_file):
            file.write_text("changed", encoding="utf-8")
            self.assertNotEqual(before, tool_calling._hash_files(tool_calling._driver_files()), str(file))
            file.write_text("original", encoding="utf-8")

    def test_concurrent_same_run_has_one_owner_and_loser_cannot_change_status(self):
        entered, release = threading.Event(), threading.Event()
        invocations = []
        def invoke(args):
            invocations.append(args[3])
            entered.set()
            self.assertTrue(release.wait(10))
            return 7
        def run(reg):
            return tool_calling.run_registered(reg, round_data=self.round, run_id="race01", cases=["B05"],
                                                allow_paid=True, invoke=invoke)
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(run, self.registry)
            self.assertTrue(entered.wait(10))
            second = executor.submit(run, EvalRegistry(self.root / "shared.sqlite"))
            try:
                with self.assertRaises(RegistryError):
                    second.result(timeout=10)
                self.assertEqual(self.registry.get_run("race01")["status"], "freezing")
                self.assertEqual(invocations, ["freeze"])
            finally:
                release.set()
            self.assertEqual(first.result(timeout=10)["stage"], "freeze")
        run_data = self.registry.get_run("race01")
        self.assertEqual([e["status"] for e in run_data["events"]], ["registered", "freezing", "failed"])
        with self.assertRaises(RegistryError):
            run(self.registry)
        self.assertEqual(self.registry.get_run("race01")["status"], "failed")
        self.assertEqual(invocations, ["freeze"])

    def test_interrupt_syncs_completed_case_before_terminal_status(self):
        batch = self.root / "data/agent-benchmark-v2/run01"
        record = batch / "records" / ("a" * 32)
        def invoke(args):
            if args[3] == "freeze":
                batch.mkdir(parents=True)
                self.write_fake_manifest(batch)
                return 0
            record.mkdir(parents=True)
            (record / "report.json").write_text(json.dumps({"case_id": "B05", "execution_status": "completed"}), encoding="utf-8")
            (batch / "summary.json").write_text(json.dumps({"dataset_id": tool_calling.SUITE_ID, "cases": [{
                "case_id": "B05", "status": "needs_review", "execution_status": "completed",
                "record_paths": ["records/" + "a" * 32 + "/report.json"]}]}), encoding="utf-8")
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            tool_calling.run_registered(self.registry, round_data=self.round, run_id="run01", cases=["B05"],
                                        allow_paid=True, invoke=invoke)
        run = self.registry.get_run("run01")
        self.assertEqual(run["status"], "interrupted")
        self.assertEqual(run["results"][0]["case_id"], "B05")


class ToolCallingRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sha = "a" * 40
        self.dataset_path = self.root / "docs/agent-eval/tool-calling-v1/cases.json"
        self.dataset_path.parent.mkdir(parents=True)
        shutil.copyfile(tool_calling.DATASET, self.dataset_path)
        self.dataset = json.loads(self.dataset_path.read_text(encoding="utf-8"))
        self.metadata = {"dataset_sha256": tool_calling._sha(self.dataset_path.read_bytes()), "model": "deepseek-flash"}
        self.source = self.root / "source.py"
        self.source.write_text("VALUE = 1\n", encoding="utf-8")
        self.batch = self.root / "data/agent-benchmark-v2/run01"
        self.batch.mkdir(parents=True)
        self.registry = EvalRegistry(self.root / "shared.sqlite")
        self.addCleanup(patch.stopall)
        patch.object(tool_calling, "ROOT", self.root).start()
        patch.object(tool_calling, "DATASET", self.dataset_path).start()
        patch.object(tool_calling, "suite_and_metadata", return_value=(self.dataset, self.metadata)).start()
        patch.object(benchmark_manifest, "PROJECT_ROOT", self.root).start()
        patch.object(benchmark_manifest, "_default_sources", return_value=[self.source]).start()
        patch.object(benchmark_manifest, "_git_state", return_value={"sha": self.sha, "dirty": False,
                     "dirty_paths": [], "status_available": True}).start()
        self.manifest = benchmark_manifest.freeze_manifest(self.batch / "manifest.json", self.dataset_path,
            pricing={"currency": "USD"}, model_configuration={"provider_request_model": "deepseek-flash", "retries": 0,
                "benchmark_run_id": "run01", "benchmark_guard": policy(["B05"])}, case_ids=["B05"])
        self.registry.register_round({"id": "R01", "title": "tool", "code_version": self.sha})
        self.registry.register_run({"id": "run01", "round_id": "R01", "suite_id": tool_calling.SUITE_ID,
            "suite_version": "v1", "planned_cases": ["B05"], "metadata": self.metadata})
        self.registry.claim_execution("run01")
        self.registry.bind_manifest("run01", tool_calling._sha((self.batch / "manifest.json").read_bytes()))
        self.registry.transition("run01", "running")
        self.registry.transition("run01", "interrupted", error="stopped")
        self.manifest_sha = tool_calling._sha((self.batch / "manifest.json").read_bytes())
        self.record_id = "b" * 32
        self.record = self.batch / "records" / self.record_id
        self.record.mkdir(parents=True)
        (self.batch / "claims").mkdir()
        self.write_json(self.batch / "claims/B05.json", {"case_id": "B05", "run_id": self.record_id, "first_attempt": True,
            "benchmark_batch_id": "run01", "benchmark_manifest_sha256": self.manifest_sha})
        self.write_json(self.record / "guard.json", {"policy": policy(["B05"]), "case_id": "B05",
            "video_dispatch_installed": True, "publication_client_installed": True, "network_sandbox": False})
        self.report = {"case_id": "B05", "run_id": self.record_id, "dataset_id": tool_calling.SUITE_ID,
            "benchmark_batch_id": "run01", "benchmark_manifest_sha256": self.manifest_sha,
            "auto_status": "passed", "semantic_status": "passed", "execution_status": "completed", "error": None,
            "scores": {"protocol": "case-rubrics-v3", "critical_status": "passed"},
            "dimensions": {"boundary_enforced": "passed"}, "trajectory": {"tool_calls": 1}, "checks": []}
        self.write_json(self.record / "report.json", self.report)
        self.row = {"case_id": "B05", "status": "passed", "auto_status": "passed", "semantic_status": "passed",
            "execution_status": "completed", "observed_error": None, "scores": self.report["scores"],
            "dimensions": self.report["dimensions"], "trajectory": self.report["trajectory"],
            "benchmark_guard": guard_evidence(self.manifest, "B05", self.record),
            "record_paths": [f"records/{self.record_id}/report.json"]}
        self.summary = {"dataset_id": tool_calling.SUITE_ID, "planned_case_ids": ["B05"], "cases": [self.row]}
        self.write_json(self.batch / "summary.json", self.summary)

    @staticmethod
    def write_json(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def test_recovery_is_idempotent_and_preserves_terminal_status(self):
        first = tool_calling.recover_registered(self.registry, "run01")
        second = tool_calling.recover_registered(self.registry, "run01")
        self.assertEqual(first, second)
        run = self.registry.get_run("run01")
        self.assertEqual(run["status"], "interrupted")
        self.assertEqual(len(run["results"]), 1)
        self.assertEqual(len(run["recoveries"]), 1)
        self.assertEqual(run["recoveries"][0]["source_batch"], "run01")

    def test_tampered_report_summary_and_claim_are_rejected(self):
        for path, altered in ((self.record / "report.json", {**self.report, "semantic_status": "failed"}),
                              (self.batch / "summary.json", {**self.summary, "planned_case_ids": ["B06"]}),
                              (self.batch / "claims/B05.json", {"case_id": "B05", "run_id": "c" * 32, "first_attempt": True})):
            before = path.read_bytes()
            self.write_json(path, altered)
            with self.assertRaises(ValueError):
                tool_calling.recover_registered(self.registry, "run01")
            self.assertEqual(self.registry.get_run("run01")["results"], [])
            path.write_bytes(before)

    def test_cross_run_and_changed_source_are_rejected(self):
        self.registry.register_run({"id": "run02", "round_id": "R01", "suite_id": tool_calling.SUITE_ID,
            "suite_version": "v1", "planned_cases": ["B05"], "metadata": self.metadata})
        self.registry.claim_execution("run02")
        self.registry.transition("run02", "unknown", error="stopped")
        with self.assertRaises((OSError, ValueError)):
            tool_calling.recover_registered(self.registry, "run02")
        self.source.write_text("VALUE = 2\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            tool_calling.recover_registered(self.registry, "run01")
        self.assertEqual(self.registry.get_run("run01")["results"], [])

    def test_duplicate_or_outside_record_is_rejected(self):
        self.row["record_paths"] = ["../outside/report.json"]
        self.write_json(self.batch / "summary.json", self.summary)
        with self.assertRaises(ValueError):
            tool_calling.recover_registered(self.registry, "run01")
        self.assertEqual(self.registry.get_run("run01")["results"], [])

    def test_other_batch_claim_and_report_cannot_be_recovered_under_target_manifest(self):
        claim = {"case_id": "B05", "run_id": self.record_id, "first_attempt": True,
                 "benchmark_batch_id": "run02", "benchmark_manifest_sha256": "c" * 64}
        other_report = {**self.report, "benchmark_batch_id": "run02", "benchmark_manifest_sha256": "c" * 64}
        self.write_json(self.batch / "claims/B05.json", claim)
        self.write_json(self.record / "report.json", other_report)
        with self.assertRaisesRegex(ValueError, "claim"):
            tool_calling.recover_registered(self.registry, "run01")
        self.assertEqual(self.registry.get_run("run01")["results"], [])
        # If only the claim is rewritten, the mismatched report is still refused.
        self.write_json(self.batch / "claims/B05.json", {**claim, "benchmark_batch_id": "run01",
            "benchmark_manifest_sha256": self.manifest_sha})
        with self.assertRaisesRegex(ValueError, "Report identity"):
            tool_calling.recover_registered(self.registry, "run01")
        self.assertEqual(self.registry.get_run("run01")["results"], [])

    def test_dirty_source_even_with_valid_manifest_receipt_is_rejected(self):
        manifest_path = self.batch / "manifest.json"
        encoded = json.loads(manifest_path.read_text(encoding="utf-8"))
        encoded["git"]["dirty"] = True
        raw = (json.dumps(encoded, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        manifest_path.write_bytes(raw)
        (self.batch / "manifest.json.sha256").write_text(tool_calling._sha(raw) + "\n", encoding="ascii")
        # Simulate a batch originally frozen and bound while dirty, rather
        # than merely replacing the already-bound manifest.
        with sqlite3.connect(self.registry.path) as db:
            db.execute("UPDATE eval_manifests SET manifest_sha256=? WHERE run_id=?", (tool_calling._sha(raw), "run01"))
        with self.assertRaisesRegex(ValueError, "clean"):
            tool_calling.recover_registered(self.registry, "run01")
        self.assertEqual(self.registry.get_run("run01")["results"], [])

    def test_replaced_or_unbound_manifest_is_rejected_without_writing(self):
        manifest_path = self.batch / "manifest.json"
        encoded = json.loads(manifest_path.read_text(encoding="utf-8"))
        encoded["frozen_at_utc"] = "2026-01-01T00:00:00+00:00"
        raw = (json.dumps(encoded, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        manifest_path.write_bytes(raw)
        (self.batch / "manifest.json.sha256").write_text(tool_calling._sha(raw) + "\n", encoding="ascii")
        # This manifest is internally consistent but is not the one bound before execution.
        with self.assertRaisesRegex(ValueError, "binding"):
            tool_calling.recover_registered(self.registry, "run01")
        self.assertEqual(self.registry.get_run("run01")["results"], [])
        with sqlite3.connect(self.registry.path) as db:
            db.execute("DELETE FROM eval_manifests WHERE run_id=?", ("run01",))
        with self.assertRaisesRegex(ValueError, "binding"):
            tool_calling.recover_registered(self.registry, "run01")
        self.assertEqual(self.registry.get_run("run01")["results"], [])

    def test_manifest_must_also_name_the_registered_batch(self):
        manifest_path = self.batch / "manifest.json"
        encoded = json.loads(manifest_path.read_text(encoding="utf-8"))
        encoded["model_configuration"]["benchmark_run_id"] = "run02"
        raw = (json.dumps(encoded, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        manifest_path.write_bytes(raw)
        (self.batch / "manifest.json.sha256").write_text(tool_calling._sha(raw) + "\n", encoding="ascii")
        with sqlite3.connect(self.registry.path) as db:
            db.execute("UPDATE eval_manifests SET manifest_sha256=? WHERE run_id=?", (tool_calling._sha(raw), "run01"))
        with self.assertRaisesRegex(ValueError, "manifest differs"):
            tool_calling.recover_registered(self.registry, "run01")
        self.assertEqual(self.registry.get_run("run01")["results"], [])

    def test_different_current_metadata_is_rejected(self):
        metadata = {**self.metadata, "driver_hash": "changed"}
        with patch.object(tool_calling, "suite_and_metadata", return_value=(self.dataset, metadata)):
            with self.assertRaisesRegex(ValueError, "metadata"):
                tool_calling.recover_registered(self.registry, "run01")
        self.assertEqual(self.registry.get_run("run01")["results"], [])


if __name__ == "__main__":
    unittest.main()
