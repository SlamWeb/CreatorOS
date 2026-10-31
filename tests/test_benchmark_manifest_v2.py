"""Freeze/verify component tests; temporary code only, no model or live service."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from creatoros.evaluation.benchmark import manifest


class BenchmarkManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="benchmark-manifest-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.object(manifest, "PROJECT_ROOT", self.root).start()
        patch.object(manifest, "_git_state", return_value={"sha": "fixed-head", "dirty": True,
            "dirty_paths": [" M unrelated-bgm.py"], "status_available": True}).start()
        self.source = self.root / "source.py"
        self.source.write_text("VALUE = 1\n", encoding="utf-8")
        self.dataset = self.root / "cases.json"
        self.cases = [{"id": f"B{n:02}", "family": f"family-{n}", "split": "acceptance" if n <= 10 else "dev",
                       "origin": {"type": "trace" if n % 2 else "adversarial_variant"}} for n in range(1, 51)]
        self.write_dataset()
        self.target = self.root / "frozen/manifest.json"
        self.pricing = {"currency": "USD", "models": {"deepseek-flash": {"output_per_million": [0.6, 1.2]}}}
        self.configuration = {"provider_request_model": "deepseek-flash", "codex_model": "gpt-6-luna", "retries": 0}

    def write_dataset(self):
        self.dataset.write_text(json.dumps({"dataset_id": "test-only", "cases": self.cases}), encoding="utf-8")

    def freeze(self, **kwargs):
        return manifest.freeze_manifest(self.target, self.dataset, pricing=self.pricing,
            model_configuration=self.configuration, source_paths=[self.source], **kwargs)

    def test_freeze_locks_all_50_while_selecting_subset_and_records_dirty_state(self):
        result = self.freeze(case_ids=["B11", "B41"])
        self.assertEqual(result["selected_case_ids"], ["B11", "B41"])
        self.assertEqual(len(result["case_groups"]), 50)
        self.assertTrue(result["git"]["dirty"])
        self.assertEqual(result["git"]["dirty_paths"], [" M unrelated-bgm.py"])
        checked = manifest.verify_manifest(self.target)
        self.assertTrue(checked["valid"])
        self.assertEqual(checked["verified_source_files"], 2)
        with zipfile.ZipFile(self.target.parent / "source-snapshot.zip") as archive:
            self.assertEqual(archive.namelist(), ["source.py"])
            self.assertEqual(archive.read("source.py"), self.source.read_bytes())

    def test_exclusive_freeze_does_not_overwrite_previous_manifest_or_snapshot(self):
        self.freeze()
        before = {p.name: p.read_bytes() for p in self.target.parent.iterdir()}
        with self.assertRaises(FileExistsError):
            self.freeze()
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.target.parent.iterdir()})

    def test_changed_runtime_source_rejects_before_any_paid_execution(self):
        self.freeze()
        self.source.write_text("VALUE = 2\n", encoding="utf-8")
        with self.assertRaisesRegex(manifest.ManifestVerificationError, "source changed"):
            manifest.verify_manifest(self.target)

    def test_changed_dataset_rejects_even_when_case_ids_are_unchanged(self):
        self.freeze()
        self.cases[20]["title"] = "changed task"
        self.write_dataset()
        with self.assertRaises(manifest.ManifestVerificationError):
            manifest.verify_manifest(self.target)

    def test_case_family_cannot_cross_dev_and_acceptance(self):
        self.cases[20]["family"] = self.cases[0]["family"]
        self.write_dataset()
        with self.assertRaisesRegex(ValueError, "crosses splits"):
            self.freeze()
        self.assertFalse(self.target.exists())

    def test_secret_file_and_credentials_configuration_are_rejected(self):
        private = self.root / ".env"
        private.write_text("API_KEY=local-only\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Secret file"):
            manifest.freeze_manifest(self.target, self.dataset, pricing=self.pricing,
                model_configuration=self.configuration, source_paths=[self.source, private])
        self.configuration["api_key"] = "local-only"
        with self.assertRaisesRegex(ValueError, "credentials"):
            self.freeze()
        self.assertFalse(self.target.exists())

    def test_data_files_are_not_allowed_as_code_snapshot_inputs(self):
        data = self.root / "data/private.json"
        data.parent.mkdir()
        data.write_text('{"private": "data"}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Only code"):
            manifest.freeze_manifest(self.target, self.dataset, pricing=self.pricing,
                model_configuration=self.configuration, source_paths=[self.source, data])

    def test_snapshot_tampering_is_rejected(self):
        self.freeze()
        snapshot = self.target.parent / "source-snapshot.zip"
        snapshot.write_bytes(snapshot.read_bytes() + b"changed")
        with self.assertRaisesRegex(manifest.ManifestVerificationError, "snapshot changed"):
            manifest.verify_manifest(self.target)

    def test_manifest_pricing_or_model_tampering_is_rejected(self):
        self.freeze()
        doc = json.loads(self.target.read_text(encoding="utf-8"))
        doc["model_configuration"]["provider_request_model"] = "different"
        self.target.write_text(json.dumps(doc), encoding="utf-8")
        with self.assertRaisesRegex(manifest.ManifestVerificationError, "manifest bytes changed"):
            manifest.verify_manifest(self.target)

    def test_default_selection_detects_new_runtime_files(self):
        sources = [self.source]
        with patch.object(manifest, "_default_sources", side_effect=lambda: list(sources)):
            manifest.freeze_manifest(self.target, self.dataset, pricing=self.pricing, model_configuration=self.configuration)
            added = self.root / "new.py"
            added.write_text("NEW = True\n", encoding="utf-8")
            sources.append(added)
            with self.assertRaisesRegex(manifest.ManifestVerificationError, "new.py"):
                manifest.verify_manifest(self.target)

    def test_retry_and_subset_contracts_are_explicit(self):
        self.configuration["retries"] = 1
        with self.assertRaisesRegex(ValueError, "zero"):
            self.freeze()
        del self.configuration["retries"]
        with self.assertRaisesRegex(ValueError, "retries=0"):
            self.freeze()
        self.configuration["retries"] = 0
        with self.assertRaisesRegex(ValueError, "subset"):
            self.freeze(case_ids=["B51"])


if __name__ == "__main__":
    unittest.main()
