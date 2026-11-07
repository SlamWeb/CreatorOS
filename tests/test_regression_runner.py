"""A regression view cannot silently skip cases or forge full coverage."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from creatoros.evaluation import regression_runner


def catalog(scope="partial", disabled=False):
    def case(identifier, enabled=True):
        from creatoros.evaluation.tool_calling import DATASET, _definition_hash
        document = json.loads(DATASET.read_text(encoding="utf-8"))
        definition = next((item for item in document["cases"] if item["id"] == identifier), None)
        return {"instance_id": "base:" + identifier, "case_id": identifier, "case_sha256": _definition_hash(definition) if definition else "c" * 64,
                "resource_class": "routine",
                "execution_readiness": "enabled" if enabled else "requires_review",
                "adapter": "local-dispatch-v2" if enabled else None, "reason": "not yet verified"}
    source = {"schema_version": "regression-catalog-v1", "version": "1"}
    candidates = [case("B07", False)] if scope == "partial" else []
    suite = {"id": "regular-v1", "version": "1", "scope_status": scope,
        "cases": [case("B05"), case("B06", not disabled)], "candidates": candidates,
        "counts": {"pending_candidates": len(candidates), "unclassified_candidates": 0}}
    suite["membership_sha256"] = regression_runner._digest(regression_runner._membership(suite))
    return {"catalog_sha256": regression_runner._digest(source), "catalog_source": source, "suites": [suite]}


class RegressionRunnerTests(unittest.TestCase):
    def test_v3_regression_binds_full_source_and_selected_case_hash(self):
        from creatoros.evaluation import tool_calling
        source = json.loads(tool_calling.REGRESSION_DATASET.read_text(encoding="utf-8"))
        definition = next(row for row in source["cases"] if row["id"] == "B08")
        data = catalog()
        from creatoros.evaluation.benchmark.natural_suite import canonical_digest
        digest = canonical_digest(source)
        data["catalog_source"]["sources"] = [{"namespace": "base", "dataset_id": tool_calling.REGRESSION_SUITE_ID,
                                               "canonical_sha256": digest}]
        data["catalog_sha256"] = regression_runner._digest(data["catalog_source"])
        suite = data["suites"][0]
        suite["cases"][0].update(instance_id="base:B08", case_id="B08",
                                 case_sha256=tool_calling._definition_hash(definition), adapter="local-dispatch-v3")
        suite["membership_sha256"] = regression_runner._digest(regression_runner._membership(suite))
        with patch.object(regression_runner, "catalog", return_value=data):
            binding = regression_runner.prepare("regular-v1", ["base:B08"], ["agent_shared"])["catalog_binding"]
            dataset, meta = tool_calling.suite_and_metadata(["B08"], local_dispatch="local-dispatch-v3", catalog_binding=binding)
            self.assertEqual(dataset["dataset_id"], tool_calling.REGRESSION_SUITE_ID)
            self.assertEqual(meta, tool_calling.suite_and_metadata(["B08"], local_dispatch=True, catalog_binding=binding)[1])
            snapshot_path, _ = tool_calling._execution_dataset("local-dispatch-v3", binding)
            self.assertEqual(meta["dataset_sha256"], tool_calling._sha(snapshot_path.read_bytes()))
            suite["cases"][0]["case_sha256"] = "f" * 64
            suite["membership_sha256"] = regression_runner._digest(regression_runner._membership(suite))
            forged = regression_runner.prepare("regular-v1", ["base:B08"], ["agent_shared"])["catalog_binding"]
            with self.assertRaisesRegex(ValueError, "case definition differs"):
                tool_calling.suite_and_metadata(["B08"], local_dispatch="local-dispatch-v3", catalog_binding=forged)

    def test_v3_frozen_source_rejects_missing_corrupt_or_foreign_snapshot(self):
        from creatoros.evaluation import tool_calling
        from creatoros.evaluation.benchmark.natural_suite import canonical_digest
        source = json.loads(tool_calling.REGRESSION_DATASET.read_text(encoding="utf-8"))
        digest = canonical_digest(source)
        binding = {"frozen_catalog": {"source": {"sources": [{"namespace": "base",
            "dataset_id": tool_calling.REGRESSION_SUITE_ID, "canonical_sha256": digest}]}}}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            snapshot = root / "docs/agent-eval/source-snapshots-v1" / f"{digest}.json"
            snapshot.parent.mkdir(parents=True)
            with patch.object(tool_calling, "ROOT", root):
                with self.assertRaises(ValueError):
                    tool_calling._execution_dataset("local-dispatch-v3", binding)
                snapshot.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
                self.assertEqual(tool_calling._execution_dataset("local-dispatch-v3", binding)[0], snapshot)
                source["cases"][0]["title"] += " changed"
                snapshot.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "digest"):
                    tool_calling._execution_dataset("local-dispatch-v3", binding)
                binding["frozen_catalog"]["source"]["sources"][0]["dataset_id"] = "foreign"
                with self.assertRaisesRegex(ValueError, "identity"):
                    tool_calling._execution_dataset("local-dispatch-v3", binding)

    def test_v1_v2_historical_catalogs_preserve_old_dispatch(self):
        for version in ("v1", "v2"):
            prepared = regression_runner.prepare("regular-" + version, ["base:B05"], ["agent_shared"])
            self.assertEqual(prepared["requires_local_dispatch"], "local-dispatch-" + version)
            self.assertFalse(prepared["blocked"])

    def test_historical_dispatch_uses_original_byte_identical_snapshot(self):
        from creatoros.evaluation import tool_calling
        original = tool_calling.DATASET.read_bytes()
        with tempfile.TemporaryDirectory() as temp:
            shared = Path(temp) / "mutable.json"
            shared.write_bytes(original)
            for version in ("v1", "v2"):
                binding = regression_runner.prepare("regular-" + version, ["base:B05"], ["agent_shared"])["catalog_binding"]
                path, dataset_id = tool_calling._execution_dataset("local-dispatch-" + version, binding)
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(dataset_id, tool_calling.SUITE_ID)
                shared.write_text('{"dataset_id":"forged","cases":[]}', encoding="utf-8")
                with patch.object(tool_calling, "DATASET", shared):
                    actual, metadata = tool_calling.suite_and_metadata(["B05"],
                        local_dispatch="local-dispatch-" + version, catalog_binding=binding)
                self.assertEqual(actual["dataset_id"], tool_calling.SUITE_ID)
                self.assertEqual(metadata["dataset_sha256"], tool_calling._sha(original))

    def test_v3_summary_rejects_cross_dataset_batch_and_tampered_claim(self):
        from creatoros.evaluation import tool_calling
        from creatoros.evaluation.benchmark import nonvideo_guard
        from creatoros.evaluation.benchmark import report as report_module
        record_id = "a" * 32
        with tempfile.TemporaryDirectory() as temp:
            batch = Path(temp)
            (batch / "records" / record_id).mkdir(parents=True)
            (batch / "claims").mkdir()
            manifest = {"dataset_id": tool_calling.REGRESSION_SUITE_ID, "selected_case_ids": ["B08"],
                        "model_configuration": {"benchmark_run_id": "batch"}, "dataset_snapshot": {"sha256": "source"}}
            raw = json.dumps(manifest).encode()
            (batch / "manifest.json").write_bytes(raw)
            digest = tool_calling._sha(raw)
            expected = {"benchmark_batch_id": "batch", "benchmark_manifest_sha256": digest}
            claim = {"case_id": "B08", "run_id": record_id, "first_attempt": True, **expected}
            saved = {"case_id": "B08", "run_id": record_id, "dataset_id": tool_calling.REGRESSION_SUITE_ID, **expected}
            row = {"case_id": "B08", "record_paths": [f"records/{record_id}/report.json"],
                   "benchmark_guard": {"status": "passed"}, "status": "needs_review", "observed_error": None}
            (batch / "summary.json").write_text(json.dumps({"dataset_id": tool_calling.REGRESSION_SUITE_ID, "cases": [row]}))
            class Registry:
                def __init__(self): self.added = []
                def get_run(self, _id): return {"manifest_sha256": digest, "suite_id": tool_calling.REGRESSION_SUITE_ID,
                    "planned_cases": ["B08"], "metadata": {"dataset_sha256": "source"}}
                def add_case_result(self, _id, result): self.added.append(result)
            registry = Registry()
            def write():
                (batch / "claims/B08.json").write_text(json.dumps(claim))
                (batch / "records" / record_id / "report.json").write_text(json.dumps(saved))
            with patch.object(nonvideo_guard, "validate", return_value=True), \
                 patch.object(nonvideo_guard, "evidence", return_value={"status": "passed"}), \
                 patch.object(report_module, "_safety", return_value=([], False)), \
                 patch.object(report_module, "_case_status", return_value="needs_review"), \
                 patch.object(report_module, "_error_kind", return_value=None):
                write()
                tool_calling.sync_summary(registry, "batch", batch, {"B08": {"id": "B08"}}, dataset_id=tool_calling.REGRESSION_SUITE_ID)
                self.assertEqual(len(registry.added), 1)
                for key, value in (("dataset_id", "foreign"), ("benchmark_batch_id", "another"),
                                   ("benchmark_manifest_sha256", "b" * 64)):
                    old = saved[key]; saved[key] = value; write()
                    with self.assertRaisesRegex(ValueError, "Report does not match"):
                        tool_calling.sync_summary(registry, "batch", batch, {"B08": {"id": "B08"}}, dataset_id=tool_calling.REGRESSION_SUITE_ID)
                    saved[key] = old
                claim["run_id"] = "c" * 32; write()
                with self.assertRaisesRegex(ValueError, "claim"):
                    tool_calling.sync_summary(registry, "batch", batch, {"B08": {"id": "B08"}}, dataset_id=tool_calling.REGRESSION_SUITE_ID)

    def test_all_admitted_members_still_partial_when_unclassified_candidates_exist(self):
        with patch.object(regression_runner, "catalog", return_value=catalog()):
            prepared = regression_runner.prepare("regular-v1")
        self.assertEqual(prepared["catalog_binding"]["selection_kind"], "partial")
        self.assertEqual(prepared["catalog_binding"]["pending_candidates"], 1)
        self.assertEqual(prepared["catalog_binding"]["unclassified_candidates"], 0)
        self.assertTrue(prepared["requires_local_dispatch"])
        self.assertEqual(prepared["selected_cases"], ["B05", "B06"])

    def test_complete_requires_every_member_and_no_remaining_gap(self):
        with patch.object(regression_runner, "catalog", return_value=catalog("complete")):
            complete = regression_runner.prepare("regular-v1")
            subset = regression_runner.prepare("regular-v1", ["base:B05"])
        self.assertEqual(complete["catalog_binding"]["selection_kind"], "complete")
        self.assertEqual(subset["catalog_binding"]["selection_kind"], "partial")

    def test_guard_policy_follows_adapter_and_mixed_versions_are_rejected(self):
        from creatoros.evaluation import tool_calling
        data = catalog()
        suite = data["suites"][0]
        suite["cases"][0]["adapter"] = "local-dispatch-v1"
        suite["membership_sha256"] = regression_runner._digest(regression_runner._membership(suite))
        with patch.object(regression_runner, "catalog", return_value=data):
            self.assertTrue(regression_runner.prepare("regular-v1")["requires_separate_runs"])
            with self.assertRaises(ValueError):
                regression_runner.prepare("regular-v1", ["base:B05", "base:B06"])
            v1 = regression_runner.prepare("regular-v1", ["base:B05"])
            self.assertEqual(v1["requires_local_dispatch"], "local-dispatch-v1")
            binding = v1["catalog_binding"]
            self.assertEqual(tool_calling.suite_and_metadata(["B05"], local_dispatch="local-dispatch-v1", catalog_binding=binding)[1]["guard_version"], "local-dispatch-v1")
            with self.assertRaises(ValueError):
                tool_calling.suite_and_metadata(["B05"], local_dispatch="local-dispatch-v2", catalog_binding=binding)

    def test_blocked_and_forged_binding_fail_closed(self):
        with patch.object(regression_runner, "catalog", return_value=catalog(disabled=True)):
            prepared = regression_runner.prepare("regular-v1")
            self.assertEqual(prepared["blocked"][0]["instance_id"], "base:B06")
            with self.assertRaises(ValueError):
                regression_runner.validate_binding(["B05", "B06"], prepared["catalog_binding"])
        with patch.object(regression_runner, "catalog", return_value=catalog()):
            prepared = regression_runner.prepare("regular-v1", ["base:B05"])
            binding = prepared["catalog_binding"]
            self.assertEqual(regression_runner.validate_binding(["B05"], binding), {"catalog_binding": binding})
            for field, value in (("selection_kind", "complete"), ("membership_sha256", "c" * 64),
                                 ("catalog_sha256", "d" * 64)):
                forged = copy.deepcopy(binding)
                forged[field] = value
                with self.assertRaises(ValueError):
                    regression_runner.validate_binding(["B05"], forged)
            with self.assertRaises(ValueError):
                regression_runner.validate_binding(["B06"], binding)

    def test_unknown_or_duplicate_instance_is_not_run(self):
        with patch.object(regression_runner, "catalog", return_value=catalog()):
            for selected in ([], ["base:B05", "base:B05"], ["natural:B05"]):
                with self.assertRaises(ValueError):
                    regression_runner.prepare("regular-v1", selected)

    def test_frozen_snapshot_survives_catalog_extension_but_tampering_is_rejected(self):
        old = catalog()
        with patch.object(regression_runner, "catalog", return_value=old):
            binding = regression_runner.prepare("regular-v1", ["base:B05"], ["agent_shared"])["catalog_binding"]
            self.assertEqual(regression_runner.validate_frozen_binding(["B05"], binding), {"catalog_binding": binding})
        changed = copy.deepcopy(old)
        changed["catalog_source"]["version"] = "2"
        changed["catalog_sha256"] = regression_runner._digest(changed["catalog_source"])
        with patch.object(regression_runner, "catalog", return_value=changed):
            with self.assertRaises(ValueError):
                regression_runner.validate_binding(["B05"], binding)
            self.assertEqual(regression_runner.validate_frozen_binding(["B05"], binding), {"catalog_binding": binding})
        tampered = copy.deepcopy(binding)
        tampered["frozen_catalog"]["membership"]["cases"][0]["case_sha256"] = "d" * 64
        with self.assertRaises(ValueError):
            regression_runner.validate_frozen_binding(["B05"], tampered)

    def test_regression_does_not_relabel_context_change_as_tool(self):
        from creatoros.evaluation import tool_calling
        with patch.object(regression_runner, "catalog", return_value=catalog()):
            binding = regression_runner.prepare("regular-v1", ["base:B05"], ["agent_shared"])["catalog_binding"]
            _dataset, metadata = tool_calling.suite_and_metadata(["B05"], local_dispatch="local-dispatch-v2", catalog_binding=binding)
            self.assertEqual(metadata["change_scope"], "agent_shared")
            self.assertEqual(metadata["suite_role"], "regression")
            self.assertEqual(metadata["change_requirements"]["declared_scopes"], ["agent_shared"])
            with self.assertRaises(ValueError):
                tool_calling.suite_and_metadata(["B05"], local_dispatch=False, catalog_binding=binding)

    def test_record_and_frozen_manifest_bind_exact_catalog_before_model(self):
        from creatoros.evaluation import tool_calling
        from creatoros.evaluation.registry import EvalRegistry
        with patch.object(regression_runner, "catalog", return_value=catalog()):
            binding = regression_runner.prepare("regular-v1", ["base:B05"])["catalog_binding"]
        dataset = {"dataset_id": tool_calling.SUITE_ID, "cases": [{"id": "B05", "steps": []}]}
        meta = {"catalog_binding": binding, "requires_manifest_binding": True}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reg = EvalRegistry(root / "registry.sqlite3")
            batch = root / "data/agent-benchmark-v2/run01"
            invocations = []
            def invoke(args):
                invocations.append(args)
                if args[3] == "freeze":
                    self.assertEqual(json.loads(Path(args[args.index("--catalog-binding-file") + 1]).read_text(encoding="utf-8")), binding)
                    batch.mkdir(parents=True)
                    raw = json.dumps({"model_configuration": {"catalog_binding": binding}}).encode()
                    (batch / "manifest.json").write_bytes(raw)
                    (batch / "manifest.json.sha256").write_text(tool_calling._sha(raw), encoding="ascii")
                    return 0
                return 3
            with patch.object(tool_calling, "ROOT", root), patch.object(tool_calling, "_execution_dataset", return_value=(tool_calling.DATASET, tool_calling.SUITE_ID)), patch.object(tool_calling, "suite_and_metadata", return_value=(dataset, meta)):
                outcome = tool_calling.run_registered(reg, round_data={"id": "round", "title": "test", "code_version": "sha", "changes": binding["change_scopes"]},
                    run_id="run01", cases=["B05"], allow_paid=True, local_dispatch="local-dispatch-v2",
                    catalog_binding=binding, invoke=invoke)
            self.assertEqual(outcome["status"], "failed")
            self.assertIn("--local-dispatch-guard", invocations[0])
            self.assertIn("--catalog-binding-file", invocations[0])
            recorded = reg.get_run("run01")
            self.assertEqual(recorded["metadata"]["catalog_binding"], binding)
            self.assertIsNotNone(recorded["manifest_sha256"])

    def test_incorrect_frozen_catalog_never_starts_model(self):
        from creatoros.evaluation import tool_calling
        from creatoros.evaluation.registry import EvalRegistry
        with patch.object(regression_runner, "catalog", return_value=catalog()):
            binding = regression_runner.prepare("regular-v1", ["base:B05"])["catalog_binding"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = root / "data/agent-benchmark-v2/run01"
            reg = EvalRegistry(root / "registry.sqlite3")
            invocations = []
            def invoke(args):
                invocations.append(args)
                batch.mkdir(parents=True)
                raw = b'{"model_configuration":{"catalog_binding":{"forged":true}}}'
                (batch / "manifest.json").write_bytes(raw)
                (batch / "manifest.json.sha256").write_text(tool_calling._sha(raw), encoding="ascii")
                return 0
            metadata = {"catalog_binding": binding, "requires_manifest_binding": True}
            with patch.object(tool_calling, "ROOT", root), patch.object(tool_calling, "_execution_dataset", return_value=(tool_calling.DATASET, tool_calling.SUITE_ID)), patch.object(tool_calling, "suite_and_metadata", return_value=({"dataset_id": tool_calling.SUITE_ID, "cases":[{"id":"B05"}]}, metadata)):
                with self.assertRaisesRegex(ValueError, "catalog binding"):
                    tool_calling.run_registered(reg, round_data={"id": "round", "title": "test", "code_version": "sha", "changes": binding["change_scopes"]},
                        run_id="run01", cases=["B05"], allow_paid=True, local_dispatch="local-dispatch-v2",
                        catalog_binding=binding, invoke=invoke)
            self.assertEqual(len(invocations), 1)
            self.assertIsNone(reg.get_run("run01")["manifest_sha256"])


if __name__ == "__main__":
    unittest.main()
