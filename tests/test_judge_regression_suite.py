"""Offline scorer checks with deliberate good/bad replies; no model calls."""
from copy import deepcopy
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from creatoros.config import PROJECT_ROOT
from tests.judge_regression_suite import DATASET, DATASET_SHA256, digest, grade, load_dataset
from tests.live_judge_regression_suite import main, regrade_suite, totals, validate_root


CASES = {case["id"]: case for case in load_dataset()["cases"]}


def response(result):
    return dict(content=json.dumps(result, ensure_ascii=False), tool_calls=[], usage=None)


def good_reply(case):
    """A local scorer fixture, never a model-quality measurement."""
    expected = case["expected"]
    if case["kind"] == "rubric":
        text = "；".join(group["any_of"][0] for group in expected["required_quality_groups"])
        domains = expected["domains"]
        return dict(criteria=[dict(description="作品应体现：" + text,
            evidence="依据本题优秀参考文字中的具体要求，待产物送达后检查。", critical=False,
            quality_kind=domains[index % len(domains)]) for index in range(3)],
            workflow_constraints=[group["any_of"][0] for group in expected["required_workflow_groups"]])
    return dict(scores=[dict(criterion_id=item["id"], score=0,
            evidence="未提供完整剧本或图片，证据不足，不能判断；发布正文可单独查看。")
            for item in case["payload"]["criteria"]],
        feedback="没有收到图片和完整剧本；未提供的证据无法判断，不猜测画面。", new_criteria=[])


