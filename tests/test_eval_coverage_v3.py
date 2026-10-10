"""Coverage-definition controls only: no services, models, or business writes."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COVERAGE_PATH = PROJECT_ROOT / "docs/agent-eval/coverage-v3.json"
SOURCE_PATH = PROJECT_ROOT / "docs/agent-eval/cases.json"
LAYER_COUNTS = {
    "account_agent": 24,
    "skill_workbench": 14,
    "business_reliability": 6,
    "codex_delivery": 6,
}
EXPECTED_IDS = (
    {f"E{number:02}" for number in range(1, 13)}
    | {f"A{number:02}" for number in range(13, 25)}
    | {f"S{number:02}" for number in range(1, 15)}
    | {f"B{number:02}" for number in range(1, 7)}
    | {f"P{number:02}" for number in range(1, 7)}
)
FORBIDDEN_RESULT_KEYS = {
    "auto_status", "results", "report", "usage", "run_id", "score",
    "success_rate", "passed", "failed", "finished_at",
}


def load_coverage():
    return json.loads(COVERAGE_PATH.read_text(encoding="utf-8"))


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _strings(value, label, minimum=1):
    _require(isinstance(value, list) and len(value) >= minimum, label)
    _require(all(isinstance(item, str) and item.strip() for item in value), label)


def _no_result_keys(value):
    if isinstance(value, dict):
        _require(not FORBIDDEN_RESULT_KEYS.intersection(value), "定义不得夹带运行成绩")
        for child in value.values():
            _no_result_keys(child)
    elif isinstance(value, list):
        for child in value:
            _no_result_keys(child)


def validate_coverage(dataset):
    """Fail closed on structural drift; this never grades an actual Agent run."""
    _require(isinstance(dataset, dict), "覆盖集必须为对象")
    _no_result_keys(dataset)
    _require(dataset.get("schema_version") == 1, "未知 schema")
    _require(dataset.get("dataset_id") == "creatoros-coverage-v3", "未知覆盖集")
    _require(isinstance(dataset.get("revision"), str) and dataset["revision"], "缺 revision")
    _require(dataset.get("status") == "definitions_planned", "定义不能宣称已运行")
    freeze = dataset.get("freeze", {})
    _require(freeze.get("status") == "not_frozen" and freeze.get("manifest") is None,
             "覆盖规划不能假称已冻结")
    _require(dataset.get("layer_counts") == LAYER_COUNTS, "层计数被改变")
    source = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    source_digest = hashlib.sha256(SOURCE_PATH.read_bytes()).hexdigest()
    expected_source = {
        "path": "docs/agent-eval/cases.json",
        "revision": source["revision"],
        "sha256": source_digest,
    }
    _require(dataset.get("source_dataset") == expected_source, "原题集来源不一致")
    _require(source["revision"] == "account-baseline-v2", "原12题不能静默换版本")
    source_cases = {case["id"]: case for case in source["cases"]}
    rows = dataset.get("cases")
    _require(isinstance(rows, list) and len(rows) == 50, "必须恰好50个场景")
    _require(all(isinstance(case, dict) for case in rows), "场景必须为对象")
    ids = [case.get("id") for case in rows]
    _require(all(isinstance(case_id, str) for case_id in ids), "场景编号必须为文本")
    _require(len(set(ids)) == len(ids), "场景编号重复")
    _require(set(ids) == EXPECTED_IDS, "场景编号缺失或无效")
    _require({layer: sum(case.get("layer") == layer for case in rows)
              for layer in LAYER_COUNTS} == LAYER_COUNTS, "实际分层计数不一致")
    cases = {case["id"]: case for case in rows}
    for case in rows:
        _require(case.get("status") == "planned", "不能把定义写成已通过或已运行")
        _require(case.get("layer") in LAYER_COUNTS, "未知评测层")
        _require(isinstance(case.get("title"), str) and case["title"].strip(), "缺标题")
        entry = case.get("user_entry", {})
        _require(entry.get("surface") in {"account_chat", "skills", "workspace", "run"},
                 "必须有真实用户页面入口")
        _require(isinstance(entry.get("action"), str) and entry["action"].strip(), "缺入口动作")
        _strings(case.get("preconditions"), "缺隔离前置", 2)
        steps = case.get("steps")
        _require(isinstance(steps, list) and steps, "缺测试步骤")
        for step in steps:
            _require(isinstance(step, dict), "步骤必须为对象")
            _require(step.get("actor") in {"user", "controller", "source_case"}, "未知步骤执行者")
            _require(isinstance(step.get("action"), str) and step["action"].strip(), "步骤无动作")
            _require("expected_answer" not in step and "oracle" not in step,
                     "不要把预期答案放进用户步骤")
        chain = case.get("expected_chain", {})
        _require(chain.get("kind") in {"tools", "http", "tools_and_http", "source_case"},
                 "缺明确工具或HTTP链")
        _strings(chain.get("actions"), "缺预期执行链")
        oracle = case.get("oracle", {})
        for dimension in ("database", "files", "reply"):
            _strings(oracle.get(dimension), f"缺{dimension} oracle")
        evidence = case.get("evidence")
        _strings(evidence, "缺必要证据", 6)
        _require(len(set(evidence)) == len(evidence), "证据项重复")
        _require({"browser_actions", "host_http_responses", "database_before_after",
                  "files_before_after", "execution_counts", "final_ui", "oracle"}.issubset(evidence),
                 "缺前端到数据库的完整证据")
        execution = case.get("execution", {})
        for flag in ("real_deepseek", "real_codex", "fault_injection"):
            _require(type(execution.get(flag)) is bool, "真实调用/故障必须明确布尔声明")
        for field in ("minimum_generated_images", "requested_generated_images"):
            _require(type(execution.get(field)) is int and execution[field] >= 0,
                     "真实图片数必须非负整数")
        _require(execution["minimum_generated_images"] == execution["requested_generated_images"],
                 "本轮固定生产任务的要求与最低新图门槛不一致")
        if execution["minimum_generated_images"]:
            _require(execution["real_codex"], "真实新图不能来自假生产器")
            _require("artifact_checksums" in evidence, "生图必须有产物校验证据")
        if execution["real_deepseek"]:
            _require({"request_snapshots", "tool_calls", "tool_results",
                      "final_answer", "raw_reply_and_host_delivery"}.issubset(evidence),
                     "真实DS必须保存实际请求、原文和宿主交付")
        if execution["real_codex"]:
            _require({"public_codex_items", "worker_task_and_receipt",
                      "thread_turn_ids"}.issubset(evidence), "真实Codex必须有执行证据")
        variants = execution.get("variants")
        _strings(variants, "缺执行变体")
        _require(len(set(variants)) == len(variants), "执行变体重复")
        _require(all(re.fullmatch(r"[a-z_]+", variant) for variant in variants), "非法变体")
        faults = case.get("declared_faults")
        _require(isinstance(faults, list), "缺故障标注")
        _require(execution["fault_injection"] == bool(faults), "故障开关与标注不一致")
        for fault in faults:
            _require(isinstance(fault, dict), "故障必须为对象")
            _require(all(isinstance(fault.get(key), str) and fault[key].strip()
                         for key in ("point", "kind", "effect", "does_not_claim")),
                     "故障需有注入点、效果及非主张边界")
        if case["id"].startswith("E"):
            reference = case.get("source_case", {})
            _require(case.get("adapter") == "source_case_runner", "旧题需引用已有执行器")
            _require({key: reference.get(key) for key in expected_source} == expected_source,
                     "单题来源 hash/revision 不一致")
            _require(reference.get("id") == case["id"] and reference["id"] in source_cases,
                     "source_case 错误或不存在")
            _require(set(reference.get("inherit", [])) ==
                     {"execution", "fixtures", "steps", "assertions", "boundary_probes", "manual_checks"},
                     "旧题须继承原完整定义，不复制修改")
            _require(set(case).isdisjoint({"fixtures", "assertions", "manual_checks", "boundary_probes"}),
                     "不要复制修改旧题标准")
            _require("query" not in entry and entry.get("query_source") ==
                     f"docs/agent-eval/cases.json#{case['id']}.steps", "旧query必须引用原步骤")
            original_execution = source_cases[case["id"]]["execution"]
            _require(execution["variants"] == original_execution["variants"], "不能删原执行变体")
            _require(execution["fault_injection"] == bool(original_execution["fault_injection"]),
                     "原题故障事实不一致")
            _require(execution["real_codex"] == (case["id"] == "E08"), "原题SDK真假事实不一致")
        else:
            _require(case.get("adapter") == "not_wired" and case.get("source_case") is None,
                     "新场景不能假称已经接线或改成旧题")
            _require(isinstance(entry.get("query"), str) and entry["query"].strip(), "新场景缺用户输入")
            _require(all(step["actor"] != "source_case" for step in steps), "新场景没有source_case")
        if case["layer"] == "account_agent":
            _require(execution["real_deepseek"], "账号Agent场景必须是真实DS")
        if case["layer"] == "codex_delivery":
            _require(execution["real_codex"], "真实交付层必须调用真实Codex")
    for case_id, expected_images in {"P01": 1, "P02": 3, "P03": 1}.items():
        execution = cases[case_id]["execution"]
        _require(execution["minimum_generated_images"] == expected_images, "前三条真实生产门槛错误")
        _require(execution["real_deepseek"] and execution["real_codex"], "前三条链路必须真实DS+Codex")
        _require(not execution["fault_injection"], "前三条必须正常真实生产，不靠注入冒充")
    _require(cases["P05"]["execution"]["fault_injection"] and
             "generation_call_counts" in cases["P05"]["evidence"], "离线恢复须声明故障并查新生成次数")
    _require(cases["P06"]["execution"]["minimum_generated_images"] == 0, "看图讨论不算新生图")
    _require(cases["B06"]["execution"]["variants"] == ["diagnostic", "authoritative"],
             "诊断与权威失败不能混成一个槽")
    _require(sum(len(case["execution"]["variants"]) for case in rows) == 52, "完整首尝试执行槽应为52")
    policy = dataset.get("scoring_policy", {})
    _require(policy.get("levels") ==
             ["execution_evidence", "business_state", "delivery_semantics", "content_quality"],
             "必须分开程序、业务、语义与质量")
    for section, keys in (
        ("execution_policy", ("entrypoint", "paid_opt_in", "isolation", "first_attempt",
                              "repeats", "scope", "faults", "probes")),
        ("scoring_policy", ("full_task", "raw_and_delivered", "program", "semantics",
                            "image_quality", "reporting", "comparison", "usage_policy")),
    ):
        _require(all(isinstance(dataset.get(section, {}).get(key), str) and dataset[section][key].strip()
                     for key in keys), "缺分层统计/基线与重复协议")
    return rows


class CoverageV3Tests(unittest.TestCase):
    def setUp(self):
        self.coverage = load_coverage()

    def rejects(self, mutate):
        changed = deepcopy(self.coverage)
        mutate(changed)
        with self.assertRaises(ValueError):
            validate_coverage(changed)

    def case(self, dataset, case_id):
        return next(case for case in dataset["cases"] if case["id"] == case_id)

    def test_fifty_planned_definitions_and_four_layers(self):
        rows = validate_coverage(self.coverage)
        self.assertEqual(len(rows), 50)
        self.assertTrue(all(row["status"] == "planned" for row in rows))
        self.assertEqual(self.coverage["layer_counts"], LAYER_COUNTS)

    def test_original_twelve_are_referenced_without_rewriting(self):
        rows = validate_coverage(self.coverage)
        references = [row["source_case"] for row in rows if row["source_case"]]
        self.assertEqual({reference["id"] for reference in references},
                         {f"E{index:02}" for index in range(1, 13)})
        self.assertTrue(all(reference["revision"] == "account-baseline-v2" for reference in references))

    def test_three_normal_real_production_journeys(self):
        validate_coverage(self.coverage)
        counts = [self.case(self.coverage, key)["execution"]["minimum_generated_images"]
                  for key in ("P01", "P02", "P03")]
        self.assertEqual(counts, [1, 3, 1])

    def test_variants_are_slots_not_new_scene_counts(self):
        rows = validate_coverage(self.coverage)
        self.assertEqual(sum(len(row["execution"]["variants"]) for row in rows), 52)
        self.assertEqual(len(rows), 50)

    def test_paid_and_fault_requirements_are_definitions_not_scores(self):
        rows = validate_coverage(self.coverage)
        self.assertEqual(sum(row["execution"]["minimum_generated_images"]
                             * len(row["execution"]["variants"]) for row in rows), 8)
        self.assertTrue(any(row["declared_faults"] for row in rows))
        self.assertEqual(self.case(self.coverage, "P06")["execution"]["requested_generated_images"], 0)

    def test_duplicate_and_missing_ids_rejected(self):
        self.rejects(lambda d: d["cases"][1].update(id="E01"))
        self.rejects(lambda d: d["cases"].pop())
        self.rejects(lambda d: self.case(d, "S01").update(id="S99"))

    def test_layer_count_and_actual_layer_drift_rejected(self):
        self.rejects(lambda d: d["layer_counts"].update(account_agent=25))
        self.rejects(lambda d: self.case(d, "S01").update(layer="account_agent"))

    def test_source_hash_revision_reference_and_copy_drift_rejected(self):
        self.rejects(lambda d: d["source_dataset"].update(sha256="0" * 64))
        self.rejects(lambda d: self.case(d, "E01")["source_case"].update(id="E02"))
        self.rejects(lambda d: self.case(d, "E01")["source_case"].update(revision="old"))
        self.rejects(lambda d: self.case(d, "E01").update(assertions=[]))
        self.rejects(lambda d: self.case(d, "E01")["source_case"].update(inherit=["steps"]))

    def test_fabricated_status_scores_and_freeze_rejected(self):
        self.rejects(lambda d: self.case(d, "P01").update(status="passed"))
        self.rejects(lambda d: d.update(status="completed"))
        self.rejects(lambda d: d["freeze"].update(status="frozen"))
        self.rejects(lambda d: self.case(d, "P01").update(score=1.0))
        self.rejects(lambda d: d.update(success_rate=0.93))

    def test_new_case_cannot_claim_old_adapter(self):
        self.rejects(lambda d: self.case(d, "P01").update(adapter="source_case_runner"))

    def test_missing_entry_steps_chain_or_oracle_rejected(self):
        self.rejects(lambda d: self.case(d, "P01")["user_entry"].pop("query"))
        self.rejects(lambda d: self.case(d, "S13").update(steps=[]))
        self.rejects(lambda d: self.case(d, "S13")["expected_chain"].update(actions=[]))
        for dimension in ("database", "files", "reply"):
            self.rejects(lambda d, dimension=dimension:
                         self.case(d, "P01")["oracle"].update({dimension: []}))

    def test_expected_answer_cannot_be_user_step(self):
        self.rejects(lambda d: self.case(d, "P01")["steps"][0].update(expected_answer="passed"))

    def test_missing_evidence_real_calls_and_receipts_rejected(self):
        self.rejects(lambda d: self.case(d, "P01").update(evidence=["final_ui"]))
        self.rejects(lambda d: self.case(d, "P01")["execution"].update(real_codex=False))
        self.rejects(lambda d: self.case(d, "A13")["execution"].update(real_deepseek=False))
        self.rejects(lambda d: self.case(d, "S04")["evidence"].remove("worker_task_and_receipt"))

    def test_fake_images_and_wrong_three_page_minimum_rejected(self):
        self.rejects(lambda d: self.case(d, "P01")["execution"].update(minimum_generated_images=0))
        self.rejects(lambda d: self.case(d, "P02")["execution"].update(
            minimum_generated_images=1, requested_generated_images=1))
        self.rejects(lambda d: self.case(d, "P03")["execution"].update(
            minimum_generated_images=True, requested_generated_images=True))

    def test_fault_flags_effects_and_boundaries_required(self):
        self.rejects(lambda d: self.case(d, "B06").update(declared_faults=[]))
        self.rejects(lambda d: self.case(d, "P01")["execution"].update(fault_injection=True))
        self.rejects(lambda d: self.case(d, "P05")["declared_faults"][0].pop("does_not_claim"))
        self.rejects(lambda d: self.case(d, "E09")["execution"].update(real_codex=True))

    def test_original_variants_and_recovery_generation_count_preserved(self):
        self.rejects(lambda d: self.case(d, "E09")["execution"].update(variants=["failed"]))
        self.rejects(lambda d: self.case(d, "B06")["execution"].update(variants=["diagnostic"]))
        self.rejects(lambda d: self.case(d, "P05")["evidence"].remove("generation_call_counts"))

    def test_reporting_and_repeat_policy_required(self):
        self.rejects(lambda d: d["scoring_policy"].pop("comparison"))
        self.rejects(lambda d: d["execution_policy"].pop("repeats"))
        self.rejects(lambda d: d["scoring_policy"].update(levels=["success"]))


if __name__ == "__main__":
    unittest.main()

