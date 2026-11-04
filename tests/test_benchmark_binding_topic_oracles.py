"""B04 and B22 outcome regressions; deterministic evidence, no model calls."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from creatoros.evaluation.benchmark.grader import MissingEvidence, _expectation


CASES = Path(__file__).resolve().parents[1] / "docs/agent-eval/benchmark-v2/cases.json"


class BindingAndTopicOracleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = {case["id"]: case for case in json.loads(CASES.read_text(encoding="utf-8"))["cases"]}

    def test_b04_checks_both_binding_stores_and_preserves_unknown(self):
        case = self.cases["B04"]
        index, expectation = next((i, item) for i, item in enumerate(case["expectations"], 1)
                                  if item["kind"] == "series_binding_unchanged")
        self.assertIn(f"outcome_{index:02}", {x["id"] for x in case["scoring"]["program_checks"] if x["critical"]})
        snapshot = {"database": {"series": [{"id": "SA", "skill_name": None, "mind_skill_id": "M",
                    "production_skill_id": "P", "name": "old"}], "series_skill_refs": [{"series_id": "SA",
                    "skill_ids_json": ["P"], "defaults_json": {"image": "P"}, "version": 1}]}}
        data = {"variables": {"series_a": "SA"}, "before": snapshot, "after": deepcopy(snapshot)}
        self.assertTrue(_expectation(expectation, data)[0])
        unrelated = deepcopy(data)
        unrelated["after"]["database"]["series"][0]["name"] = "new"
        unrelated["after"]["database"]["series_skill_refs"][0]["version"] = 2
        self.assertTrue(_expectation(expectation, unrelated)[0])
        for table, field, new_value in (("series", "skill_name", "other"),
                                        ("series", "mind_skill_id", "other"),
                                        ("series", "production_skill_id", "other"),
                                        ("series_skill_refs", "skill_ids_json", ["other"]),
                                        ("series_skill_refs", "defaults_json", {"image": "other"})):
            altered = deepcopy(data)
            altered["after"]["database"][table][0][field] = new_value
            with self.subTest(table=table, field=field):
                self.assertFalse(_expectation(expectation, altered)[0])
        missing = deepcopy(data)
        del missing["after"]["database"]["series_skill_refs"]
        with self.assertRaises(MissingEvidence):
            _expectation(expectation, missing)
        for phase in ("before", "after"):
            data[phase]["database"]["series_skill_refs"] = []
        self.assertTrue(_expectation(expectation, data)[0])
        data["after"]["database"]["series_skill_refs"].append({"series_id": "SA", "skill_ids_json": [], "defaults_json": {}})
        self.assertFalse(_expectation(expectation, data)[0])

    def test_b22_requires_old_topics_preserved_and_no_new_topics(self):
        case = self.cases["B22"]
        pair = [(i, item) for i, item in enumerate(case["expectations"], 1)
                if item.get("table") == "topics" and item["kind"] in ("rows_unchanged", "table_delta")]
        self.assertEqual({item["kind"] for _, item in pair}, {"rows_unchanged", "table_delta"})
        self.assertTrue({f"outcome_{i:02}" for i, _ in pair} <=
                        {x["id"] for x in case["scoring"]["program_checks"] if x["critical"]})
        data = {"before": {"database": {"topics": [{"id": "old", "title": "keep"}]}},
                "after": {"database": {"topics": [{"id": "old", "title": "keep"}]}}}
        self.assertTrue(all(_expectation(item, data)[0] for _, item in pair))
        added = deepcopy(data)
        added["after"]["database"]["topics"].append({"id": "unrequested", "title": "new"})
        self.assertFalse(all(_expectation(item, added)[0] for _, item in pair))
        replaced = deepcopy(data)
        replaced["after"]["database"]["topics"] = [{"id": "new", "title": "replacement"}]
        self.assertFalse(all(_expectation(item, replaced)[0] for _, item in pair))
        del data["after"]["database"]["topics"]
        for _, item in pair:
            with self.assertRaises(MissingEvidence):
                _expectation(item, data)

    def test_b22_new_series_binds_the_requested_active_skill(self):
        rule = next(x for x in self.cases["B22"]["expectations"] if x["kind"] == "new_series_skill_binding")
        old = {"id":"old", "creator_id":"A", "name":"existing", "skill_name":"SK", "production_skill_id":None}
        created = {"id":"new", "creator_id":"A", "name":"四格辨词实验", "skill_name":"SK", "production_skill_id":None}
        data = {"variables":{"creator_a":"A", "skill_a":"SK"},
                "before":{"database":{"series":[old]}}, "after":{"database":{"series":[old, created]}}}
        self.assertTrue(_expectation(rule, data)[0])
        created["skill_name"] = "wrong"
        self.assertFalse(_expectation(rule, data)[0])
        created["skill_name"], created["production_skill_id"] = None, "SK"
        self.assertTrue(_expectation(rule, data)[0])
        del created["production_skill_id"]
        with self.assertRaises(MissingEvidence):
            _expectation(rule, data)


if __name__ == "__main__":
    unittest.main()
