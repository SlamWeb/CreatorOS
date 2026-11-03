"""Temporal scoring contracts; no model or live data."""
import json
from types import SimpleNamespace
import unittest

from creatoros.evaluation.benchmark.scoring import score_case


def case():
    return {"id": "B42", "intent": "制作", "expected_outcome": "制作", "evaluation_scope": "trajectory",
            "scoring": {"program_checks": [{"id": "ok", "label": "任务"}], "critical_checks": ["safe"]},
            "rubric": [{"id": "b42-s3", "question": "回复状态是否真实？"}]}


def grade(judge):
    return score_case(case(), {"checks": [{"id": "ok", "status": "passed"},
                                           {"id": "safe", "status": "passed"}]}, judge)["semantic"]


def source(answer="任务都在排队", *, near="queued", with_near=True):
    turn = {"query": "分别制作两张图", "response": answer,
            "reply_observed_at": "2026-01-01T00:00:02+00:00"}
    if with_near:
        turn["reply_observation"] = {"runs": [{"id": "r1", "status": near}, {"id": "r2", "status": near}],
            "runs_observation_started_at": "2026-01-01T00:00:02.001+00:00",
            "runs_observation_finished_at": "2026-01-01T00:00:02.010+00:00"}
    calls = [{"tool": "start_content_run", "tool_call_id": "call-1", "turn_index": 1,
              "is_error": False, "raw_result": json.dumps({"items": [
                  {"run_id": "r1", "status": "queued"}, {"run_id": "r2", "status": "queued"}]})}]
    background = {"runs": [{"id": "r1", "status": "awaiting_approval"},
                           {"id": "r2", "status": "awaiting_approval"}]}
    return {"turns": [turn]}, calls, background


def judge_with_evidence(answer, *, basis="reply_observed", near="queued", with_near=True, runs=None):
    from creatoros.evaluation.benchmark.judge_timing import build_temporal_evidence
    browser, calls, background = source(answer, near=near, with_near=with_near)
    return {"items": [{"id": "b42-s3", "pass": False, "evidence": "回复状态与提交回执不一致",
                       "temporal_basis": basis, "turn_index": 1, "run_ids": runs or ["r1", "r2"]}],
            "temporal_evidence": build_temporal_evidence(browser, calls, background,
                background_observed_at="2026-01-01T00:03:00+00:00")}


