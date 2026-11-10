"""Narrow explicit self-correction checks; no model calls."""
from copy import deepcopy
import unittest
from creatoros.evaluation.benchmark.judge_contradiction_guard import qualify_explicit_contradictions
from creatoros.evaluation.benchmark.scoring import score_case
from tests.test_benchmark_scoring import case, grade


class ContradictionTests(unittest.TestCase):
    def item(self, value, evidence):
        return {"items": [{"id": "meaning", "pass": value, "evidence": evidence}]}

    def test_real_b07_terminal_self_correction_and_scoring(self):
        original = self.item(False, "数量正确；更正：审查后重新判断，数量一致；本项不成立，pass应为true。")
        frozen = deepcopy(original)
        result = qualify_explicit_contradictions(case(), original)
        self.assertEqual(original, frozen)
        self.assertIsNone(result["items"][0]["pass"])
        self.assertIs(result["items"][0]["raw_pass"], False)
        scored = score_case(case(), grade(), original)
        self.assertEqual(scored["semantic"]["status"], "needs_review")
        self.assertIsNone(scored["semantic"]["score"])

    def test_both_directions_and_explicit_agreement(self):
        for value, text, expected in [(False, "pass应为true。", None),
                                      (True, "pass应为false。", None),
                                      (True, "pass应为true。", True),
                                      (False, "pass应为false。", False),
                                      (None, "pass应为true。", None)]:
            with self.subTest(value=value, text=text):
                self.assertIs(qualify_explicit_contradictions(case(), self.item(value, text))["items"][0]["pass"], expected)

    def test_non_verdict_and_quoted_strings_do_not_trigger(self):
        for text in ["事实符合，不能说不成立。", "pass应为true，但仍需检查。",
                     "其他项 b07-s1：pass应为true。", "示例：‘文字；pass应为true。",
                     '示例："文字；pass应为true。', "示例：`文字；pass应为true。"]:
            with self.subTest(text=text):
                self.assertIs(qualify_explicit_contradictions(case(), self.item(False, text))["items"][0]["pass"], False)

    def test_multiple_corrections_conflict_is_not_accepted(self):
        original = self.item(False, "pass应为false；pass应为true。")
        self.assertIsNone(qualify_explicit_contradictions(case(), original)["items"][0]["pass"])

    def test_terminal_retractions_preserve_raw_and_require_review(self):
        examples = [(False, "故应判 true，不判 false。撤回 false，改判 true。"),
                    (True, "撤回 true，改判 false。"),
                    (False, "Retract false; change the verdict to true."),
                    (True, "Withdraw TRUE, corrected to FALSE."),
                    (False, "Correction: pass should be true."),
                    (True, "Correction: pass should be false.")]
        for value, text in examples:
            with self.subTest(text=text):
                original = self.item(value, text)
                frozen = deepcopy(original)
                item = qualify_explicit_contradictions(case(), original)["items"][0]
                self.assertIsNone(item['pass'])
                self.assertIs(item['raw_pass'], value)
                self.assertEqual(original, frozen)
                self.assertEqual(score_case(case(), grade(), original)['semantic']['status'], 'needs_review')

    def test_retraction_does_not_guess_or_cross_scope(self):
        for value, text, expected in [
            (False, '撤回 true，改判 false。', False),
            (None, '撤回 false，改判 true。', None),
            (False, '撤回 false，改判 false。', False),
            (False, '撤回 false，改判 true，但仍需确认。', False),
            (False, '其他项：撤回 false，改判 true。', False),
            (False, '示例：“撤回 false，改判 true。', False),
            (False, '撤回 false。', False),
            (False, 'Retract false; change the verdict to true, pending review.', False),
        ]:
            with self.subTest(text=text):
                self.assertIs(qualify_explicit_contradictions(case(), self.item(value, text))['items'][0]['pass'], expected)


if __name__ == "__main__":
    unittest.main()
