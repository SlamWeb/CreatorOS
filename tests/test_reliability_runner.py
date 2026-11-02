"""Fast subprocess regressions for complete, skipped and failed unittest groups."""
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import contextlib
import io

from tests.run_reliability import classify_unittest, select_cases, module_inventory, reject_operational_plan_fixture, main


class ReliabilityRunnerTests(unittest.TestCase):
    def run_group(self, body):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "test_fixture.py").write_text("import unittest\n" + body, encoding="utf-8")
            receipt = root / "result.json"
            process = subprocess.run([sys.executable, "-m", "tests.reliability_unittest", "--result", str(receipt),
                                      "discover", "-s", str(root), "-p", "test_fixture.py"],
                                     capture_output=True, text=True, encoding="utf-8", timeout=30)
            return process, json.loads(receipt.read_text(encoding="utf-8"))

    def test_full_execution_passes_with_counts(self):
        process, result = self.run_group("class Sample(unittest.TestCase):\n def test_ok(self): pass\n")
        self.assertEqual(process.returncode, 0)
        self.assertEqual((result["discovered"], result["run"], result["skipped"]), (1, 1, 0))
        self.assertEqual(classify_unittest(process.returncode, result), "passed")

    def test_partial_and_entire_skip_are_incomplete(self):
        for body, run in [
            ("class Sample(unittest.TestCase):\n def test_ok(self): pass\n @unittest.skip('fixture skip')\n def test_skip(self): pass\n", 2),
            ("@unittest.skip('whole class')\nclass Sample(unittest.TestCase):\n def test_skip(self): pass\n", 1),
            ("class Sample(unittest.TestCase):\n @classmethod\n def setUpClass(cls): raise unittest.SkipTest('no runtime')\n def test_skip(self): pass\n", 0),
        ]:
            with self.subTest(body=body):
                process, result = self.run_group(body)
                self.assertEqual(result["run"], run)
                self.assertGreater(result["skipped"], 0)
                self.assertEqual(classify_unittest(process.returncode, result), "incomplete")
                self.assertTrue(result["skip_reasons"])

    def test_empty_suite_is_incomplete_and_failure_remains_failed(self):
        process, result = self.run_group("")
        self.assertEqual(classify_unittest(process.returncode, result), "incomplete")
        process, result = self.run_group("class Sample(unittest.TestCase):\n def test_bad(self): self.fail('retained failure')\n")
        self.assertEqual(classify_unittest(process.returncode, result), "failed")
        self.assertIn("retained failure", process.stderr)

    def test_missing_receipt_is_not_a_pass(self):
        self.assertEqual(classify_unittest(0, None), "incomplete")

    def test_inventory_lists_unreviewed_modules_without_importing_them(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "tests").mkdir()
            (root / "tests" / "test_unreviewed.py").write_text("raise RuntimeError('must never execute')", encoding="utf-8")
            report = module_inventory(root, [])
            self.assertEqual(report["not_in_any_python_gate"], ["tests.test_unreviewed"])
            self.assertEqual(report["not_run_in_this_invocation"], ["tests.test_unreviewed"])

    def test_old_plan_fixture_is_rejected_before_any_tool_can_dispatch(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "tests").mkdir()
            source = root / "tests" / "test_agent_plans.py"
            source.write_text("target = 'http://127.0.0.1:8765'", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Unsafe operational URL"):
                reject_operational_plan_fixture(root, ["tests.test_agent_plans"])
            source.write_text("target = 'http://127.0.0.1:1'", encoding="utf-8")
            reject_operational_plan_fixture(root, ["tests.test_agent_plans"])

    def test_media_failure_retains_failure_and_unrun_inventory(self):
        # Do not start any allowlisted group after the required codec preflight fails.
        with patch("tests.run_reliability.verify_media", side_effect=RuntimeError("missing codec")), \
                patch("tests.run_reliability.subprocess.run") as run, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["--shard", "1/2"]), 1)
        run.assert_not_called()
        report = json.loads(output.getvalue())
        self.assertFalse(report["all_passed"])
        self.assertEqual(report["results"], [])
        self.assertTrue(report["unrun_groups"])

    def test_real_skipped_group_returns_nonzero_and_retains_coverage(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "test_fixture.py").write_text("import unittest\nclass Sample(unittest.TestCase):\n @unittest.skip('required fixture missing')\n def test_skip(self): pass\n", encoding="utf-8")
            cases = [("skip-proof", ["-m", "unittest", "discover", "-s", str(root), "-p", "test_fixture.py"])]
            with patch("tests.run_reliability.CASES", cases), patch("tests.run_reliability.verify_media", return_value={}), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main([]), 1)
            lines = output.getvalue().splitlines()
            group = json.loads(lines[0])
            self.assertEqual(group["status"], "incomplete")
            self.assertEqual(group["unittest"]["skipped"], 1)
            report_path = Path(lines[-1].removeprefix("report="))
            self.assertFalse(json.loads(report_path.read_text(encoding="utf-8"))["all_passed"])
            self.assertEqual(group["unittest"]["skip_reasons"][0]["reason"], "required fixture missing")
            self.assertIn("skipped=1", (report_path.parent / "skip-proof.log").read_text(encoding="utf-8"))

    def test_shards_are_disjoint_and_cover_the_allowlist(self):
        cases = [(str(n), []) for n in range(7)]
        a, b = select_cases(cases, "1/2"), select_cases(cases, "2/2")
        self.assertEqual(set(x[0] for x in a) | set(x[0] for x in b), set(x[0] for x in cases))
        self.assertFalse(set(x[0] for x in a) & set(x[0] for x in b))
        for invalid in ["0/2", "3/2", "1/0", "n/a"]:
            with self.assertRaises(ValueError): select_cases(cases, invalid)