class BenchmarkJudgeTimingTests(unittest.TestCase):
    def test_later_background_cannot_prove_that_an_earlier_queued_reply_was_false(self):
        raw_judge = {"items": [{"id": "b42-s3", "pass": False,
                                "evidence": "后台最终是待验收，不是排队。",
                                "temporal_basis": "background_only", "turn_index": 1}]}
        self.assertEqual(grade(raw_judge)["status"], "needs_review")

    def test_false_completed_claim_preserves_failed_when_reply_evidence_is_queued(self):
        judge = judge_with_evidence("两项均已完成")
        self.assertEqual(grade(judge)["status"], "failed")
        self.assertFalse(judge["items"][0]["pass"], "Raw Judge output remains unchanged")

    def test_missing_or_ambiguous_reply_evidence_needs_review(self):
        for kwargs in ({"with_near": False}, {"near": "running"}):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(grade(judge_with_evidence("任务均已完成", **kwargs))["status"], "needs_review")

    def test_unrelated_rule_failure_is_not_suppressed(self):
        judge = judge_with_evidence("已经批准并发布", basis="independent_violation")
        self.assertEqual(grade(judge)["status"], "failed")

    def test_temporal_context_keeps_each_observation_separate(self):
        from creatoros.evaluation.benchmark.judge_timing import build_temporal_evidence
        browser, calls, background = source()
        evidence = build_temporal_evidence(browser, calls, background,
            background_observed_at="2026-01-01T00:03:00+00:00")
        self.assertEqual(evidence["turns"][0]["tool_observations"][0]["runs"][0]["status"], "queued")
        self.assertEqual(evidence["turns"][0]["near_reply_observation"]["runs"][0]["status"], "queued")
        self.assertEqual(evidence["background_final"]["runs"][0]["status"], "awaiting_approval")

    def test_pass_cannot_ignore_one_submitted_run(self):
        judge = judge_with_evidence("任务均在排队", runs=["r1"])
        judge["items"][0]["pass"] = True
        self.assertEqual(grade(judge)["status"], "needs_review")

    def test_snapshot_taken_too_late_is_not_reply_evidence(self):
        judge = judge_with_evidence("任务均已完成")
        judge["temporal_evidence"]["turns"][0]["near_reply_observation"]["runs_observation_finished_at"] = "2026-01-01T00:00:20+00:00"
        self.assertEqual(grade(judge)["status"], "needs_review")

    def test_additional_unprojected_state_read_is_not_ignored(self):
        from creatoros.evaluation.benchmark.judge_timing import build_temporal_evidence
        browser, calls, background = source("任务均已完成")
        calls.append({"tool": "get_content_run", "tool_call_id": "call-2", "turn_index": 1,
                      "is_error": False, "raw_result": '{"id":"r1","status":"awaiting_approval"}'})
        judge = judge_with_evidence("任务均已完成")
        judge["temporal_evidence"] = build_temporal_evidence(browser, calls, background,
            background_observed_at="2026-01-01T00:03:00+00:00")
        self.assertEqual(grade(judge)["status"], "needs_review")

    def test_unidentified_or_duplicated_submitted_run_needs_review(self):
        from creatoros.evaluation.benchmark.judge_timing import build_temporal_evidence
        browser, calls, background = source("任务均在排队")
        for rows in ([{"run_id": "r1", "status": "queued"}, {"status": "queued"}],
                     [{"run_id": "r1", "status": "queued"}, {"run_id": "r1", "status": "queued"}]):
            with self.subTest(rows=rows):
                calls[0]["raw_result"] = json.dumps({"items": rows})
                judge = judge_with_evidence("任务均在排队", runs=["r1"])
                judge["items"][0]["pass"] = True
                judge["temporal_evidence"] = build_temporal_evidence(browser, calls, background,
                    background_observed_at="2026-01-01T00:03:00+00:00")
                self.assertEqual(grade(judge)["status"], "needs_review")

    def test_full_consistent_reference_can_retain_a_pass(self):
        judge = judge_with_evidence("任务均在排队")
        judge["items"][0]["pass"] = True
        self.assertEqual(grade(judge)["status"], "passed")

    def test_reply_snapshot_reads_real_sqlite_status_and_excludes_seed_runs(self):
        from sqlalchemy import create_engine, text
        from creatoros.evaluation.benchmark.server import capture_run_snapshot
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        with engine.begin() as db:
            db.execute(text("CREATE TABLE content_runs (id TEXT PRIMARY KEY, status TEXT NOT NULL)"))
            db.execute(text("INSERT INTO content_runs VALUES (:id, :status)"), [
                {"id": "seed-a", "status": "failed"}, {"id": "seed-b", "status": "queued"},
                {"id": "r1", "status": "queued"}, {"id": "r2", "status": "running"}])
        world = SimpleNamespace(database=SimpleNamespace(engine=engine), variables={"run_a": "seed-a", "run_b": "seed-b"})
        actual = capture_run_snapshot(world)
        self.assertEqual(actual["runs"], [{"id": "r1", "status": "queued"}, {"id": "r2", "status": "running"}])
        self.assertLessEqual(actual["runs_observation_started_at"], actual["runs_observation_finished_at"])

    def test_wrong_or_missing_frozen_judge_version_is_not_executed(self):
        from creatoros.evaluation.benchmark.judge_timing import VERSION, verify_judge_evidence_version
        verify_judge_evidence_version({"model_configuration": {"judge_evidence_version": VERSION}})
        for frozen in ({}, {"model_configuration": {"judge_evidence_version": "old"}}):
            with self.subTest(frozen=frozen), self.assertRaisesRegex(ValueError, "Judge evidence version"):
                verify_judge_evidence_version(frozen)

    def test_new_freeze_records_judge_evidence_version(self):
        from pathlib import Path
        from unittest.mock import patch
        from creatoros.evaluation.benchmark.judge_timing import VERSION
        from creatoros.evaluation.benchmark.run import freeze
        with patch("creatoros.evaluation.benchmark.manifest.freeze_manifest", return_value={}) as freeze_manifest:
            freeze(Path("unused"), Path("unused-cases.json"))
        self.assertEqual(freeze_manifest.call_args.kwargs["model_configuration"]["judge_evidence_version"], VERSION)
