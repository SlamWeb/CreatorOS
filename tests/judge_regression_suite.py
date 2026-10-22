"""Fixed, explainable semantic checks for the Judge v2 boundary cases.

This is a test-only scorer, not a production filter or a universal semantic
judge. Its explicit phrase/anchor rules are frozen with the dataset version.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from creatoros.integrations.skill_iteration_judge import parse_reply


DATASET = Path(__file__).parent / "fixtures" / "judge_semantics_v1.json"
# Filled once before the first live run; changing the dataset requires a new
# version, rather than quietly teaching this scorer to accept a failed answer.
DATASET_SHA256 = "509af8b435188119f37a40080b8b2e0bdb576ad2dd965a6f62260192ac8d0700"
SCORER_VERSION = "1.1-whitespace"

# These are positive claims of received evidence, not broad bans on words such
# as '不', '产物', or '发布文案'. Conditional checks of future outputs are legal.
RECEIVED_CLAIMS = (
    r"(?:已收到|已经收到|已提供|已经提供|已看到|已经看到|当前提供的|本次提供的)"
    r"[^。；;\n]{0,14}(?:候选产物|产物|候选图|试产图|发布正文|完整剧本)",
    r"(?:产物|候选图|试产图|发布正文|完整剧本)[^。；;\n]{0,10}(?:已收到|已提供|已看到)",
)
SCRIPT_CLAIMS = (
    r"(?:已收到|已经收到|已提供|已经提供|已看到|已经看到)[^。；;\n]{0,10}(?:完整剧本|场景剧本)",
    r"(?:完整剧本|场景剧本)[^。；;\n]{0,12}(?:显示|表明|呈现|可见)",
    r"剧本中(?:的|第|可以|可见)",
)
IMAGE_CLAIMS = (
    r"(?:已看到|已经看到|已收到|已经收到)[^。；;\n]{0,10}(?:候选图|产物图|试产图|图片|图像)",
    r"(?:候选图|产物图|试产图)[^。；;\n]{0,10}(?:显示|呈现|可见|看出)",
    r"(?:画面中|图中|图片中)(?:可以|可见|清楚|呈现|显示|有|的)",
)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_dataset(path=DATASET, *, require_frozen=True):
    path = Path(path)
    actual = digest(path)
    if require_frozen and (path.resolve() != DATASET.resolve() or actual != DATASET_SHA256):
        raise ValueError("Frozen Judge dataset differs; publish a new dataset version instead of rewriting v1")
    data = json.loads(path.read_text(encoding="utf-8"))
    ids = [case["id"] for case in data["cases"]]
    if len(ids) != len(set(ids)) or len(ids) != 8:
        raise ValueError("v1 must contain exactly eight uniquely identified frozen cases")
    return data


def _text(criteria):
    return "\n".join(str(item.get(field, "")) for item in criteria
                     for field in ("description", "evidence"))


def _normalize(text):
    # The first live baseline exposed '2 轮' vs '2轮'. This is a scorer fix,
    # not a changed answer; the original 7/8 report remains immutable.
    return re.sub(r"\s+", "", text).casefold()


def grade(case, raw_response):
    """Return actual and expected checks even when the product parser rejects it."""
    checks = []

    def check(name, expected, actual, passed):
        checks.append(dict(check=name, expected=expected, actual=actual, passed=bool(passed)))

    try:
        result = parse_reply(case["kind"], raw_response, case["payload"])
        check("product_schema_and_scope", "accepted by current product parser", "accepted", True)
    except Exception as error:
        check("product_schema_and_scope", "accepted by current product parser",
              dict(type=type(error).__name__, message=str(error)), False)
        return dict(passed=False, checks=checks, parsed=None)

    expected = case["expected"]
    additions = result.get("new_criteria", [])
    quality = result["criteria"] if case["kind"] == "rubric" else additions
    quality_text = _text(quality)
    domains = sorted({item["quality_kind"] for item in quality})
    if case["kind"] == "rubric":
        check("quality_domains", sorted(expected["domains"]), domains,
              domains == sorted(expected["domains"]))
    else:
        check("append_quality_domains", expected["domains"], domains,
              set(domains) <= set(expected["domains"]))
        wanted = [item["id"] for item in case["payload"]["criteria"]]
        actual = [item["criterion_id"] for item in result["scores"]]
        check("frozen_criteria_coverage", wanted, actual,
              len(actual) == len(wanted) and set(actual) == set(wanted))
        if not case["payload"]["allow_additions"]:
            check("no_unapproved_additions", [], additions, not additions)

    def groups(name, entries, text):
        for entry in entries:
            matches = [term for term in entry["any_of"] if _normalize(term) in _normalize(text)]
            check(name + ":" + entry["label"], dict(any_of=entry["any_of"]), matches, bool(matches))

    groups("required_quality", expected["required_quality_groups"], quality_text)
    workflow = result.get("workflow_constraints", [])
    groups("workflow_separate", expected["required_workflow_groups"], "\n".join(workflow))
    forbidden = [term for term in expected["forbidden_quality_terms"]
                 if _normalize(term) in _normalize(quality_text)]
    check("no_fixed_case_operation_or_wrong_domain_phrases", [], forbidden, not forbidden)
    # Claim checks apply to evidence/feedback only. A criterion may legitimately
    # describe what a future candidate should contain, without claiming receipt.
    evidence = "\n".join(item.get("evidence", "") for item in quality)
    evidence += "\n" + "\n".join(item["evidence"] for item in result.get("scores", []))
    evidence += "\n" + result.get("feedback", "")
    groups("required_response", expected.get("required_response_groups", []), evidence)
    patterns = []
    if case["kind"] == "rubric" or expected.get("forbid_received_claims"):
        patterns += list(RECEIVED_CLAIMS)
    if expected.get("forbid_script_claims"):
        patterns += list(SCRIPT_CLAIMS)
    if expected.get("forbid_image_claims"):
        patterns += list(IMAGE_CLAIMS)
    invented = [dict(pattern=pattern, match=match.group()) for pattern in patterns
                if (match := re.search(pattern, evidence))]
    check("no_fixed_case_unreceived_evidence_claims", [], invented, not invented)
    return dict(passed=all(item["passed"] for item in checks), checks=checks, parsed=result)
