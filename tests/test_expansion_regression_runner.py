"""v5 source isolation, immutable selection, registration and recovery."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from creatoros.evaluation import regression_runner, tool_calling
from creatoros.evaluation.benchmark.local_dispatch_guard import CASES_BY_NAMESPACE, CASES_BY_VERSION
from creatoros.evaluation.benchmark.nonvideo_guard import evidence, policy, validate
from creatoros.evaluation.benchmark.run import freeze
from creatoros.evaluation.benchmark.manifest import verify_manifest
from creatoros.evaluation.comparison import CONTRACT_FIELDS, compare_runs


class ExpansionRegressionRunnerTests(unittest.TestCase):
    def test_exact_scopes_and_legacy_policy(self):
        v4, v5 = CASES_BY_NAMESPACE["local-dispatch-v4"], CASES_BY_NAMESPACE["local-dispatch-v5"]
        self.assertEqual(v5["base"] - v4["base"], {"B18", "B19", "B33", "B35", "B37", "B39"})
        self.assertEqual(v5["natural"], v4["natural"])
        self.assertEqual(v5["expansion"], {"E06", "E07", "Y04", "C02", "E05"})
        self.assertEqual(CASES_BY_VERSION["local-dispatch-v4"], v4["base"])
        self.assertNotIn("source_namespace", policy(["B06"], local_dispatch="local-dispatch-v3"))
        with self.assertRaises(ValueError):
            policy(["E06"], local_dispatch="local-dispatch-v4", source_namespace="expansion")
        for namespace, case in (("base", "E06"), ("natural", "E06"), ("expansion", "B06"), ("expansion", "E08")):
            with self.subTest(namespace=namespace, case=case), self.assertRaises(ValueError):
                policy([case], local_dispatch="local-dispatch-v5", source_namespace=namespace)

    def test_no_mixed_sources_or_unqualified_expansion(self):
        for selected in (["base:B06", "natural:B06"], ["base:B06", "expansion:E06"],
                         ["natural:B06", "expansion:E06"], ["expansion:E08"], ["expansion:E06", "expansion:E06"]):
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                prepared = regression_runner.prepare("regular-v5", selected, ["agent_shared"])
                if prepared["blocked"]:
                    raise ValueError("not admitted")
        for case in ("E06", "E07", "Y04", "C02", "E05"):
            prepared = regression_runner.prepare("regular-v5", ["expansion:" + case], ["agent_shared"])
            self.assertFalse(prepared["blocked"])
            regression_runner.validate_frozen_binding([case], prepared["catalog_binding"])
        self.assertEqual(regression_runner.prepare("regular-v4", ["natural:B06"])["requires_local_dispatch"], "local-dispatch-v4")

    def test_real_freeze_for_each_source_and_source_swap_rejected(self):
        for identity in ("base:B18", "natural:B06", "expansion:E06"):
            with self.subTest(identity=identity), tempfile.TemporaryDirectory() as temp:
                prepared = regression_runner.prepare("regular-v5", [identity], ["agent_shared"])
                binding, cases = prepared["catalog_binding"], prepared["selected_cases"]
                path, expected = tool_calling._execution_dataset("local-dispatch-v5", binding)
                dataset, metadata = tool_calling.suite_and_metadata(cases, local_dispatch="local-dispatch-v5", catalog_binding=binding)
                namespace = identity.split(":")[0]
                self.assertEqual(dataset["dataset_id"], expected)
                self.assertEqual(metadata["source_namespace"], namespace)
                root = Path(temp) / "batch"
                root.mkdir()
                manifest = freeze(root, path, cases, nonvideo_guard=True, local_dispatch="local-dispatch-v5", catalog_binding=binding)
                self.assertEqual(manifest["dataset_id"], expected)
                self.assertTrue(validate(manifest))
                self.assertEqual(verify_manifest(root / "manifest.json")["manifest"]["selected_case_ids"], cases)
                altered = copy.deepcopy(manifest)
                altered["model_configuration"]["benchmark_guard"]["source_namespace"] = "base" if namespace != "base" else "expansion"
                with self.assertRaises(ValueError):
                    validate(altered)
                changed = copy.deepcopy(binding)
                src = next(row for row in changed["frozen_catalog"]["source"]["sources"] if row["namespace"] == namespace)
                src["canonical_sha256"] = "f" * 64
                with self.assertRaises(ValueError):
                    tool_calling._execution_dataset("local-dispatch-v5", changed)

    def test_expansion_sync_and_recovery_preserve_dataset_identity(self):
        case, run_id, record_id = "E06", "run01", "b" * 32
        prepared = regression_runner.prepare("regular-v5", ["expansion:" + case], ["agent_shared"])
        binding = prepared["catalog_binding"]
        path, dataset_id = tool_calling._execution_dataset("local-dispatch-v5", binding)
        dataset, metadata = tool_calling.suite_and_metadata([case], local_dispatch="local-dispatch-v5", catalog_binding=binding)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            batch = root / "data/agent-benchmark-v2" / run_id
            record = batch / "records" / record_id
            record.mkdir(parents=True)
            manifest = freeze(batch, path, [case], nonvideo_guard=True, local_dispatch="local-dispatch-v5", catalog_binding=binding)
            verify_manifest(batch / "manifest.json")
            digest = tool_calling._sha((batch / "manifest.json").read_bytes())
            guard = manifest["model_configuration"]["benchmark_guard"]
            (record / "guard.json").write_text(json.dumps({"policy": guard, "case_id": case,
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
            (batch / "summary.json").write_text(json.dumps({"dataset_id": dataset_id, "planned_case_ids": [case], "cases": [row]}))
            definition = next(item for item in dataset["cases"] if item["id"] == case)
            class Registry:
                def __init__(self): self.added, self.recovered = [], []
                def get_run(self, _): return {"id": run_id, "round_id": "round", "status": "interrupted",
                    "suite_id": dataset_id, "suite_version": "v1", "manifest_sha256": digest,
                    "planned_cases": [case], "metadata": metadata, "results": [], "recoveries": []}
                def round_detail(self, _): return {"round": {"code_version": "a" * 40}}
                def add_case_result(self, _, result): self.added.append(result)
                def recover_case_result(self, _, result, **_kw): self.recovered.append(result)
            registry = Registry()
            self.assertEqual(tool_calling.sync_summary(registry, run_id, batch, {case: definition}, dataset_id=dataset_id), 1)
            self.assertEqual(registry.added[0]["case_definition_hash"], tool_calling._definition_hash(definition))
            (record / "report.json").write_text(json.dumps({**report, "dataset_id": tool_calling.REGRESSION_SUITE_ID}))
            with self.assertRaisesRegex(ValueError, "Report does not match"):
                tool_calling.sync_summary(registry, run_id, batch, {case: definition}, dataset_id=dataset_id)
            (record / "report.json").write_text(json.dumps(report))
            (batch / f"claims/{case}.json").write_text(json.dumps({**claim, "benchmark_batch_id": "another"}))
            with self.assertRaisesRegex(ValueError, "claim"):
                tool_calling.sync_summary(registry, run_id, batch, {case: definition}, dataset_id=dataset_id)
            (batch / f"claims/{case}.json").write_text(json.dumps(claim))
            fake = copy.deepcopy(manifest)
            fake["git"] = {"sha": "a" * 40, "dirty": False, "status_available": True}
            with patch.object(tool_calling, "ROOT", root), \
                 patch.object(tool_calling, "suite_and_metadata", return_value=(dataset, metadata)), \
                 patch("creatoros.evaluation.benchmark.manifest.verify_manifest", return_value={"manifest": fake}):
                tool_calling.recover_registered(registry, run_id)
            self.assertEqual(len(registry.recovered), 1)

    def test_source_namespace_prevents_comparison(self):
        meta = {key: "same" for key in CONTRACT_FIELDS}
        meta["execution_mode"] = "local-dispatch-v5"
        result = {"case_id": "E06", "status": "passed", "case_definition_hash": "same", "round_attempt": 1}
        first = {"id": "first", "metadata": {**meta, "source_namespace": "base"}, "planned_cases": ["E06"], "results": [result]}
        second = {"id": "second", "metadata": {**meta, "source_namespace": "expansion"}, "planned_cases": ["E06"], "results": [result]}
        comparison = compare_runs(first, second)
        self.assertFalse(comparison["contract_compatible"])
        self.assertIn({"field": "source_namespace", "reason": "different"}, comparison["contract_differences"])

    def test_registered_expansion_freezes_before_controlled_failure(self):
        from creatoros.evaluation.registry import EvalRegistry
        prepared = regression_runner.prepare("regular-v5", ["expansion:E06"], ["agent_shared"])
        binding = prepared["catalog_binding"]
        path, identity = tool_calling._execution_dataset("local-dispatch-v5", binding)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            batch = root / "data/agent-benchmark-v2/expansion-run"
            registry = EvalRegistry(root / "registry.sqlite")
            commands = []
            def invoke(args):
                commands.append(args[3])
                if args[3] == "freeze":
                    self.assertEqual(registry.get_run("expansion-run")["status"], "freezing")
                    frozen_binding = json.loads(Path(args[args.index("--catalog-binding-file") + 1]).read_text(encoding="utf-8"))
                    batch.mkdir(parents=True)
                    freeze(batch, path, ["E06"], nonvideo_guard=True,
                           local_dispatch="local-dispatch-v5", catalog_binding=frozen_binding)
                    return 0
                self.assertEqual(registry.get_run("expansion-run")["status"], "running")
                return 9  # Do not start a browser or paid model.
            with patch.object(tool_calling, "ROOT", root), \
                 patch.object(tool_calling, "_execution_dataset", return_value=(path, identity)), \
                 patch.object(tool_calling, "_hash_files", return_value="a" * 64), \
                 patch.object(tool_calling, "_driver_files", return_value=[]):
                result = tool_calling.run_registered(registry, round_data={"id": "round", "title": "expansion",
                    "code_version": "test", "changes": ["agent_shared"]}, run_id="expansion-run", cases=["E06"],
                    allow_paid=True, invoke=invoke, local_dispatch="local-dispatch-v5", catalog_binding=binding)
            run = registry.get_run("expansion-run")
            self.assertEqual(commands, ["freeze", "run"])
            self.assertEqual(result["status"], "failed")
            self.assertEqual(run["suite_id"], tool_calling.EXPANSION_SUITE_ID)
            self.assertEqual(run["metadata"]["source_namespace"], "expansion")
            self.assertEqual(run["manifest_sha256"], tool_calling._sha((batch / "manifest.json").read_bytes()))


if __name__ == "__main__":
    unittest.main()
