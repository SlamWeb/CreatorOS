"""Explicit isolated datasets do not replace the original twelve definitions."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from creatoros.evaluation.store import EvalStore, EvalStoreError


class EvalDatasetExtensionTests(unittest.TestCase):
    def test_explicit_slices_accept_registered_task_ids_without_inheriting_old_runs(self):
        original = EvalStore()._dataset()
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for identity in ("A14", "S13", "P01", "B01"):
                dataset = copy.deepcopy(original)
                dataset["dataset_id"] = "creatoros-workbench-v1"
                dataset["cases"] = [dataset["cases"][0]]
                dataset["cases"][0]["id"] = identity
                path = root / f"{identity}.json"
                path.write_text(json.dumps(dataset), encoding="utf-8")
                store = EvalStore(root / "evidence", cases_path=path)
                case = store.overview()["cases"][0]
                self.assertEqual(case["id"], identity)
                self.assertEqual(case["status"], "not_run")
                self.assertEqual(store.runs()["items"], [])
                self.assertFalse(store.root.exists())
            self.assertEqual(len(EvalStore(root / "old").overview()["cases"]), 12)

    def test_other_ids_or_unregistered_report_still_rejected(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for identity in ("P1", "P001", "X01", "../P01"):
                dataset = copy.deepcopy(EvalStore()._dataset())
                dataset["cases"][0]["id"] = identity
                path = root / "cases.json"
                path.write_text(json.dumps(dataset), encoding="utf-8")
                with self.assertRaises(EvalStoreError):
                    EvalStore(root / "evidence", cases_path=path).overview()


if __name__ == "__main__":
    unittest.main()
