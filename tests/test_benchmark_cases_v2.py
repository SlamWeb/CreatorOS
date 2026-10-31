"""v2.1 case contracts and alternative valid trajectories; no model/API calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest

from creatoros.evaluation.benchmark.grader import grade_case


DATASET = Path(__file__).resolve().parents[1] / "docs/agent-eval/benchmark-v2/cases.json"


def evidence(calls):
    before = {"database": {"creators": [{"id": "A"}, {"id": "B"}],
        "series": [{"id": "SA", "creator_id": "A", "name": "英语辨析", "revision": 1}],
        "topics": [{"id": "TA", "series_id": "SA", "title": "original", "brief": "original brief"}],
        "topic_removals": [], "content_runs": [], "production_tasks": [], "media_collections": []}, "files": {}}
    return {"before": before, "after": deepcopy(before),
        "foreign_before": {"database": {"topics": [{"id": "TB", "title": "PRIVATE"}]}},
        "foreign_after": {"database": {"topics": [{"id": "TB", "title": "PRIVATE"}]}},
        "variables": {"creator_a": "A", "creator_b": "B", "series_a": "SA", "series_b": "SB",
            "topic_a": "TA", "topic_b": "TB", "skill_a": "SK", "extra_skill": "EXTRA"},
        "foreign_canaries": ["PRIVATE"], "requests": [{"context": {"messages": [{"role": "user", "content": "current account task"}]}}],
        "calls": calls, "final_answer": "done", "browser": {"completed": True, "refresh_ok": True, "errors": []},
        "background": {"pool": {"items": [{"skill_id": "SK"}, {"skill_id": "EXTRA"}]},
            "skills": [], "images": [], "videos": [], "exports": [], "research": [], "extractions": [], "plans": []}}


def call(tool, **arguments):
    return {"tool": tool, "arguments": arguments, "turn_index": 1, "is_error": False}


class BenchmarkCasesV21Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = json.loads(DATASET.read_text(encoding="utf-8"))
        cls.cases = {case["id"]: case for case in cls.dataset["cases"]}

    def test_all_50_cases_and_family_split_stay_locked(self):
        self.assertEqual(list(self.cases), [f"B{n:02}" for n in range(1, 51)])
        self.assertEqual(sum(c["split"] == "dev" for c in self.cases.values()), 40)
        self.assertEqual(sum(c["split"] == "acceptance" for c in self.cases.values()), 10)
        dev = {c["family"] for c in self.cases.values() if c["split"] == "dev"}
        acceptance = {c["family"] for c in self.cases.values() if c["split"] == "acceptance"}
        self.assertFalse(dev & acceptance)
        self.assertTrue(all(c["origin"]["type"] in {"trace", "adversarial_variant"} for c in self.cases.values()))

    def test_revision_is_explicit_and_preserves_the_first_round_identity(self):
        self.assertEqual(self.dataset["dataset_id"], "creatoros-trace-benchmark-v2.1-20261031")
        revision = self.dataset["revision"]
        self.assertEqual(revision["parent_dataset_id"], "creatoros-trace-benchmark-v2-20261031")
        self.assertEqual(revision["parent_definition_sha256"], "acc7f822419acba280bc954ffd9af0e457cc3e61c70ae3b299565dad3d2fa4cf")
        self.assertTrue(revision["original_reports_and_scores_unchanged"])
        self.assertEqual(set(revision["reasons"]), {"B04", "B06", "B07", "B08", "B17", "B21", "B24", "B29", "B42", "B46"})

    def test_cleared_brief_accepts_product_normalized_null(self):
        task = self.cases["B17"]
        expected = next(x for x in task["expectations"] if x["kind"] == "topic_field")
        self.assertIsNone(expected["value"])
        data = evidence([call("manage_topics", action="get", topic_id="TA"),
            call("manage_topics", action="update", topic_id="TA", changes={"brief": ""}, expected_digest="a" * 64)])
        data["after"]["database"]["topics"][0]["brief"] = None
        result = grade_case(task, data)
        self.assertEqual(result["auto_status"], "passed")

    def test_rename_accepts_current_context_direct_write_and_optional_refresh(self):
        write = call("update_series_composition", series_id="SA", name="四格英语辨析", expected_revision=1)
        for calls in ([write], [call("list_creator_series"), write]):
            with self.subTest(calls=len(calls)):
                data = evidence(calls)
                data["after"]["database"]["series"][0]["name"] = "四格英语辨析"
                self.assertEqual(grade_case(self.cases["B21"], data)["auto_status"], "passed")

    def test_set_pool_accepts_current_version_direct_write_and_optional_get(self):
        write = call("manage_skill_library", action="set_pool", series_id="SA", expected_version=1, skill_ids=["SK", "EXTRA"])
        for calls in ([write], [call("manage_skill_library", action="get", series_id="SA"), write]):
            with self.subTest(calls=len(calls)):
                self.assertEqual(grade_case(self.cases["B24"], evidence(calls))["auto_status"], "passed")

    def test_exact_asset_path_direct_read_does_not_require_text_preread(self):
        skill_id = "bench-picture--0123456789abcdef"
        read = call("read_image", skill_id=skill_id, path="assets/reference-01.png")
        for calls in ([read], [call("get_producer_skill", skill_id=skill_id, list_files=True), read]):
            with self.subTest(calls=len(calls)):
                data = evidence(calls)
                data["variables"]["skill_a"] = skill_id
                self.assertEqual(grade_case(self.cases["B29"], data)["auto_status"], "passed")

    def test_137_item_pagination_accepts_different_page_sizes_but_rejects_one_page(self):
        task = self.cases["B07"]
        self.assertEqual(task["pagination_fixture"]["visible_items"], 137)
        for size, pages in ((100, 2), (50, 3), (20, 7)):
            with self.subTest(page_size=size):
                calls = [call("manage_topics", action="list", series_id="SA", query={"state": "queued", "offset": i * size, "limit": size}) for i in range(pages)]
                self.assertEqual(grade_case(task, evidence(calls))["auto_status"], "passed")
        one_page = evidence([call("manage_topics", action="list", series_id="SA", query={"offset": 0, "limit": 100})])
        result = grade_case(task, one_page)
        self.assertEqual(next(c for c in result["checks"] if c["id"] == "required_01")["status"], "failed")

    def test_new_oracles_compare_real_fields_threads_and_frozen_candidate(self):
        self.assertIn({"kind": "answer_contains_topic_field", "id": "{topic_a}", "field": "brief"}, self.cases["B06"]["expectations"])
        self.assertIn({"kind": "answer_equals_topic_field", "id": "{topic_a}", "field": "title"}, self.cases["B08"]["expectations"])
        self.assertIn({"kind": "codex_threads_min", "value": 3}, self.cases["B42"]["expectations"])
        self.assertIn({"kind": "candidate_used", "id": "{skill_a}", "topic_id": "{topic_a}", "value": True}, self.cases["B46"]["expectations"])

    def test_rsi_dispatch_level_is_separate_from_the_strict_delivery_repair_case(self):
        rsi = next(x["question"] for x in self.cases["B04"]["rubric"] if x["id"] == "rsi_stop")
        for phrase in ("业务round", "trial/dispatch", "不直接判", "成本诊断"):
            self.assertIn(phrase, rsi)
        self.assertEqual(self.cases["B04"]["trajectory"]["required"][0]["arguments_contains"]["max_rounds"], 1)
        self.assertEqual(self.cases["B50"]["nested_codex_constraints"]["new_image_generations"], 1)

    def test_b20_and_b50_are_unchanged_from_the_frozen_baseline(self):
        expected = {"B20": "8bca73249e707e6241d436f629600bbb04b78d4acb17878173cb4b9c14c17fbd",
            "B50": "62a7d8f8645bf0acc7166c08095d6be2c44fe3109a994c8d734ba8eccb754d47"}
        for case_id, digest in expected.items():
            encoded = json.dumps(self.cases[case_id], sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
            self.assertEqual(hashlib.sha256(encoded).hexdigest(), digest)
        self.assertIn("全部brief", self.cases["B06"]["rubric"][0]["question"])
        self.assertIn("逐字", self.cases["B06"]["steps"][0]["text"])


if __name__ == "__main__":
    unittest.main()