class FrozenJudgeScorerTests(unittest.TestCase):
    def test_dataset_is_frozen_and_all_eight_cases_have_explicit_expectations(self):
        self.assertEqual(digest(DATASET), DATASET_SHA256)
        self.assertEqual(list(CASES), ["R01", "R02", "R03", "R04", "R05", "J06", "J07", "J08"])
        for case in CASES.values():
            self.assertIn("domains", case["expected"])
            self.assertTrue(case["payload"]["judge_policy"]["version"] == 2)

    def test_modified_or_substitute_dataset_cannot_run_as_frozen_v1(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "changed.json"
            path.write_bytes(DATASET.read_bytes() + b"\n")
            with self.assertRaisesRegex(ValueError, "Frozen Judge dataset differs"):
                load_dataset(path)

    def test_valid_local_replies_pass_every_case(self):
        for case in CASES.values():
            with self.subTest(case=case["id"]):
                result = grade(case, response(good_reply(case)))
                self.assertTrue(result["passed"], result)

    def test_operation_disguised_as_content_is_rejected_despite_valid_enum(self):
        reply = good_reply(CASES["R01"])
        reply["criteria"][0]["description"] += "；图片必须写出最多3轮和不发布。"
        result = grade(CASES["R01"], response(reply))
        self.assertFalse(result["passed"])
        self.assertTrue(result["checks"][0]["passed"], "Product enum alone accepts this semantic mistake")

    def test_correct_category_with_wrong_domain_words_is_rejected(self):
        for identifier, phrase in (("R02", "字号必须统一，画风高级"), ("R03", "故事立意必须深刻")):
            with self.subTest(case=identifier):
                reply = good_reply(CASES[identifier])
                reply["criteria"][0]["description"] += phrase
                self.assertFalse(grade(CASES[identifier], response(reply))["passed"])

    def test_wrong_category_and_missing_overall_coverage_are_rejected(self):
        for identifier in ("R02", "R03", "R01"):
            reply = good_reply(CASES[identifier])
            wrong = "presentation" if identifier == "R02" else "content"
            for item in reply["criteria"]:
                item["quality_kind"] = wrong
            self.assertFalse(grade(CASES[identifier], response(reply))["passed"])

    def test_negative_creative_rules_and_publish_copy_are_not_blanket_banned(self):
        reply = good_reply(CASES["R04"])
        reply["criteria"][0]["description"] += "不得添加标题，不用噪点，不遮挡文字；发布文案词义准确。"
        self.assertTrue(grade(CASES["R04"], response(reply))["passed"])

    def test_missing_required_quality_and_missing_separate_operations_are_rejected(self):
        for target in ("quality", "workflow"):
            reply = good_reply(CASES["R01"])
            if target == "quality":
                for item in reply["criteria"]:
                    item["description"] = "一切都很好"
            else:
                reply["workflow_constraints"] = []
            self.assertFalse(grade(CASES["R01"], response(reply))["passed"])

    def test_whitespace_normalization_accepts_same_answer_and_does_not_hide_operations(self):
        reply = good_reply(CASES["R02"])
        reply["workflow_constraints"] = ["最多进行 2 轮评审"]
        self.assertTrue(grade(CASES["R02"], response(reply))["passed"])
        reply["criteria"][0]["description"] += "；作品需显示最多 2 轮。"
        self.assertFalse(grade(CASES["R02"], response(reply))["passed"])

    def test_rubric_cannot_claim_received_candidate_or_script(self):
        for claim in ("本次已收到候选产物，可以评分。", "已收到的发布正文写得很好。", "完整剧本已提供。"):
            reply = good_reply(CASES["R05"])
            reply["criteria"][0]["evidence"] = claim
            self.assertFalse(grade(CASES["R05"], response(reply))["passed"], claim)
        reply = good_reply(CASES["R05"])
        reply["criteria"][0]["evidence"] = "未提供候选产物；未来产物应按参考文字检查。"
        self.assertTrue(grade(CASES["R05"], response(reply))["passed"])

    def test_judge_cannot_invent_script_or_image_observations(self):
        for identifier, claim in (("J06", "完整剧本显示所有场景都很连贯。"),
                                  ("J07", "候选图清楚显示方块体素风格统一。"),
                                  ("J08", "图中可以看到清晰文字。")):
            reply = good_reply(CASES[identifier])
            reply["scores"][0]["evidence"] = claim
            self.assertFalse(grade(CASES[identifier], response(reply))["passed"])

    def test_judge_must_acknowledge_missing_evidence(self):
        reply = good_reply(CASES["J06"])
        for item in reply["scores"]:
            item["evidence"] = "全都很好。"
        reply["feedback"] = "通过。"
        self.assertFalse(grade(CASES["J06"], response(reply))["passed"])

    def test_optional_addition_can_be_empty_or_in_scope_but_not_wrong_domain_or_operations(self):
        for kind, description, accepted in (("presentation", "上下格冷暖光形成对照", True),
            ("content", "故事立意很深刻", False), ("presentation", "最多3轮，不发布", False)):
            reply = good_reply(CASES["J07"])
            reply["new_criteria"] = [dict(description=description, evidence="依据优秀参考文字的冷暖光描述。",
                critical=False, quality_kind=kind)]
            self.assertEqual(grade(CASES["J07"], response(reply))["passed"], accepted)

    def test_fixed_rejudge_cannot_add_or_skip_a_criterion(self):
        original = good_reply(CASES["J08"])
        added = deepcopy(original)
        added["new_criteria"] = [dict(description="更多具体行动", evidence="优秀参考文字。",
            critical=False, quality_kind="content")]
        skipped = deepcopy(original)
        skipped["scores"].pop()
        duplicate = deepcopy(original)
        duplicate["scores"][-1]["criterion_id"] = "c1"
        for reply in (added, skipped, duplicate):
            self.assertFalse(grade(CASES["J08"], response(reply))["passed"])

    def test_malformed_json_tools_and_bad_score_type_are_rejected(self):
        for raw in (dict(content="not json"), dict(content="{}", tool_calls=[dict(name="publish")])):
            self.assertFalse(grade(CASES["J08"], raw)["passed"])
        reply = good_reply(CASES["J08"])
        reply["scores"][0]["score"] = "100"
        self.assertFalse(grade(CASES["J08"], response(reply))["passed"])

    def test_paid_runner_requires_flag_and_rejects_formal_or_existing_evidence_roots(self):
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as failure:
            main(["--root", "tmp/not-created-without-paid-consent"])
        self.assertEqual(failure.exception.code, 2)
        for path in ("data/judge-suite", "tmp", "tests", DATASET.parent):
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_root(path)

    def test_offline_regrade_keeps_source_bytes_and_never_calls_a_provider(self):
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "tmp") as directory:
            source = Path(directory) / "source"
            source.mkdir()
            results = []
            for case in CASES.values():
                item = source / case["id"]
                item.mkdir()
                (item / "request.json").write_text(json.dumps(dict(case=case)), encoding="utf-8")
                (item / "raw-response.json").write_text(json.dumps(response(good_reply(case))), encoding="utf-8")
                results.append(dict(id=case["id"], passed=True, usage=None, error=None))
            (source / "report.json").write_text(json.dumps(dict(dataset_sha256=DATASET_SHA256,
                results=results, passed=8, usage={})), encoding="utf-8")
            before = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
            output = Path(directory) / "regrade"
            # Patching this boundary is a no-network assertion for the offline
            # command, not a fake model response or a paid-quality measurement.
            with patch("creatoros.integrations.skill_iteration_judge.default_provider",
                       side_effect=AssertionError("Offline replay attempted a model call")), redirect_stdout(StringIO()):
                self.assertEqual(regrade_suite(source, output), 0)
            report = json.loads((output / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["passed"], 8)
            self.assertEqual(report["paid_model_requests"], 0)
            self.assertTrue(report["old_evidence_unchanged"])
            self.assertEqual({p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}, before)
            with self.assertRaisesRegex(ValueError, "inside the original evidence"):
                regrade_suite(source, source / "nested-regrade")
            self.assertFalse((source / "nested-regrade").exists())

    def test_usage_unknown_is_not_reported_as_zero_cost_or_double_counted(self):
        self.assertEqual(totals([dict(usage=None), dict(usage=dict(input_tokens=12, output_tokens=7,
            cache_hit_tokens=5))]), dict(input_tokens=12, output_tokens=7, cache_hit_tokens=5,
                usage_known_requests=1, usage_unknown_requests=1))


if __name__ == "__main__":
    unittest.main()
