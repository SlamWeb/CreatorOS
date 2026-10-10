"""Local-only v1 task-set validation; never runs an Agent or assigns eval scores."""
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import re

DATASET_PATH = Path(__file__).resolve().parents[1] / "docs" / "agent-eval" / "cases.json"
CATEGORIES = {"security", "persistence", "state", "tools"}
VARIABLES = {
    "series_a_id": "series-eval-a", "series_b_id": "series-eval-b",
    "creator_b_id": "creator-eval-b", "run_b_id": "eval-run-b",
    "batch_a_id": "0123456789abcdef0123456789abcdef",
    "batch_b_id": "fedcba9876543210fedcba9876543210",
    "skill_a_id": "eval-skill--0123456789abcdef",
    "other_result_ref": "other-session-call",
    "other_archive_path": "other/messages.json.tool-results/result.txt",
}
EVENTS = {
    "reload_service", "prepare_interruption", "replay_request",
    "change_current_data", "release_research", "release_research_failure",
    "concurrent_skill_edit", "replay_queue_receipt",
}
TOKEN = re.compile(r"\{\{([a-z_]+)\}\}")
FAULT_CASES = {"E06", "E09", "E12"}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _text(value, label):
    _require(isinstance(value, str) and bool(value.strip()), label + ": empty text")
    unknown = set(TOKEN.findall(value)) - VARIABLES.keys()
    _require(not unknown, label + ": unknown template variable")


