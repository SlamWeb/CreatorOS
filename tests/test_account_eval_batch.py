"""Pure freeze/claim checks; no Provider, browser query or formal data."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from creatoros.evaluation import batch


class FrozenBatchTests(unittest.TestCase):
    def manifest(self, root):
        path = Path(root) / "freeze-manifest.json"
        source = {"creatoros/web/chat.py": "old", "creatoros/evaluation/grader.py": "fixed"}
        path.write_text(json.dumps({"source_hashes": source,
            "evaluator_hashes": {"creatoros/evaluation/grader.py": "fixed"}, "execution_keys": ["E01"]}), encoding="utf-8")
        return path, source

    def test_baseline_rejects_any_source_change(self):
        with TemporaryDirectory() as root:
            path, source = self.manifest(root)
            source["creatoros/web/chat.py"] = "new"
            with patch.object(batch, "MANIFEST", path), patch.object(batch, "hashes", return_value=source):
                with self.assertRaisesRegex(ValueError, "冻结来源"):
                    batch.verify("baseline")

    def test_regression_allows_business_fix_not_grader_change(self):
        with TemporaryDirectory() as root:
            path, source = self.manifest(root)
            source["creatoros/web/chat.py"] = "new"
            with patch.object(batch, "MANIFEST", path), patch.object(batch, "hashes", return_value=source):
                batch.verify("regression")
                source["creatoros/evaluation/grader.py"] = "looser"
                with self.assertRaisesRegex(ValueError, "冻结来源"):
                    batch.verify("regression")

    def test_same_slot_is_never_replaced_or_retried(self):
        with TemporaryDirectory() as root:
            path, source = self.manifest(root)
            with patch.object(batch, "MANIFEST", path), patch.object(batch, "hashes", return_value=source):
                first = batch.claim(root, "batch-one", "baseline", "E01", None, "original")
                with self.assertRaises(FileExistsError):
                    batch.claim(root, "batch-one", "baseline", "E01", None, "retry")
                saved = json.loads((Path(root) / "batches/batch-one/baseline/E01.json").read_text(encoding="utf-8"))
                self.assertEqual(saved, first)

    def test_unsafe_batch_path_rejected(self):
        with self.assertRaises(ValueError):
            batch.claim("ignored", "../outside", "baseline", "E01", None, "run")


if __name__ == "__main__":
    unittest.main()
