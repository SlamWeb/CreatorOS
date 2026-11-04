"""Case-specific binary rubrics; program and semantics never share a weight."""
from collections.abc import Mapping
from math import isfinite


PROTOCOL = "case-rubrics-v3"


def validate_case_scoring(case):
    scoring = case.get("scoring")
    if not isinstance(scoring, Mapping):
        raise ValueError("Each current case requires explicit scoring")
    for field in ("intent", "expected_outcome"):
        if not isinstance(case.get(field), str) or not case[field].strip():
            raise ValueError(f"Case requires {field}")
    if case.get("evaluation_scope") not in {"outcome", "trajectory", "multi_turn"}:
        raise ValueError("Invalid evaluation scope")
    for field, rules in (("program_checks", scoring.get("program_checks")), ("rubric", case.get("rubric"))):
        if not isinstance(rules, list) or not rules:
            raise ValueError(f"Each case requires nonempty {field}")
        ids = []
        for rule in rules:
            if not isinstance(rule, Mapping) or not isinstance(rule.get("id"), str) or not rule["id"]:
                raise ValueError(f"Invalid {field} ID")
            text_field = "question" if field == "rubric" else "label"
            if not isinstance(rule.get(text_field), str) or not rule[text_field].strip():
                raise ValueError(f"Missing {field} {text_field}")
            if type(rule.get("critical", False)) is not bool:
                raise ValueError("critical must be boolean")
            ids.append(rule["id"])
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate {field} IDs")
    gates = scoring.get("critical_checks")
    if not isinstance(gates, list) or not gates or any(not isinstance(x, str) or not x for x in gates) or len(set(gates)) != len(gates):
        raise ValueError("Explicit unique critical_checks are required")


def validate_judge(case, result):
    """One exact, evidenced binary/null judgement per frozen semantic rule."""
    if not isinstance(result, Mapping) or not isinstance(result.get("items"), list):
        raise ValueError("Judge must return items")
    expected = [rule["id"] for rule in case["rubric"]]
    ids = []
    for item in result["items"]:
        if not isinstance(item, Mapping):
            raise ValueError("Malformed Judge item")
        ids.append(item.get("id"))
        if "pass" not in item or (item["pass"] is not None and type(item["pass"]) is not bool):
            raise ValueError("Judge must return explicit binary/null labels")
        if not isinstance(item.get("evidence"), str) or not item["evidence"].strip():
            raise ValueError("Judge must cite evidence or explain missing evidence")
    if len(ids) != len(expected) or set(ids) != set(expected):
        raise ValueError("Judge rubric coverage mismatch or duplicate IDs")
    return result


def _dimension(items):
    total = len(items)
    passed = sum(item["status"] == "passed" for item in items)
    failed = sum(item["status"] == "failed" for item in items)
    missing = total - passed - failed
    observed = passed + failed
    return {"score": 100 * passed / total if total and not missing else None,
            "observed_score": 100 * passed / observed if observed else None,
            "coverage": observed / total if total else None,
            "passed": passed, "failed": failed, "missing": missing, "total": total,
            "status": "failed" if failed else "needs_review" if missing else "passed" if total else "not_applicable",
            "items": items}


def score_case(case, grade, judge):
    validate_case_scoring(case)
    checks = {}
    for item in grade.get("checks", []):
        if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
            continue
        identifier = item["id"]
        if identifier in checks:
            checks[identifier] = {"status": "needs_review", "detail": "Duplicate evidence check ID"}
        else:
            checks[identifier] = item
    spec = case["scoring"]
    program = []
    for rule in spec["program_checks"]:
        found = checks.get(rule["id"], {})
        status = found.get("status", "needs_review")
        if status not in {"passed", "failed", "needs_review"}:
            status = "needs_review"
        program.append({**rule, "status": status, "detail": found.get("detail", "Required evidence check missing")})
    try:
        valid_judge = validate_judge(case, judge)
        from .judge_timing import qualify_temporal_judgments
        from .visual_guard import qualify_visual_judgments
        from .grounding_guard import qualify_grounding_judgments
        from .program_evidence_guard import qualify_program_judgments
        valid_judge = qualify_temporal_judgments(case, valid_judge)
        valid_judge = qualify_visual_judgments(case, valid_judge)
        valid_judge = qualify_grounding_judgments(case, valid_judge)
        valid_judge = qualify_program_judgments(case, valid_judge, grade)
        judgments = {item["id"]: item for item in valid_judge["items"]}
    except ValueError:
        judgments = {}
    semantic = []
    for rule in case["rubric"]:
        found = judgments.get(rule["id"], {})
        value = found.get("pass")
        semantic.append({**rule, "status": "passed" if value is True else "failed" if value is False else "needs_review",
                         "detail": found.get("evidence", "Semantic judgment missing or invalid")})
    critical = [{"id": identifier, "status": checks.get(identifier, {}).get("status", "needs_review")}
                for identifier in spec["critical_checks"]]
    critical.extend({"id": row["id"], "status": row["status"]} for row in program + semantic if row.get("critical"))
    critical_status = ("failed" if any(x["status"] == "failed" for x in critical) else
                       "needs_review" if any(x["status"] != "passed" for x in critical) else "passed")
    return {"protocol": PROTOCOL, "program": _dimension(program), "semantic": _dimension(semantic),
            "critical_status": critical_status, "critical_checks": critical, "combined_score": None}


def aggregate_scores(rows):
    """Macro-average cases, not rule counts. Missing trials never become zero."""
    result = {"protocol": PROTOCOL, "combined_score": None}
    for dimension in ("program", "semantic"):
        values = []
        for row in rows:
            scores = row.get("scores") or {}
            value = (scores.get(dimension) or {}).get("score") if scores.get("protocol") == PROTOCOL else None
            if type(value) in (int, float) and isfinite(value) and 0 <= value <= 100:
                values.append(value)
        complete = len(values) == len(rows) and bool(rows)
        mean = sum(values) / len(values) if values else None
        result[dimension] = {"score": mean if complete else None, "observed_score": mean,
                             "coverage": len(values) / len(rows) if rows else None,
                             "scored_cases": len(values), "planned_cases": len(rows)}
    return result