def _resolve(value):
    if isinstance(value, str):
        _text(value, "probe argument")
        return TOKEN.sub(lambda match: VARIABLES[match.group(1)], value)
    if isinstance(value, dict):
        return {key: _resolve(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve(item) for item in value]
    return value


def validate_dataset(dataset):
    # Registry imports only expose schemas; execute/Provider methods are never called.
    from creatoros.tools.definitions import tool_registry
    from creatoros.web.chat import ACCOUNT_TOOLS

    _require(dataset.get("schema_version") == 1, "unsupported schema version")
    _require(dataset.get("dataset_id") == "creatoros-account-agent-v1", "wrong dataset id")
    _require(dataset.get("status") in {"definitions_ready_not_run", "frozen_not_run"}
             and dataset.get("scope") == "creator",
             "task-set status/scope mismatch")
    _require(isinstance(dataset.get("revision"), str)
             and re.fullmatch(r"account-baseline-v[2-9][0-9]*", dataset["revision"]),
             "dataset revision missing")
    freeze = dataset.get("freeze")
    _require(isinstance(freeze, dict) and freeze.get("manifest") == "freeze-manifest.json",
             "freeze metadata missing")
    if dataset["status"] == "definitions_ready_not_run":
        _require(freeze.get("status") == "pending_executor_validation"
                 and freeze.get("frozen_at") is None, "premature freeze claim")
    else:
        try:
            frozen_at = datetime.fromisoformat(freeze["frozen_at"].replace("Z", "+00:00"))
        except (KeyError, TypeError, AttributeError, ValueError):
            raise ValueError("frozen dataset needs explicit timestamp") from None
        _require(freeze.get("status") == "frozen" and frozen_at.tzinfo is not None,
                 "invalid freeze state")
    policy = dataset.get("grading_policy", {})
    _require(set(policy) == {"program", "reply", "probes", "faults", "paths", "baseline"},
             "grading layers missing")
    for value in policy.values():
        _text(value, "grading policy")
    cases = dataset.get("cases")
    _require(isinstance(cases, list) and len(cases) == 12, "exactly 12 cases required")
    _require([case.get("id") for case in cases] == [f"E{i:02}" for i in range(1, 13)],
             "case ids must be unique E01..E12 in order")
    _require(Counter(case.get("category") for case in cases) == Counter({k: 3 for k in CATEGORIES}),
             "three cases per category required")
    _require(Counter(case.get("split") for case in cases) == Counter(dev=8, acceptance=4),
             "8 dev / 4 acceptance required")
    _require(dataset.get("split_policy", {}).get("dev") == 8
             and dataset.get("split_policy", {}).get("acceptance") == 4, "split policy mismatch")
    fixtures = dataset.get("fixture_catalog", {})
    evidence = set(dataset.get("evidence_catalog", []))
    _require(fixtures and evidence, "fixture/evidence catalogs missing")
    for case in cases:
        label = case["id"]
        _text(case.get("title"), label)
        _require(case.get("status") == "not_run", label + ": fabricated result")
        execution = case.get("execution", {})
        _require(set(execution) == {"entrypoint", "agent_provider", "dependency", "fault_injection", "variants"}
                 and execution["entrypoint"] == "real_frontend"
                 and execution["agent_provider"] == "real_deepseek", label + ": real browser/model entry missing")
        expected_dependency = ("controlled_fault" if label in FAULT_CASES else
                               "real_codex_research" if label == "E08" else "isolated_business")
        _require(execution["dependency"] == expected_dependency, label + ": wrong execution layer")
        faults = execution["fault_injection"]
        _require(isinstance(faults, list) and bool(faults) == (label in FAULT_CASES),
                 label + ": fault injection not explicit")
        for fault in faults:
            _text(fault, label + " fault")
        _require(execution["variants"] == (["failed", "unknown"] if label == "E09" else ["default"]),
                 label + ": variant coverage missing")
        selected = case.get("fixtures", [])
        _require(selected and all(name in fixtures for name in selected), label + ": unknown fixture")
        if label == "E08":
            _require("real_codex_research" in selected and "controlled_research_ready" not in selected,
                     "E08: normal research must be real Codex")
        steps = case.get("steps", [])
        _require(steps and any(step.get("kind") == "user" for step in steps), label + ": no user input")
        for step in steps:
            if step.get("kind") == "user":
                _require(set(step) == {"kind", "text"}, label + ": oracle in user step")
                _text(step["text"], label)
            else:
                _require(set(step) == {"kind", "name", "details"} and step["kind"] == "event"
                         and step["name"] in EVENTS, label + ": invalid control event")
                _text(step["details"], label)
                _require(label != "E08" or step["name"] != "release_research",
                         "E08: cannot replace live research with controlled ready")
        checks = case.get("assertions", [])
        _require(len(checks) >= 3, label + ": incomplete checks")
        _require(len({check["id"] for check in checks}) == len(checks), label + ": duplicate assertion")
        for check in checks:
            _text(check.get("pass"), label)
            refs = check.get("evidence", [])
            _require(refs and set(refs) <= evidence, label + ": unknown/missing evidence")
        review = case.get("manual_checks", [])
        _require(review, label + ": manual review missing")
        for item in review:
            _text(item, label)
        probes = case.get("boundary_probes", [])
        if case["category"] == "security":
            _require(probes, label + ": security case missing mandatory guard probe")
        for probe in probes:
            tool = probe.get("tool")
            _require(tool in ACCOUNT_TOOLS and tool in tool_registry, label + ": unavailable probe tool")
            _require(isinstance(probe.get("arguments"), dict), label + ": probe arguments missing")
            _text(probe.get("expect"), label)
            try:
                tool_registry[tool].parse_arguments(json.dumps(_resolve(probe["arguments"])))
            except ValueError as error:
                raise ValueError(label + ": probe violates current tool schema") from error
    return cases


def load_dataset(path=DATASET_PATH):
    dataset = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_dataset(dataset)
    return dataset


def main():
    dataset = load_dataset()
    print(json.dumps({"dataset_id": dataset["dataset_id"], "revision": dataset["revision"],
                      "freeze_status": dataset["freeze"]["status"], "cases": len(dataset["cases"]),
                      "baseline_executions": sum(len(case["execution"]["variants"]) for case in dataset["cases"]),
                      "structure_valid": True, "model_runs": 0, "case_status": "not_run"},
                     ensure_ascii=False))
    for case in dataset["cases"]:
        print(f"{case['id']} [{case['split']}] {case['title']}")


if __name__ == "__main__":
    main()
