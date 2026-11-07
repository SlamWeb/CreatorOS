import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from creatoros.evaluation.regression_catalog import DEFAULT_MANIFEST, ROOT, build_catalog, catalog, freeze_sources
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
        shutil.copytree(ROOT / "docs/agent-eval/source-snapshots-v1", self.root / "docs/agent-eval/source-snapshots-v1")
        self.doc = json.loads((ROOT / "docs/agent-eval/regression-catalog-v2/catalog.json").read_text(encoding="utf-8"))

    def tearDown(self):
        self.temp.cleanup()

    def test_current_membership_and_provenance_do_not_double_count(self):
        data = catalog(ROOT / "docs/agent-eval/regression-catalog-v2/catalog.json")
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
        new = catalog(ROOT / "docs/agent-eval/regression-catalog-v2/catalog.json")
        self.assertEqual((old["catalog_id"], new["catalog_id"]),
                         ("creatoros-regression-v1", "creatoros-regression-v2"))
        self.assertEqual(old["catalog_sha256"], "51e070beff5e9297a3f53e24dce7fa4afe3cdfafda32a965fbf1b62e0352f54c")
        self.assertEqual(new["catalog_sha256"], "ee4d021c69e95a8f74fbac474303c9adabf0ff12d4cae80ad4488c6dce0c37c3")
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
        altered = self.root / "docs/agent-eval/source-snapshots-v1" / (self.doc["sources"][0]["canonical_sha256"] + ".json")
        payload = json.loads(altered.read_text(encoding="utf-8"))
        payload["cases"][0]["title"] = "modified"
        altered.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "digest"):
            build_catalog(self.doc, repo_root=self.root)

    def test_published_sources_and_adapter_remain_available_after_shared_changes(self):
        old_doc = json.loads((ROOT / "docs/agent-eval/regression-catalog-v1/catalog.json").read_text(encoding="utf-8"))
        before = [build_catalog(doc, repo_root=self.root) for doc in (old_doc, self.doc)]
        for relative in ("docs/agent-eval/benchmark-v2/cases.json", "docs/agent-eval/natural-v2/cases.json",
                         "docs/agent-eval/expansion-50-v1/cases.json", "docs/agent-eval/tool-calling-v1/cases.json"):
            path = self.root / relative
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["cases"][0]["title"] += " changed later"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        after = [build_catalog(doc, repo_root=self.root) for doc in (old_doc, self.doc)]
        self.assertEqual(before, after)
        (self.root / "docs/agent-eval/benchmark-v2/cases.json").unlink()
        self.assertEqual(before, [build_catalog(doc, repo_root=self.root) for doc in (old_doc, self.doc)])
        # Historical display depends on frozen hashes, not later run-validator semantics.
        with patch("creatoros.evaluation.regression_catalog._dataset", side_effect=ValueError("future validator")):
            self.assertEqual(before, [build_catalog(doc, repo_root=self.root) for doc in (old_doc, self.doc)])

    def test_published_snapshot_tampering_and_missing_adapter_fail_closed(self):
        frozen = self.root / "docs/agent-eval/source-snapshots-v1"
        adapter = frozen / "72f4ac13ac545ff1d681e143819d521fcf035db304129b0890c1bb01bb588c9e.json"
        payload = json.loads(adapter.read_text(encoding="utf-8"))
        payload["cases"][0]["title"] += " altered"
        adapter.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "snapshot digest"):
            build_catalog(self.doc, repo_root=self.root)
        adapter.unlink()
        with self.assertRaises(FileNotFoundError):
            build_catalog(self.doc, repo_root=self.root)

    def test_v3_adapter_uses_frozen_base_and_cannot_widen_v1_or_v2(self):
        doc = copy.deepcopy(self.doc)
        doc["adapter_source_sha256"] = doc["sources"][0]["canonical_sha256"]
        group = next(group for group in doc["classification_groups"] if "base:B08" in group["instances"])
        group["instances"].remove("base:B08")
        doc["classifications"]["base:B08"] = {"resource_class": "routine", "adapter": "local-dispatch-v3", "reason": "qualified"}
        case = next(row for row in build_catalog(doc, repo_root=self.root)["suites"][0]["cases"] if row["instance_id"] == "base:B08")
        self.assertEqual(case["execution_readiness"], "enabled")
        for adapter in ("local-dispatch-v1", "local-dispatch-v2"):
            doc["classifications"]["base:B08"]["adapter"] = adapter
            with self.assertRaisesRegex(ValueError, "adapter"):
                build_catalog(doc, repo_root=self.root)
        doc["classifications"]["base:B08"]["adapter"] = "local-dispatch-v3"
        doc["adapter_source_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "adapter"):
            build_catalog(doc, repo_root=self.root)

    def test_v3_catalog_cannot_authorize_its_own_altered_baseline(self):
        doc = json.loads((ROOT / "docs/agent-eval/regression-catalog-v3/catalog.json").read_text(encoding="utf-8"))
        self.assertEqual(next(c for c in build_catalog(doc, repo_root=self.root)["suites"][0]["cases"]
                              if c["instance_id"] == "base:B25")["execution_readiness"], "enabled")
        snapshot_dir = self.root / "docs/agent-eval/source-snapshots-v1"
        base = json.loads((self.root / doc["sources"][0]["path"]).read_text(encoding="utf-8"))
        next(c for c in base["cases"] if c["id"] == "B25")["title"] += " altered task"
        changed = canonical_digest(base)
        (snapshot_dir / (changed + ".json")).write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
        doc["sources"][0]["canonical_sha256"] = changed
        doc["adapter_source_sha256"] = changed
        # Keep natural-parent provenance and both content-addressed snapshots
        # internally consistent, so the authorization check is the rejection.
        natural = json.loads((self.root / doc["sources"][1]["path"]).read_text(encoding="utf-8"))
        natural["natural_selection_policy"]["parent_canonical_sha256"] = changed
        natural_sha = canonical_digest(natural)
        (snapshot_dir / (natural_sha + ".json")).write_text(json.dumps(natural, ensure_ascii=False), encoding="utf-8")
        doc["sources"][1]["canonical_sha256"] = natural_sha
        with self.assertRaisesRegex(ValueError, "v3 adapter requires its exact frozen base source digest"):
            build_catalog(doc, repo_root=self.root)

    def test_v4_natural_qualification_is_namespace_bound_and_cannot_self_authorize(self):
        doc = json.loads((ROOT / "docs/agent-eval/regression-catalog-v4/catalog.json").read_text(encoding="utf-8"))
        rows = {c["instance_id"]: c for c in build_catalog(doc, repo_root=self.root)["suites"][0]["cases"]}
        self.assertEqual(rows["natural:B06"]["execution_readiness"], "enabled")
        self.assertEqual(rows["natural:B07"]["execution_readiness"], "enabled")
        self.assertEqual(rows["natural:B05"]["execution_readiness"], "requires_review")
        forged = copy.deepcopy(doc)
        forged["classifications"]["natural:B06"]["adapter"] = "local-dispatch-v3"
        with self.assertRaisesRegex(ValueError, "ineligible execution adapter"):
            build_catalog(forged, repo_root=self.root)
        forged = copy.deepcopy(doc)
        group = next(g for g in forged["classification_groups"] if "natural:B05" in g["instances"])
        group["instances"].remove("natural:B05")
        forged["classifications"]["natural:B05"] = {"resource_class": "routine", "adapter": "local-dispatch-v4", "reason": "forged"}
        with self.assertRaisesRegex(ValueError, "adapter does not authorize"):
            build_catalog(forged, repo_root=self.root)
        forged = copy.deepcopy(doc)
        natural = json.loads((self.root / forged["sources"][1]["path"]).read_text(encoding="utf-8"))
        next(c for c in natural["cases"] if c["id"] == "B06")["title"] += " changed"
        digest = canonical_digest(natural)
        (self.root / "docs/agent-eval/source-snapshots-v1" / (digest + ".json")).write_text(
            json.dumps(natural, ensure_ascii=False), encoding="utf-8")
        forged["sources"][1]["canonical_sha256"] = digest
        forged["adapter_source_sha256_by_namespace"]["natural"] = digest
        with self.assertRaisesRegex(ValueError, "v4 adapter requires its exact fixed source digests"):
            build_catalog(forged, repo_root=self.root)

    def test_freeze_sources_is_content_addressed_and_does_not_overwrite(self):
        self.doc["catalog_id"] = "new-catalog"
        digest = self.doc["sources"][2]["canonical_sha256"]
        target = self.root / "docs/agent-eval/source-snapshots-v1" / (digest + ".json")
        target.unlink()
        self.assertIn(digest, freeze_sources(self.doc, repo_root=self.root))
        original = target.read_bytes()
        self.assertIn(digest, freeze_sources(self.doc, repo_root=self.root))
        self.assertEqual(target.read_bytes(), original)
        target.write_text('{}', encoding="utf-8")
        with self.assertRaises((ValueError, KeyError)):
            freeze_sources(self.doc, repo_root=self.root)
        self.assertEqual(target.read_text(encoding="utf-8"), '{}')
        self.doc["sources"][0]["path"] = "docs/agent-eval/../../secrets.json"
        with self.assertRaisesRegex(ValueError, "source path"):
            build_catalog(self.doc, repo_root=self.root)

    def test_sealed_catalog_requires_snapshots_but_can_be_explicitly_frozen(self):
        doc = copy.deepcopy(self.doc)
        doc["catalog_id"] = "new-sealed-catalog"
        doc["sources_frozen"] = True
        digest = doc["sources"][2]["canonical_sha256"]
        target = self.root / "docs/agent-eval/source-snapshots-v1" / (digest + ".json")
        target.unlink()
        with self.assertRaises(FileNotFoundError):
            build_catalog(doc, repo_root=self.root)
        freeze_sources(doc, repo_root=self.root)
        self.assertEqual(target.exists(), True)
        self.assertEqual(build_catalog(doc, repo_root=self.root)["catalog_id"], "new-sealed-catalog")
        self.assertEqual(list(target.parent.glob('.source-*.tmp')), [])

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
        full = catalog(ROOT / "docs/agent-eval/regression-catalog-v2/catalog.json")["suites"][1]["cases"]
        case = next(item for item in full if item["instance_id"] == "base:B08")
        self.assertFalse(case["live_codex_declared"])
        self.assertEqual(case["execution_readiness"], "requires_review")
        self.assertEqual(case["resource_class"], "routine")

    def test_each_routine_case_has_traceable_basis_and_danger_is_not_hidden(self):
        regular = catalog(ROOT / "docs/agent-eval/regression-catalog-v2/catalog.json")["suites"][0]["cases"]
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
