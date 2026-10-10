"""Pure freeze/claim checks; no Provider, browser query or formal data."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from creatoros.evaluation import batch


class FrozenBatchTests(unittest.TestCase):
    def test_independent_round_preserves_old_manifest_and_claims_once(self):
        with TemporaryDirectory() as root:
            path, source = self.manifest(root)
            original = path.read_bytes()
            with patch.dict("os.environ", {"CREATOROS_EVAL_MANIFEST": "new-round.json"}), \
                 patch.object(batch, "MANIFEST", path), patch.object(batch, "hashes", return_value=source):
                created = batch.freeze_round("new-round")
                self.assertEqual(created["execution_keys"], ["E01"])
                batch.verify("baseline")
                batch.claim(root, "fresh-batch", "baseline", "E01", None, "run")
                with self.assertRaises(FileExistsError):
                    batch.claim(root, "fresh-batch", "baseline", "E01", None, "retry")
                with self.assertRaisesRegex(ValueError, "不得覆盖"):
                    batch.freeze_round("new-round")
                source["creatoros/web/chat.py"] = "changed"
                with self.assertRaisesRegex(ValueError, "冻结来源"):
                    batch.verify("baseline")
            self.assertEqual(path.read_bytes(), original)

    def test_independent_manifest_path_cannot_escape(self):
        for name in ("../outside.json", "C:/file.json", "bad\\file.json", "bad file.json"):
            with self.subTest(name=name), patch.dict("os.environ", {"CREATOROS_EVAL_MANIFEST": name}):
                with self.assertRaisesRegex(ValueError, "非法独立"):
                    batch.manifest_path("baseline")

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

    def test_amended_regression_keeps_baseline_and_original_criteria(self):
        with TemporaryDirectory() as root:
            path, source = self.manifest(root)
            original = path.read_bytes()
            amended = path.with_name("regression-manifest.json")
            import hashlib
            new_source = {**source, "creatoros/evaluation/grader.py": "parser-errata"}
            amended.write_text(json.dumps({"parent_manifest_sha256": hashlib.sha256(original).hexdigest(),
                "evaluator_hashes": {"creatoros/evaluation/grader.py": "parser-errata"},
                "execution_keys": ["E01"], "revision": "same-cases-errata1"}), encoding="utf-8")
            with patch.object(batch, "MANIFEST", path), patch.object(batch, "hashes", return_value=new_source):
                batch.verify("regression")
                claim = batch.claim(root, "batch-one", "regression", "E01", None, "regression")
                self.assertEqual(claim["protocol_revision"], "same-cases-errata1")
                with self.assertRaisesRegex(ValueError, "冻结来源"):
                    batch.verify("baseline")
                new_source["creatoros/evaluation/grader.py"] = "later-change"
                with self.assertRaisesRegex(ValueError, "冻结来源"):
                    batch.verify("regression")
            self.assertEqual(path.read_bytes(), original)

    def test_amendment_cannot_point_to_changed_parent(self):
        with TemporaryDirectory() as root:
            path, source = self.manifest(root)
            path.with_name("regression-manifest.json").write_text(json.dumps({
                "parent_manifest_sha256": "wrong", "evaluator_hashes": {}}), encoding="utf-8")
            with patch.object(batch, "MANIFEST", path), patch.object(batch, "hashes", return_value=source):
                with self.assertRaisesRegex(ValueError, "原冻结来源"):
                    batch.verify("regression")

    def test_amendment_rejects_incomplete_baseline_and_changed_case(self):
        with TemporaryDirectory() as root:
            path, source = self.manifest(root)
            parent = json.loads(path.read_text(encoding="utf-8"))
            parent["source_hashes"]["docs/agent-eval/cases.json"] = "original-case"
            path.write_text(json.dumps(parent), encoding="utf-8")
            with patch.object(batch, "MANIFEST", path), patch.object(batch, "hashes", return_value=source):
                with patch.object(batch, "summarize", return_value={"missing": ["E01"], "recorded": 0}):
                    with self.assertRaisesRegex(ValueError, "尚未结束"):
                        batch.freeze_regression("batch")
                with patch.object(batch, "summarize", return_value={"missing": [], "recorded": 1, "runs": []}):
                    with self.assertRaisesRegex(ValueError, "冻结题目"):
                        batch.freeze_regression("batch")


if __name__ == "__main__":
    unittest.main()
