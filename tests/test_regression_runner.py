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
        return {"instance_id": "base:" + identifier, "case_id": identifier, "case_sha256": "c" * 64,
                "resource_class": "routine",
                "execution_readiness": "enabled" if enabled else "requires_review",
                "adapter": "tool-calling-v1" if enabled else None, "reason": "not yet verified"}
    source = {"schema_version": "regression-catalog-v1", "version": "1"}
    candidates = [case("B07", False)] if scope == "partial" else []
    suite = {"id": "regular-v1", "version": "1", "scope_status": scope,
        "cases": [case("B05"), case("B06", not disabled)], "candidates": candidates,
        "counts": {"pending_candidates": len(candidates), "unclassified_candidates": 0}}
    suite["membership_sha256"] = regression_runner._digest(regression_runner._membership(suite))
    return {"catalog_sha256": regression_runner._digest(source), "catalog_source": source, "suites": [suite]}


class RegressionRunnerTests(unittest.TestCase):
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
            _dataset, metadata = tool_calling.suite_and_metadata(["B05"], local_dispatch=True, catalog_binding=binding)
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
        dataset = {"cases": [{"id": "B05", "steps": []}]}
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
            with patch.object(tool_calling, "ROOT", root), patch.object(tool_calling, "suite_and_metadata", return_value=(dataset, meta)):
                outcome = tool_calling.run_registered(reg, round_data={"id": "round", "title": "test", "code_version": "sha", "changes": binding["change_scopes"]},
                    run_id="run01", cases=["B05"], allow_paid=True, local_dispatch=True,
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
            with patch.object(tool_calling, "ROOT", root), patch.object(tool_calling, "suite_and_metadata", return_value=({"cases":[{"id":"B05"}]}, metadata)):
                with self.assertRaisesRegex(ValueError, "catalog binding"):
                    tool_calling.run_registered(reg, round_data={"id": "round", "title": "test", "code_version": "sha", "changes": binding["change_scopes"]},
                        run_id="run01", cases=["B05"], allow_paid=True, local_dispatch=True,
                        catalog_binding=binding, invoke=invoke)
            self.assertEqual(len(invocations), 1)
            self.assertIsNone(reg.get_run("run01")["manifest_sha256"])


if __name__ == "__main__":
    unittest.main()
