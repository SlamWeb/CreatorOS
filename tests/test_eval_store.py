"""Deterministic storage boundaries; these records are not model eval scores."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from creatoros.evaluation.store import DIMENSIONS, EvalStore, EvalStoreError, MAX_EVIDENCE_BYTES


def fixture_report(run_id=None, *, auto_status="passed"):
    return {"schema_version": 1, "run_id": run_id or uuid4().hex, "case_id": "E01",
            "dataset_id": "creatoros-account-agent-v1", "git_sha": "0" * 40,
            "fixture_version": "store-test-v1", "started_at": "2026-10-10T00:00:00+00:00",
            "finished_at": "2026-10-10T00:00:01+00:00", "execution_status": "completed",
            "execution_mode": "controlled", "model": {"name": "fixture", "provider": "controlled"},
            "usage": None, "elapsed_seconds": 1.0, "auto_status": auto_status,
            "checks": [{"id": "recorded", "label": "受控存储测试", "status": auto_status,
                        "detail": "仅验证文件/API，不宣称真实模型成绩。", "evidence": ["trace.json"]}],
            "dimensions": {key: auto_status for key in DIMENSIONS}, "error": None,
            "evidence_files": [{"name": "trace.json", "label": "测试证据"}],
            "manual_checks": ["核对测试证据。"], "review": None}


def save_report(root, report):
    directory = root / report["run_id"]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    (directory / "trace.json").write_text(json.dumps({"data": ["合成私有哨兵可检查"],
                                                       "api_key": "credential-value"}), encoding="utf-8")
    return directory


def file_snapshot(root):
    # Windows holds the executor's coordination file exclusively while serving.
    # Its metadata is checked; every persisted DB/evidence file is byte checked.
    return {str(path.relative_to(root)): (
                "[OS_LOCK]" if path.name.endswith(".execution.lock") else hashlib.sha256(path.read_bytes()).hexdigest(),
                path.stat().st_size, path.stat().st_mtime_ns)
            for path in root.rglob("*") if path.is_file()}


class EvalStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "eval"
        self.store = EvalStore(self.root)

    def test_empty_read_does_not_create_root(self):
        self.assertEqual(len(self.store.overview()["cases"]), 12)
        self.assertEqual(self.store.runs(), {"items": [], "errors": []})
        self.assertFalse(self.root.exists())

    def test_public_case_definition_is_static_and_read_only(self):
        dataset = json.loads(self.store.cases_path.read_text(encoding="utf-8"))
        cases = self.store.overview()["cases"]
        self.assertTrue(all(case["status"] == "not_run" and case["run_count"] == 0 for case in cases))
        for actual, source in zip(cases, dataset["cases"]):
            self.assertEqual(set(actual["definition"]), {"steps", "assertions", "manual_checks"})
            self.assertEqual(actual["definition"], {key: source[key] for key in actual["definition"]})
        self.assertFalse(self.root.exists())

    def test_malformed_public_definition_is_rejected(self):
        dataset = json.loads(self.store.cases_path.read_text(encoding="utf-8"))
        path = Path(self.temporary.name) / "cases.json"
        invalid = copy.deepcopy(dataset)
        invalid["cases"][0]["steps"][0]["text"] = {"not": "text"}
        path.write_text(json.dumps(invalid), encoding="utf-8")
        with self.assertRaises(EvalStoreError) as error:
            EvalStore(self.root, cases_path=path).overview()
        self.assertEqual(error.exception.code, "eval_report_invalid")
        self.assertFalse(self.root.exists())

    def test_review_cas_and_reload_preserve_original_report(self):
        report = fixture_report()
        directory = save_report(self.root, report)
        original = (directory / "report.json").read_bytes()
        before = file_snapshot(self.root)
        detail = self.store.detail(report["run_id"])
        self.assertEqual(detail["status"], "needs_review")
        self.assertEqual(file_snapshot(self.root), before)
        reviewed = self.store.review(report["run_id"], expected_digest=detail["report_digest"],
                                     decision="passed", note="已检查该受控记录。")
        self.assertEqual(reviewed["status"], "passed")
        self.assertNotEqual(reviewed["report_digest"], detail["report_digest"])
        self.assertEqual((directory / "report.json").read_bytes(), original)
        self.assertEqual(EvalStore(self.root).detail(report["run_id"])["review"], reviewed["review"])
        with self.assertRaises(EvalStoreError) as stale:
            self.store.review(report["run_id"], expected_digest=detail["report_digest"], decision="failed", note="旧视图")
        self.assertEqual(stale.exception.code, "eval_review_conflict")
        report["elapsed_seconds"] = 2.0
        (directory / "report.json").write_text(json.dumps(report), encoding="utf-8")
        with self.assertRaises(EvalStoreError) as changed:
            self.store.detail(report["run_id"])
        self.assertEqual(changed.exception.code, "eval_review_stale")

    def test_failed_and_missing_evidence_cannot_be_reviewed_as_passed(self):
        for status in ("failed", "needs_review"):
            report = fixture_report(auto_status=status)
            save_report(self.root, report)
            detail = self.store.detail(report["run_id"])
            with self.assertRaises(EvalStoreError) as error:
                self.store.review(report["run_id"], expected_digest=detail["report_digest"], decision="passed", note="检查")
            self.assertEqual(error.exception.code, "eval_review_blocked")

    def test_invalid_report_visible_without_hiding_valid_run(self):
        valid = fixture_report()
        save_report(self.root, valid)
        invalid = fixture_report()
        directory = save_report(self.root, invalid)
        (directory / "report.json").write_text('{"invalid":', encoding="utf-8")
        results = self.store.runs()
        self.assertEqual([item["run_id"] for item in results["items"]], [valid["run_id"]])
        self.assertEqual(results["errors"][0]["run_id"], invalid["run_id"])
        self.assertEqual(self.store.overview()["errors"], results["errors"])
        with self.assertRaises(EvalStoreError) as error:
            self.store.detail(invalid["run_id"])
        self.assertEqual(error.exception.status_code, 422)

    def test_execution_failure_remains_failed_with_incomplete_grading(self):
        report = fixture_report(auto_status="needs_review")
        report.update(execution_status="failed", error={"kind": "environment", "message": "隔离执行未完成。"})
        save_report(self.root, report)
        detail = self.store.detail(report["run_id"])
        self.assertEqual(detail["auto_status"], "needs_review")
        self.assertEqual(detail["status"], "failed")
        self.assertEqual(self.store.runs()["items"][0]["status"], "failed")
        self.assertEqual(self.store.overview()["cases"][0]["status"], "failed")
        with self.assertRaises(EvalStoreError) as blocked:
            self.store.review(report["run_id"], expected_digest=detail["report_digest"], decision="passed", note="复核")
        self.assertEqual(blocked.exception.code, "eval_review_blocked")

    def test_schema_contradictions_and_secret_names_rejected(self):
        base = fixture_report()
        mutations = [lambda d: d.update(dimensions={}), lambda d: d.update(usage=False),
                     lambda d: d.update(usage={}), lambda d: d.update(schema_version=True),
                     lambda d: d.update(elapsed_seconds=-1), lambda d: d.update(case_id="E99"),
                     lambda d: d.update(execution_status="failed"),
                     lambda d: d["dimensions"].update(task_success="needs_review"),
                     lambda d: d["checks"][0].update(status="failed"),
                     lambda d: d["evidence_files"][0].update(name="../outside.json"),
                     lambda d: d["evidence_files"][0].update(name="credentials.json")]
        for mutate in mutations:
            report = copy.deepcopy(base)
            mutate(report)
            save_report(self.root, report)
            with self.assertRaises(EvalStoreError):
                self.store.detail(report["run_id"])

    def test_evidence_whitelist_size_and_redaction(self):
        report = fixture_report()
        directory = save_report(self.root, report)
        evidence = self.store.evidence(report["run_id"], "trace.json")
        self.assertEqual(evidence["format"], "json")
        self.assertEqual(evidence["content"]["data"], ["合成私有哨兵可检查"])
        self.assertEqual(evidence["content"]["api_key"], "[REDACTED]")
        for name in ("report.json", "review.json", "../trace.json", "C:/secret.txt", ".env", "unlisted.json"):
            with self.assertRaises(EvalStoreError):
                self.store.evidence(report["run_id"], name)
        (directory / "trace.json").write_bytes(b" " * (MAX_EVIDENCE_BYTES + 1))
        with self.assertRaises(EvalStoreError) as error:
            self.store.evidence(report["run_id"], "trace.json")
        self.assertEqual(error.exception.status_code, 413)

    def test_jsonl_credentials_and_private_reasoning_are_not_exposed(self):
        report = fixture_report()
        report["evidence_files"].append({"name": "events.jsonl", "label": "公开事件"})
        directory = save_report(self.root, report)
        (directory / "events.jsonl").write_text(
            json.dumps({"type": "reasoning", "text": "HIDDEN_REASONING"}) + "\n" +
            json.dumps({"type": "agentMessage", "text": "ordinary data", "token": "HIDDEN_TOKEN"}), encoding="utf-8")
        content = self.store.evidence(report["run_id"], "events.jsonl")["content"]
        self.assertIn("ordinary data", content)
        self.assertNotIn("HIDDEN_REASONING", content)
        self.assertNotIn("HIDDEN_TOKEN", content)

    def test_linked_run_directory_is_rejected(self):
        report = fixture_report()
        outside = Path(self.temporary.name) / "outside"
        target = save_report(outside, report)
        self.root.mkdir()
        link = self.root / report["run_id"]
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True,
                           capture_output=True)
            self.addCleanup(link.rmdir)
        else:
            link.symlink_to(target, target_is_directory=True)
            self.addCleanup(link.unlink)
        with self.assertRaises(EvalStoreError):
            self.store.detail(report["run_id"])
        self.assertEqual(self.store.runs()["items"], [])
        self.assertEqual(len(self.store.runs()["errors"]), 1)


if __name__ == "__main__":
    unittest.main()
