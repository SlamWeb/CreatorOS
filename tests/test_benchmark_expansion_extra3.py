"""Media lifecycle expansion fixtures, graders, and missing-evidence gates."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "docs/agent-eval/expansion-extra3/cases.json"


class Extra3MediaStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = json.loads(DATASET.read_text(encoding="utf-8"))
        cls.cases = {case["id"]: case for case in cls.document["cases"]}

    def test_dataset_membership_and_atomic_semantic_rules(self):
        from creatoros.evaluation.benchmark.manifest import _dataset

        dataset = _dataset(DATASET)
        self.assertEqual([case["id"] for case in dataset["cases"]], ["Z01", "Z02", "Z03", "Z04"])
        for case in dataset["cases"]:
            generated = {f"outcome_{i:02}" for i in range(1, len(case["expectations"]) + 1)}
            generated |= {f"required_{i:02}" for i in range(1, len(case["trajectory"]["required"]) + 1)}
            self.assertEqual([rule["id"] for rule in case["rubric"]], [case["id"].lower() + "-s1"])
            self.assertEqual({row["id"] for row in case["scoring"]["program_checks"]}, generated)
            self.assertTrue(case["rubric"][0]["critical"])

    def test_read_only_trajectory_positive_negative_and_missing_evidence(self):
        from creatoros.evaluation.benchmark.grader import grade_case

        case = self.cases["Z01"]
        before = {"database": {"managed_materials": [{"id": "a", "creator_id": "bench-a"}]}, "files": {}}
        calls = [
            {"tool": "manage_material", "arguments": {"action": "analysis_status", "material_id": "a"}},
            {"tool": "manage_material", "arguments": {"action": "analysis", "material_id": "a"}},
        ]
        evidence = {
            "before": before, "after": deepcopy(before), "variables": {"bgm_material": "a"},
            "calls": calls, "foreign_before": {"database": {"managed_materials": [{"id": "b"}]}},
            "foreign_after": {"database": {"managed_materials": [{"id": "b"}]}},
            "foreign_canaries": ["PRIVATE_CANARY"],
            "requests": [{"purpose": "agent", "context": {"messages": [{"role": "user", "content": "status?"}]}}],
            "final_answer": "最近重分析失败，旧摘要仍可读取。",
            "browser": {"completed": True, "refresh_ok": True, "errors": []},
        }

        def status(data, identifier):
            return next(row["status"] for row in grade_case(self.cases["Z01"], data)["checks"]
                        if row["id"] == identifier)

        self.assertEqual(status(evidence, "outcome_01"), "passed")
        self.assertEqual(status(evidence, "required_01"), "passed")
        self.assertEqual(status(evidence, "required_02"), "passed")

        wrong = deepcopy(evidence)
        wrong["calls"] = [calls[0]]
        self.assertEqual(status(wrong, "required_02"), "failed")
        write = deepcopy(evidence)
        write["calls"].append({"tool": "manage_material", "arguments": {"action": "analyze", "material_id": "a"}})
        self.assertEqual(status(write, "forbidden_actions"), "failed")
        missing = deepcopy(evidence)
        missing["calls"] = None
        self.assertEqual(status(missing, "required_01"), "needs_review")
        changed = deepcopy(evidence)
        changed["after"]["files"]["unexpected"] = "digest"
        self.assertEqual(status(changed, "outcome_01"), "failed")
        absent = deepcopy(evidence)
        absent["before"].pop("files")
        self.assertEqual(status(absent, "outcome_01"), "needs_review")

    def test_each_case_requires_its_own_reads_and_keeps_failures_and_unknowns_distinct(self):
        from creatoros.evaluation.benchmark.grader import grade_case

        for case in self.document["cases"]:
            with self.subTest(case=case["id"]):
                variables = {"creator_a": "bench-a", "creator_b": "bench-b",
                             "bgm_material": "a" * 64, "failed_import": "f" * 64,
                             "completed_import": "e" * 64}
                calls = []
                for rule in case["trajectory"]["required"]:
                    wanted = rule["arguments_contains"]
                    arguments = {key: variables[value[1:-1]] if isinstance(value, str) and
                                 value.startswith("{") and value.endswith("}") else value
                                 for key, value in wanted.items()}
                    arguments["action"] = rule["action"]
                    calls.append({"tool": rule["tool"], "arguments": arguments})
                snapshot = {"database": {"managed_materials": [{"id": "a" * 64,
                             "creator_id": "bench-a"}]}, "files": {"fixture": "digest"}}
                evidence = {"before": snapshot, "after": deepcopy(snapshot), "variables": variables,
                            "calls": calls, "foreign_before": {"database": {"managed_materials":
                                [{"id": "b" * 64, "creator_id": "bench-b"}]}},
                            "foreign_after": {"database": {"managed_materials":
                                [{"id": "b" * 64, "creator_id": "bench-b"}]}},
                            "foreign_canaries": ["PRIVATE_CANARY"],
                            "requests": [{"purpose": "agent", "context": {"messages":
                                [{"role": "user", "content": "query"}]}}],
                            "final_answer": "状态已核对。", "browser": {"completed": True}}
                good = {row["id"]: row["status"] for row in grade_case(case, evidence)["checks"]}
                for number in range(1, len(calls) + 1):
                    self.assertEqual(good[f"required_{number:02}"], "passed")

                    missing = deepcopy(evidence)
                    missing["calls"] = calls[:number - 1] + calls[number:]
                    result = {row["id"]: row["status"] for row in grade_case(case, missing)["checks"]}
                    self.assertEqual(result[f"required_{number:02}"], "failed")

                    absent = deepcopy(evidence)
                    absent["calls"] = None
                    result = {row["id"]: row["status"] for row in grade_case(case, absent)["checks"]}
                    self.assertEqual(result[f"required_{number:02}"], "needs_review")

                unexpected_write = deepcopy(evidence)
                unexpected_write["calls"].append({"tool": "manage_material",
                    "arguments": {"action": "analyze", "material_id": "a" * 64}})
                result = {row["id"]: row["status"] for row in grade_case(case, unexpected_write)["checks"]}
                self.assertEqual(result["forbidden_actions"], "failed")

    def test_seeded_states_are_visible_through_real_scoped_material_apis(self):
        script = r"""
