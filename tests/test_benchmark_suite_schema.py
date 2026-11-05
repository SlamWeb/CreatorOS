"""Versioned variable-size suites; legacy batches stay readable and immutable."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from creatoros.evaluation.benchmark import manifest
from creatoros.evaluation.benchmark.report import summarize_batch
from creatoros.evaluation.benchmark.store import BenchmarkStore, BenchmarkStoreError
from creatoros.evaluation.benchmark.suite_schema import VERSION, case_ids


def case(identifier, family, split="dev"):
    return {"id": identifier, "title": identifier, "category": "state", "family": family, "split": split,
            "origin": {"type": "synthetic"}, "intent": "Test", "expected_outcome": "Done",
            "evaluation_scope": "outcome", "scoring": {"program_checks": [{"id": "outcome_01", "label": "Done"}],
            "critical_checks": ["no_private_leak"]}, "rubric": [{"id": "r1", "question": "Is it done?"}]}


class SuiteSchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="suite-contract-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dataset = self.root / "cases.json"
        self.source = self.root / "source.py"
        self.source.write_text("VALUE = 1\n", encoding="utf-8")
        self.doc = {"dataset_id": "expanded-test-v1", "suite": {"version": VERSION,
                    "eligible_case_ids": ["C01", "C02", "V05"]},
                    "cases": [case("C01", "f1"), case("C02", "f2"), case("V05", "f3", "acceptance")]}
        self.write_dataset()
        self.root_patch = patch.object(manifest, "PROJECT_ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.git_patch = patch.object(manifest, "_git_state", return_value={"sha": "test"})
        self.git_patch.start()
        self.addCleanup(self.git_patch.stop)
        self.batch = self.root / "batch"
        self.target = self.batch / "manifest.json"

    def write_dataset(self):
        self.dataset.write_text(json.dumps(self.doc), encoding="utf-8")

    def freeze(self, selected=None):
        return manifest.freeze_manifest(self.target, self.dataset, case_ids=selected, source_paths=[self.source],
                pricing={"currency": "USD", "models": {}}, model_configuration={"retries": 0})

    def test_variable_suite_freeze_verify_report_and_store(self):
        saved = self.freeze(["C01", "V05"])
        self.assertEqual(saved["all_case_ids"], ["C01", "C02", "V05"])
        self.assertTrue(manifest.verify_manifest(self.target)["valid"])
        result = summarize_batch(self.batch, self.dataset)
        self.assertEqual(result["planned_case_ids"], ["C01", "V05"])
        self.assertEqual(result["dataset_case_count"], 3)
        self.assertEqual(result["planned"], 2)
        self.assertEqual(result["execution_scope"], "subset")
        store = BenchmarkStore(self.root, cases_path=self.dataset)
        self.assertEqual([c["id"] for c in store.overview()["cases"]], ["C01", "C02", "V05"])
        self.assertEqual(store.detail("batch", "V05")["frozen_definition"]["id"], "V05")
        with self.assertRaises(BenchmarkStoreError):
            store.detail("batch", "C02")
        with self.assertRaises(BenchmarkStoreError):
            store.detail("batch", "../secret")

    def test_duplicate_invalid_and_unselected_cases_fail_closed(self):
        for selected in ([], ["C01", "C01"], ["C01", "C99"]):
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                self.freeze(selected)
        for ids in (["C01", "C01", "V05"], ["C01", "../x", "V05"], ["C01", "C02"]):
            with self.subTest(ids=ids):
                doc = json.loads(json.dumps(self.doc)); doc["suite"]["eligible_case_ids"] = ids
                with self.assertRaises(ValueError): case_ids(doc)

    def test_tampered_manifest_selection_rejected_even_with_receipt_recomputed(self):
        self.freeze(["C01"])
        doc = json.loads(self.target.read_text(encoding="utf-8"))
        doc["selected_case_ids"] = ["C01", "C99"]
        payload = json.dumps(doc).encode()
        self.target.write_bytes(payload)
        self.target.with_name("manifest.json.sha256").write_text(hashlib.sha256(payload).hexdigest(), encoding="ascii")
        with self.assertRaises(manifest.ManifestVerificationError):
            manifest.verify_manifest(self.target)

    def test_suite_metadata_and_family_split_are_strict(self):
        self.doc["cases"][1]["family"] = "f1"
        self.doc["cases"][1]["split"] = "acceptance"
        self.write_dataset()
        with self.assertRaisesRegex(ValueError, "crosses splits"):
            self.freeze()
        self.doc["cases"][1]["family"] = "f2"
        self.doc["suite"]["version"] = "unknown"
        self.write_dataset()
        with self.assertRaises(ValueError):
            self.freeze()

    def test_natural_overview_excludes_compatibility_placeholders(self):
        path = Path(__file__).resolve().parents[1] / "docs/agent-eval/natural-v2/cases.json"
        store = BenchmarkStore(self.root, cases_path=path)
        shown = store.overview()["cases"]
        from creatoros.evaluation.benchmark.natural_suite import NATURAL_IDS
        self.assertEqual({item["id"] for item in shown}, set(NATURAL_IDS))


if __name__ == "__main__": unittest.main()
