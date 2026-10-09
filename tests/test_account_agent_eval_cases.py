"""Positive/negative controls for dataset structure, not Agent performance."""
from copy import deepcopy
import unittest

from tests.account_agent_eval_cases import load_dataset, validate_dataset


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.dataset = load_dataset()

    def rejects(self, mutate):
        changed = deepcopy(self.dataset)
        mutate(changed)
        with self.assertRaises(ValueError):
            validate_dataset(changed)

    def test_canonical_twelve_cases(self):
        self.assertEqual(len(validate_dataset(self.dataset)), 12)
        self.assertTrue(all(c["status"] == "not_run" for c in self.dataset["cases"]))

    def test_missing_case(self):
        self.rejects(lambda d: d["cases"].pop())

    def test_duplicate_id(self):
        self.rejects(lambda d: d["cases"][1].update(id="E01"))

    def test_unbalanced_category(self):
        self.rejects(lambda d: d["cases"][0].update(category="tools"))

    def test_wrong_split(self):
        self.rejects(lambda d: d["cases"][0].update(split="acceptance"))

    def test_fabricated_pass(self):
        self.rejects(lambda d: d["cases"][0].update(status="passed"))

    def test_missing_evidence(self):
        self.rejects(lambda d: d["cases"][0]["assertions"][0].update(evidence=[]))

    def test_unknown_evidence(self):
        self.rejects(lambda d: d["cases"][0]["assertions"][0].update(evidence=["invented"]))

    def test_answer_not_in_user_input(self):
        self.rejects(lambda d: d["cases"][0]["steps"][0].update(expected_answer="oracle"))

    def test_unavailable_probe_tool(self):
        self.rejects(lambda d: d["cases"][0]["boundary_probes"][0].update(tool="install_producer_skill"))

    def test_probe_invalid_schema(self):
        self.rejects(lambda d: d["cases"][4]["boundary_probes"][0]["arguments"].update(offset=0))

    def test_security_probe_cannot_be_omitted(self):
        self.rejects(lambda d: d["cases"][0].update(boundary_probes=[]))

    def test_unknown_fixture(self):
        self.rejects(lambda d: d["cases"][0].update(fixtures=["missing"]))

    def test_unknown_variable(self):
        self.rejects(lambda d: d["cases"][0]["steps"][0].update(text="查看 {{unknown_id}}"))

    def test_missing_human_rubric(self):
        self.rejects(lambda d: d["cases"][0].update(manual_checks=[]))

    def test_invalid_event(self):
        self.rejects(lambda d: d["cases"][3]["steps"][1].update(name="fake_success"))


if __name__ == "__main__":
    unittest.main()
