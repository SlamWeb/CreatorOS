"""B12 allows a narrowly declared title suffix, not fuzzy topic matching."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from creatoros.evaluation.benchmark.grader import MissingEvidence, _expectation


DATASET = Path(__file__).resolve().parents[1] / "docs/agent-eval/benchmark-v2/cases.json"


class B12TitleMatchingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dataset = json.loads(DATASET.read_text(encoding="utf-8"))
        cls.case = next(case for case in dataset["cases"] if case["id"] == "B12")
        cls.expectation = cls.case["expectations"][0]

    def evidence(self, suffix="辨析"):
        old = {"id": "old", "series_id": "SA", "title": "已有内容", "status": "queued"}
        pairs = ("rise与raise", "accept与except", "affect与effect")
        new = [{"id": str(index), "series_id": "SA", "title": pair + suffix, "status": "queued"}
               for index, pair in enumerate(pairs)]
        return {"variables": {"series_a": "SA"},
                "before": {"database": {"topics": [old]}},
                "after": {"database": {"topics": [deepcopy(old), *new]}}}

    def test_declared_suffix_and_exact_titles_are_accepted(self):
        self.assertEqual(self.expectation.get("optional_title_suffixes"), ["辨析"])
        for suffix in ("", "辨析", " 辨析"):
            with self.subTest(suffix=suffix):
                self.assertTrue(_expectation(self.expectation, self.evidence(suffix))[0])
        observed = self.evidence()
        observed["after"]["database"]["topics"][1]["title"] = "rise 与 raise 辨析"
        self.assertTrue(_expectation(self.expectation, observed)[0])

    def test_wrong_missing_combined_duplicate_or_extra_topics_are_rejected(self):
        for mutation in ("missing_word", "wrong_word", "combined", "duplicate", "missing_row", "extra_row", "extra_suffix"):
            data = self.evidence()
            rows = data["after"]["database"]["topics"]
            if mutation == "missing_word":
                rows[1]["title"] = "rise辨析"
            elif mutation == "wrong_word":
                rows[1]["title"] = "rise与raze辨析"
            elif mutation == "combined":
                rows[1]["title"] = "rise与raise、accept与except辨析"
            elif mutation == "duplicate":
                rows[2]["title"] = rows[1]["title"]
            elif mutation == "missing_row":
                rows.pop()
            elif mutation == "extra_row":
                rows.append({"id": "extra", "series_id": "SA", "title": "extra", "status": "queued"})
            else:
                rows[1]["title"] += "详解"
            with self.subTest(mutation=mutation):
                self.assertFalse(_expectation(self.expectation, data)[0])

    def test_wrong_series_or_status_and_changed_old_rows_are_rejected(self):
        for mutation in ("series", "status", "old_changed", "old_removed"):
            data = self.evidence()
            rows = data["after"]["database"]["topics"]
            if mutation == "series":
                rows[1]["series_id"] = "OTHER"
            elif mutation == "status":
                rows[1]["status"] = "candidate"
            elif mutation == "old_changed":
                rows[0]["title"] = "已改动"
            else:
                rows.pop(0)
            with self.subTest(mutation=mutation):
                self.assertFalse(_expectation(self.expectation, data)[0])

    def test_default_matching_stays_exact_and_only_b12_declares_suffix(self):
        strict = deepcopy(self.expectation)
        strict.pop("optional_title_suffixes")
        self.assertFalse(_expectation(strict, self.evidence())[0])
        dataset = json.loads(DATASET.read_text(encoding="utf-8"))
        other = [case["id"] for case in dataset["cases"] if case["id"] != "B12"
                 for exp in case["expectations"] if "optional_title_suffixes" in exp]
        self.assertEqual(other, [])

    def test_bad_suffix_declarations_do_not_silently_relax_matching(self):
        for invalid in ("辨析", [""], ["  "], [None]):
            exp = deepcopy(self.expectation)
            exp["optional_title_suffixes"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(MissingEvidence):
                _expectation(exp, self.evidence())


if __name__ == "__main__":
    unittest.main()
