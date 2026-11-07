import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from creatoros.evaluation.regression_catalog import DEFAULT_MANIFEST, ROOT, build_catalog, catalog
from creatoros.evaluation.benchmark.natural_suite import canonical_digest


class RegressionCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for relative in ("docs/agent-eval/benchmark-v2/cases.json", "docs/agent-eval/natural-v2/cases.json",
                         "docs/agent-eval/expansion-50-v1/cases.json", "docs/agent-eval/tool-calling-v1/cases.json"):
            dest = self.root / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, dest)
        self.doc = json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8"))

    def tearDown(self):
        self.temp.cleanup()

    def test_current_membership_and_provenance_do_not_double_count(self):
        data = catalog()
        regular, full = data["suites"]
        self.assertEqual(regular["counts"], {"defined": 108, "enabled": 11, "requires_review": 97,
                                             "routine": 108, "heavy": 0, "unknown_resource": 0,
                                             "pending_candidates": 0, "unclassified_candidates": 0})
        self.assertEqual(full["counts"], {"defined": 120, "enabled": 11, "requires_review": 109,
                                          "routine": 108, "heavy": 12, "unknown_resource": 0,
                                          "pending_candidates": 0, "unclassified_candidates": 0})
        self.assertEqual(regular["scope_status"], "partial")
        ids = {case["instance_id"] for case in full["cases"]}
        self.assertEqual(len(ids), 120)
        self.assertNotIn("natural:B01", ids)  # compatibility placeholder
        variant = next(case for case in full["cases"] if case["instance_id"] == "natural:B06")
        self.assertEqual(variant["parent_instance_id"], "base:B06")
        self.assertEqual(variant["execution_readiness"], "requires_review")
        self.assertNotEqual(variant["case_sha256"], next(c for c in full["cases"] if c["instance_id"] == "base:B06")["case_sha256"])

    def test_versioned_catalog_preserves_old_membership_and_adapters(self):
        old = catalog(ROOT / "docs/agent-eval/regression-catalog-v1/catalog.json")
        new = catalog()
        self.assertEqual((old["catalog_id"], new["catalog_id"]),
                         ("creatoros-regression-v1", "creatoros-regression-v2"))
        for before, after in zip(old["suites"], new["suites"]):
            self.assertNotEqual(before["id"], after["id"])
            self.assertNotEqual(before["membership_sha256"], after["membership_sha256"])
            self.assertEqual(before["counts"], after["counts"])
            old_rows = {c["instance_id"]: c for c in before["cases"]}
            new_rows = {c["instance_id"]: c for c in after["cases"]}
            self.assertEqual(set(old_rows), set(new_rows))
            for key, row in old_rows.items():
                updated = new_rows[key]
                self.assertEqual(row["case_sha256"], updated["case_sha256"])
                self.assertEqual(updated["adapter"],
                                 "local-dispatch-v2" if row["adapter"] == "local-dispatch-v1" else row["adapter"])

    def test_source_digest_and_path_fail_closed(self):
        altered = self.root / "docs/agent-eval/benchmark-v2/cases.json"
        payload = json.loads(altered.read_text(encoding="utf-8"))
        payload["cases"][0]["title"] = "modified"
        altered.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "digest"):
            build_catalog(self.doc, repo_root=self.root)
        self.doc["sources"][0]["path"] = "docs/agent-eval/../../secrets.json"
        with self.assertRaisesRegex(ValueError, "source path"):
            build_catalog(self.doc, repo_root=self.root)

    def test_duplicates_unknown_members_and_forged_adapter_rejected(self):
        self.doc["suites"][0].pop("include_resource_classes")
        cases = self.doc["suites"][0]["instances"] = ["base:B05"]
        cases.append(cases[0])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            build_catalog(self.doc, repo_root=self.root)
        cases.pop()
        cases.append("base:DOES_NOT_EXIST")
        with self.assertRaisesRegex(ValueError, "unknown"):
            build_catalog(self.doc, repo_root=self.root)
        cases.pop()
        self.doc["classifications"]["base:B04"] = {"resource_class": "routine", "adapter": "local-dispatch-v1", "reason": "forged"}
        with self.assertRaisesRegex(ValueError, "adapter"):
            build_catalog(self.doc, repo_root=self.root)

    def test_new_source_and_suite_are_extensible_but_not_assumed_runnable(self):
        expansion = json.loads((self.root / "docs/agent-eval/expansion-50-v1/cases.json").read_text(encoding="utf-8"))
        case = copy.deepcopy(expansion["cases"][0])
        case.update(id="NEW01", family="new_unique_family", split="dev", title="New controlled task")
        new_doc = {"schema_version": 3, "dataset_id": "example-new-v1",
                   "suite": {"version": "benchmark-suite-v1", "eligible_case_ids": ["NEW01"]}, "cases": [case]}
        self.doc["version"] = "2"
        self.doc["suites"][1]["version"] = "2"
        relative = "docs/agent-eval/new-suite-v1/cases.json"
        target = self.root / relative
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps(new_doc, ensure_ascii=False), encoding="utf-8")
        self.doc["sources"].append({"namespace": "new", "dataset_id": new_doc["dataset_id"], "path": relative,
                                    "canonical_sha256": canonical_digest(new_doc), "selection": "suite_eligible"})
        self.doc["suites"].append({"id": "new-only", "version": "1.0", "title": "New suite", "instances": ["new:NEW01"]})
        result = build_catalog(self.doc, repo_root=self.root)
        item = result["suites"][-1]["cases"][0]
        self.assertEqual(item["execution_readiness"], "requires_review")
        self.assertEqual(item["resource_class"], "unknown")
        self.assertEqual(result["suites"][-1]["scope_status"], "partial")
        self.assertEqual(result["suites"][1]["counts"]["defined"], 121)

    def test_variant_parent_and_split_must_be_consistent(self):
        self.doc["sources"][1]["parent_namespace"] = "expansion"
        with self.assertRaisesRegex(ValueError, "parent"):
            build_catalog(self.doc, repo_root=self.root)
        self.doc["sources"][1]["parent_namespace"] = "base"
        self.doc["sources"][0]["parent_namespace"] = "natural"
        with self.assertRaisesRegex(ValueError, "cyclic"):
            build_catalog(self.doc, repo_root=self.root)

    def test_suites_and_catalog_are_pinned_by_content(self):
        original = build_catalog(self.doc, repo_root=self.root)
        again = build_catalog(copy.deepcopy(self.doc), repo_root=self.root)
        self.assertEqual(original["catalog_sha256"], again["catalog_sha256"])
        self.assertEqual(original["suites"][0]["membership_sha256"], again["suites"][0]["membership_sha256"])
        self.doc["classification_groups"][0]["resource_class"] = "unknown"
        changed = build_catalog(self.doc, repo_root=self.root)
        self.assertNotEqual(original["catalog_sha256"], changed["catalog_sha256"])
        self.assertNotEqual(original["suites"][0]["membership_sha256"], changed["suites"][0]["membership_sha256"])

    def test_same_dataset_cannot_be_registered_under_a_second_namespace(self):
        duplicate = copy.deepcopy(self.doc["sources"][0])
        duplicate["namespace"] = "duplicate"
        self.doc["sources"].append(duplicate)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            build_catalog(self.doc, repo_root=self.root)

    def test_unverified_case_with_no_codex_flag_is_not_enabled(self):
        full = catalog()["suites"][1]["cases"]
        case = next(item for item in full if item["instance_id"] == "base:B08")
        self.assertFalse(case["live_codex_declared"])
        self.assertEqual(case["execution_readiness"], "requires_review")
        self.assertEqual(case["resource_class"], "routine")

    def test_each_routine_case_has_traceable_basis_and_danger_is_not_hidden(self):
        regular = catalog()["suites"][0]["cases"]
        self.assertTrue(all(item["classification_evidence"] and item["scenario"] in item["reason"] for item in regular))
        self.assertTrue(all((ROOT / path).is_file() for item in regular for path in item["classification_evidence"]))
        image = next(item for item in regular if item["instance_id"] == "base:B29")
        self.assertTrue(any("视觉" in item for item in image["dependencies"]))
        production = next(item for item in regular if item["instance_id"] == "base:B34")
        self.assertEqual(production["execution_readiness"], "requires_review")
        self.assertTrue(any("付费" in item for item in production["side_effects"]))

    def test_overlapping_classification_groups_are_rejected(self):
        self.doc["classification_groups"][1]["instances"].append(self.doc["classification_groups"][0]["instances"][0])
        with self.assertRaisesRegex(ValueError, "duplicate classification"):
            build_catalog(self.doc, repo_root=self.root)

    def test_new_source_may_not_split_existing_family(self):
        base = json.loads((self.root / "docs/agent-eval/benchmark-v2/cases.json").read_text(encoding="utf-8"))["cases"][0]
        expansion = json.loads((self.root / "docs/agent-eval/expansion-50-v1/cases.json").read_text(encoding="utf-8"))
        case = copy.deepcopy(expansion["cases"][0])
        case.update(id="NEW01", family=base["family"], split="dev" if base["split"] == "acceptance" else "acceptance")
        data = {"schema_version": 3, "dataset_id": "example-new-v1",
                "suite": {"version": "benchmark-suite-v1", "eligible_case_ids": ["NEW01"]}, "cases": [case]}
        relative = "docs/agent-eval/new-suite-v1/cases.json"
        target = self.root / relative
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.doc["sources"].append({"namespace": "new", "dataset_id": data["dataset_id"], "path": relative,
                                    "canonical_sha256": canonical_digest(data), "selection": "suite_eligible"})
        with self.assertRaisesRegex(ValueError, "family crosses"):
            build_catalog(self.doc, repo_root=self.root)


if __name__ == "__main__":
    unittest.main()
