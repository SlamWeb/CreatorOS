"""Natural and base requests with equal short IDs stay isolated in v4."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from creatoros.evaluation import regression_runner, tool_calling
from creatoros.evaluation.benchmark.local_dispatch_guard import CASES_BY_NAMESPACE
from creatoros.evaluation.benchmark.nonvideo_guard import evidence, policy, validate
from creatoros.evaluation.benchmark.run import freeze
from creatoros.evaluation.benchmark.manifest import verify_manifest
from creatoros.evaluation.comparison import CONTRACT_FIELDS, compare_runs


class NaturalRegressionRunnerTests(unittest.TestCase):
    def test_exact_guard_scope_and_old_policy_are_unchanged(self):
        self.assertEqual(CASES_BY_NAMESPACE["local-dispatch-v4"]["natural"], {"B06", "B07"})
        self.assertNotIn("source_namespace", policy(["B06"], local_dispatch="local-dispatch-v3"))
        for namespace, case in (("natural", "B05"), ("natural", "B08"), ("bad", "B06"), (None, "B06")):
            with self.subTest(namespace=namespace, case=case), self.assertRaises(ValueError):
                policy([case], local_dispatch="local-dispatch-v4", source_namespace=namespace)
        with self.assertRaises(ValueError):
            policy(["B06"], local_dispatch="local-dispatch-v3", source_namespace="base")

    def test_selection_forbids_mixed_sources_and_placeholders(self):
        for cases in (["base:B06", "natural:B06"], ["base:B06", "natural:B07"], ["natural:B08"],
                      ["expansion:B06"], ["natural:B06", "natural:B06"]):
            with self.subTest(cases=cases), self.assertRaises(ValueError):
                prepared = regression_runner.prepare("regular-v4", cases, ["agent_shared"])
                if prepared["blocked"]:
                    raise ValueError("not admitted")
        for identity in ("base:B06", "natural:B06", "natural:B07"):
            prepared = regression_runner.prepare("regular-v4", [identity], ["agent_shared"])
            self.assertFalse(prepared["blocked"])
            regression_runner.validate_frozen_binding(prepared["selected_cases"], prepared["catalog_binding"])
        legacy = regression_runner.prepare("regular-v3", ["base:B06"], ["agent_shared"])
        self.assertEqual(legacy["requires_local_dispatch"], "local-dispatch-v3")
        self.assertEqual(legacy["selected_cases"], ["B06"])

    def test_real_freeze_binds_namespace_and_frozen_source(self):
        for identity in ("base:B06", "natural:B06", "natural:B07"):
            with self.subTest(identity=identity), tempfile.TemporaryDirectory() as temp:
                prepared = regression_runner.prepare("regular-v4", [identity], ["agent_shared"])
                binding, cases = prepared["catalog_binding"], prepared["selected_cases"]
                dataset_path, expected = tool_calling._execution_dataset("local-dispatch-v4", binding)
                dataset, metadata = tool_calling.suite_and_metadata(cases, local_dispatch="local-dispatch-v4", catalog_binding=binding)
                namespace = identity.split(":")[0]
                self.assertEqual(dataset["dataset_id"], expected)
                self.assertEqual(metadata["source_namespace"], namespace)
                root = Path(temp) / "batch"
                root.mkdir()
                manifest = freeze(root, dataset_path, cases, nonvideo_guard=True, local_dispatch="local-dispatch-v4", catalog_binding=binding)
                self.assertEqual(manifest["dataset_id"], expected)
                self.assertEqual(manifest["model_configuration"]["benchmark_guard"]["source_namespace"], namespace)
                self.assertTrue(validate(manifest))
                self.assertEqual(verify_manifest(root / "manifest.json")["manifest"]["selected_case_ids"], cases)
                other = copy.deepcopy(manifest)
                other["model_configuration"]["benchmark_guard"]["source_namespace"] = "base" if namespace == "natural" else "natural"
                with self.assertRaises(ValueError):
                    validate(other)
                forged = copy.deepcopy(manifest)
                forged["model_configuration"]["catalog_binding"]["selected_instance_ids"] = [("base:" if namespace == "natural" else "natural:") + cases[0]]
                with self.assertRaises(ValueError):
                    validate(forged)
                wrong_digest = copy.deepcopy(binding)
                source = next(row for row in wrong_digest["frozen_catalog"]["source"]["sources"] if row["namespace"] == namespace)
                source["canonical_sha256"] = "f" * 64
                with self.assertRaises(ValueError):
                    tool_calling._execution_dataset("local-dispatch-v4", wrong_digest)
                wrong = Path(temp) / "same-copy.json"
                wrong.write_bytes(dataset_path.read_bytes())
                with self.assertRaisesRegex(ValueError, "authorized frozen source"):
                    freeze(root, wrong, cases, nonvideo_guard=True, local_dispatch="local-dispatch-v4", catalog_binding=binding)

    def test_namespace_is_a_required_comparison_contract(self):
        meta = {key: "same" for key in CONTRACT_FIELDS}
        meta["execution_mode"] = "local-dispatch-v4"
        result = {"case_id": "B06", "status": "passed", "case_definition_hash": "same", "round_attempt": 1}
        first = {"id": "first", "metadata": {**meta, "source_namespace": "base"}, "planned_cases": ["B06"], "results": [result]}
        second = {"id": "second", "metadata": {**meta, "source_namespace": "natural"}, "planned_cases": ["B06"], "results": [result]}
        comparison = compare_runs(first, second)
        self.assertFalse(comparison["contract_compatible"])
        self.assertEqual(comparison["comparable_cases"], 0)
        self.assertIn({"field": "source_namespace", "reason": "different"}, comparison["contract_differences"])
        del second["metadata"]["source_namespace"]
        self.assertFalse(compare_runs(first, second)["contract_compatible"])

    def test_natural_record_is_verified_for_sync_and_recovery(self):
        identity, case, run_id, record_id = "natural:B06", "B06", "run01", "b" * 32
        prepared = regression_runner.prepare("regular-v4", [identity], ["agent_shared"])
        binding = prepared["catalog_binding"]
        dataset_path, dataset_id = tool_calling._execution_dataset("local-dispatch-v4", binding)
        dataset, metadata = tool_calling.suite_and_metadata([case], local_dispatch="local-dispatch-v4", catalog_binding=binding)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            batch = root / "data/agent-benchmark-v2" / run_id
            record = batch / "records" / record_id
            record.mkdir(parents=True)
            manifest = freeze(batch, dataset_path, [case], nonvideo_guard=True, local_dispatch="local-dispatch-v4", catalog_binding=binding)
            verify_manifest(batch / "manifest.json")
            digest = tool_calling._sha((batch / "manifest.json").read_bytes())
            policy_config = manifest["model_configuration"]["benchmark_guard"]
            (record / "guard.json").write_text(json.dumps({"policy": policy_config, "case_id": case,
                "video_dispatch_installed": True, "publication_client_installed": True, "network_sandbox": False,
                "local_dispatch_installed": True, "background_services_deferred": True}))
            shared = {"benchmark_batch_id": run_id, "benchmark_manifest_sha256": digest}
            claim = {"case_id": case, "run_id": record_id, "first_attempt": True, **shared}
            (batch / "claims").mkdir()
            (batch / f"claims/{case}.json").write_text(json.dumps(claim))
            report = {"case_id": case, "run_id": record_id, "dataset_id": dataset_id, **shared,
                      "auto_status": "passed", "semantic_status": "passed", "execution_status": "completed",
                      "dimensions": {"boundary_enforced": "passed"}, "error": None}
            (record / "report.json").write_text(json.dumps(report))
            row = {key: report[key] for key in ("case_id", "auto_status", "semantic_status", "execution_status", "dimensions")}
            row.update(status="passed", observed_error=None, record_paths=[f"records/{record_id}/report.json"],
                       benchmark_guard=evidence(manifest, case, record))
            summary = {"dataset_id": dataset_id, "planned_case_ids": [case], "cases": [row]}
            (batch / "summary.json").write_text(json.dumps(summary))
            definition = next(item for item in dataset["cases"] if item["id"] == case)
            class Registry:
                def __init__(self): self.added, self.recovered = [], []
                def get_run(self, _): return {"id": run_id, "round_id": "round", "status": "interrupted",
                    "suite_id": dataset_id, "suite_version": "v2", "manifest_sha256": digest,
                    "planned_cases": [case], "metadata": metadata, "results": [], "recoveries": []}
                def round_detail(self, _): return {"round": {"code_version": "a" * 40}}
                def add_case_result(self, _, result): self.added.append(result)
                def recover_case_result(self, _, result, **_kw): self.recovered.append(result)
            registry = Registry()
            self.assertEqual(tool_calling.sync_summary(registry, run_id, batch, {case: definition}, dataset_id=dataset_id), 1)
            self.assertEqual(registry.added[0]["case_definition_hash"], tool_calling._definition_hash(definition))
            altered = {**report, "dataset_id": tool_calling.REGRESSION_SUITE_ID}
            (record / "report.json").write_text(json.dumps(altered))
            with self.assertRaisesRegex(ValueError, "Report does not match"):
                tool_calling.sync_summary(registry, run_id, batch, {case: definition}, dataset_id=dataset_id)
            (record / "report.json").write_text(json.dumps(report))
            (batch / f"claims/{case}.json").write_text(json.dumps({**claim, "benchmark_batch_id": "another"}))
            with self.assertRaisesRegex(ValueError, "claim"):
                tool_calling.sync_summary(registry, run_id, batch, {case: definition}, dataset_id=dataset_id)
            (batch / f"claims/{case}.json").write_text(json.dumps(claim))
            fake_manifest = copy.deepcopy(manifest)
            fake_manifest["git"] = {"sha": "a" * 40, "dirty": False, "status_available": True}
            with patch.object(tool_calling, "ROOT", root), \
                 patch.object(tool_calling, "suite_and_metadata", return_value=(dataset, metadata)), \
                 patch("creatoros.evaluation.benchmark.manifest.verify_manifest", return_value={"manifest": fake_manifest}):
                tool_calling.recover_registered(registry, run_id)
            self.assertEqual(len(registry.recovered), 1)

    def test_registered_natural_run_freezes_and_binds_before_controlled_failure(self):
        from creatoros.evaluation.registry import EvalRegistry
        prepared = regression_runner.prepare("regular-v4", ["natural:B06"], ["agent_shared"])
        binding = prepared["catalog_binding"]
        path, identity = tool_calling._execution_dataset("local-dispatch-v4", binding)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            batch = root / "data/agent-benchmark-v2/natural-run"
            registry = EvalRegistry(root / "registry.sqlite")
            commands = []
            def invoke(args):
                commands.append(args[3])
                if args[3] == "freeze":
                    self.assertEqual(registry.get_run("natural-run")["status"], "freezing")
                    frozen_binding = json.loads(Path(args[args.index("--catalog-binding-file") + 1]).read_text(encoding="utf-8"))
                    batch.mkdir(parents=True)
                    freeze(batch, path, ["B06"], nonvideo_guard=True,
                           local_dispatch="local-dispatch-v4", catalog_binding=frozen_binding)
                    return 0
                self.assertEqual(registry.get_run("natural-run")["status"], "running")
                return 9  # Do not start a browser or paid model.
            with patch.object(tool_calling, "ROOT", root), \
                 patch.object(tool_calling, "_execution_dataset", return_value=(path, identity)), \
                 patch.object(tool_calling, "_hash_files", return_value="a" * 64), \
                 patch.object(tool_calling, "_driver_files", return_value=[]):
                result = tool_calling.run_registered(registry, round_data={"id": "round", "title": "natural",
                    "code_version": "test", "changes": ["agent_shared"]}, run_id="natural-run", cases=["B06"],
                    allow_paid=True, invoke=invoke, local_dispatch="local-dispatch-v4", catalog_binding=binding)
            run = registry.get_run("natural-run")
            self.assertEqual(commands, ["freeze", "run"])
            self.assertEqual(result["status"], "failed")
            self.assertEqual(run["suite_id"], tool_calling.NATURAL_SUITE_ID)
            self.assertEqual(run["metadata"]["source_namespace"], "natural")
            self.assertEqual(run["manifest_sha256"], tool_calling._sha((batch / "manifest.json").read_bytes()))


if __name__ == "__main__":
    unittest.main()
