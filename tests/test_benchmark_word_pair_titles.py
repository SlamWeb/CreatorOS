"""Opt-in matching of requested word pairs without accepting other topics."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from creatoros.evaluation.benchmark.grader import MissingEvidence, _expectation


CASES = Path(__file__).resolve().parents[1] / "docs/agent-eval/benchmark-v2/cases.json"


class WordPairTitleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
        cls.rules = {case["id"]: deepcopy(next(x for x in case["expectations"]
                    if x.get("kind") == "new_rows_match" and x.get("table") == "topics"))
                    for case in cases if case["id"] in {"B11", "B12"}}

    def setUp(self):
        self.variables = {"series_a": "SA"}

    def evaluate(self, rule, titles):
        before = [{"id": "old", "series_id": "SA", "title": "keep", "status": "queued"}]
        after = deepcopy(before) + [{"id": str(i), "series_id": "SA", "title": title, "status": "queued"}
                                    for i, title in enumerate(titles)]
        return _expectation(rule, {"variables": self.variables, "before": {"database": {"topics": before}},
                                   "after": {"database": {"topics": after}}})[0]

    def rule(self, case):
        rule = deepcopy(self.rules[case])
        rule["word_pair_title_separators"] = True
        rule["optional_title_suffixes"] = ["辨析"]
        return rule

    def test_single_pair_allows_only_named_separators_and_one_suffix(self):
        rule = self.rule("B11")
        for title in ("borrow与lend", "borrow 和 lend", "borrow / lend", "borrow／lend辨析"):
            with self.subTest(title=title):
                self.assertTrue(self.evaluate(rule, [title]))
        for title in ("lend与borrow", "borrow与raise", "borrow与lend辨析辨析", "borrow与lend解释",
                      "图书馆里的borrow与lend", "borrow与lend与raise", "borrowlend"):
            with self.subTest(title=title):
                self.assertFalse(self.evaluate(rule, [title]))

    def test_three_distinct_pairs_no_missing_duplicate_combined_or_misordered(self):
        rule = self.rule("B12")
        good = ["rise / raise辨析", "accept 和 except", "affect／effect"]
        self.assertTrue(self.evaluate(rule, good))
        for titles in (good[:2], [good[0], good[0], good[2]],
                       [good[0] + "、" + good[1], good[2]],
                       ["raise / rise", good[1], good[2]],
                       [good[0], "accept / affect", good[2]], good + ["rise与raise"]):
            with self.subTest(titles=titles):
                self.assertFalse(self.evaluate(rule, titles))

    def test_option_is_explicit_and_rejects_malformed_definitions(self):
        rule = self.rule("B11")
        del rule["word_pair_title_separators"]
        self.assertFalse(self.evaluate(rule, ["borrow / lend"]))
        rule["word_pair_title_separators"] = True
        rule["value"][0]["title"] = "模糊词组"
        with self.assertRaises(MissingEvidence):
            self.evaluate(rule, ["borrow / lend"])


if __name__ == "__main__":
    unittest.main()