import json, os, sys
from pathlib import Path
root=Path(sys.argv[1]); case_id=sys.argv[2]; dataset_path=Path(sys.argv[3])
os.environ['DATABASE_URL']='sqlite:///'+(root/'world'/'world.db').as_posix()
from creatoros.evaluation.benchmark.world import BenchmarkWorld
doc=json.loads(dataset_path.read_text(encoding='utf-8'))
case=next(item for item in doc['cases'] if item['id']==case_id)
world=BenchmarkWorld(root/'world',case,None,dataset_path=dataset_path,eval_root=root/'eval')
try:
 material_id=world.variables['bgm_material']
 before_queries=world.snapshot()
 from fastapi.testclient import TestClient
 with TestClient(world.app) as client:
  if case_id=='Z01':
   status=client.get(f'/api/creators/bench-a/materials/{material_id}/analysis/status').json()
   analysis=client.get(f'/api/creators/bench-a/materials/{material_id}/analysis').json()
   assert status['status']=='failed'
   assert analysis['analysis_available'] is True and analysis['analysis']['bpm']==120
  elif case_id=='Z02':
   status=client.get(f'/api/creators/bench-a/materials/{material_id}/analysis/status').json()
   assert status['status']=='interrupted'
  elif case_id=='Z03':
   receipt=client.get(f"/api/creators/bench-a/materials/imports/{world.variables['failed_import']}").json()
   assert receipt['status']=='failed' and 'material_id' not in receipt
  elif case_id=='Z04':
   receipt=client.get(f"/api/creators/bench-a/materials/imports/{world.variables['completed_import']}").json()
   status=client.get(f'/api/creators/bench-a/materials/{material_id}/analysis/status').json()
   assert receipt['status']=='completed' and receipt['analysis_status']=='running'
   assert receipt['material_id']==material_id and status['status']=='interrupted'
  else: raise AssertionError(case_id)
 snapshot=world.snapshot()
 assert before_queries['database']==snapshot['database']
 assert before_queries['files']==snapshot['files']
 evidence={'before':snapshot,'after':snapshot,'variables':world.variables,'calls':[],
           'foreign_before':world.foreign_snapshot(snapshot),'foreign_after':world.foreign_snapshot(snapshot)}
 assert evidence['before']['files']==evidence['after']['files']
finally: world.close()
"""
        def verify(case_id):
            with TemporaryDirectory() as temp:
                result = subprocess.run(
                    [sys.executable, "-c", script, temp, case_id, str(DATASET)], cwd=ROOT,
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                )
                return case_id, result.returncode, result.stderr[-4000:]

        for case_id, code, error in map(verify, self.cases):
            with self.subTest(case=case_id):
                self.assertEqual(code, 0, error)


if __name__ == "__main__":
    unittest.main()
