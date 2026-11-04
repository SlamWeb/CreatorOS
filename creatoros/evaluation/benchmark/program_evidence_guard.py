"""Qualify semantic verdicts against independently captured program checks.

This does not replace the program score or certify Judge reasoning. It only
prevents an ungrounded semantic verdict from treating an unknown check as fact.
"""
from collections.abc import Mapping
from copy import deepcopy


VERSION = "program-evidence-qualification-v2"

# Only a failed check that directly contradicts the semantic claim can
# preserve an existing negative verdict in the presence of missing checks.
RULES = {
    ("B22", "b22-s1"): (("outcome_05",), frozenset({"outcome_05"})),
    ("B24", "b24-s1"): (("outcome_01", "outcome_02", "outcome_03", "outcome_04", "outcome_05"),
                          frozenset({"outcome_01"})),
    ("B43", "b43-s1"): (("outcome_04",), frozenset({"outcome_04"})),
}


def _statuses(source):
    """Accept grade['checks'] or payload['state_checks']; duplicates are unknown."""
    if isinstance(source, Mapping):
        source = source.get("checks")
    if not isinstance(source, list):
        return {}
    statuses = {}
    duplicates = set()
    for item in source:
        if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
            continue
        identifier = item["id"]
        if identifier in statuses:
            duplicates.add(identifier)
        status = item.get("status")
        statuses[identifier] = status if isinstance(status, str) and status in {"passed", "failed", "needs_review"} else "needs_review"
    for identifier in duplicates:
        statuses[identifier] = "needs_review"
    return statuses


def qualify_program_judgments(case, judge, checks):
    """Return a copy with unsupported verdicts set to null, never to passing.

    A negative verdict is retained if a direct independent program check has
    already failed, even when another required check is missing. Other program
    failures remain visible in their own dimension but do not automatically
    establish this particular semantic statement is false.
    """
    result = deepcopy(judge)
    statuses = _statuses(checks)
    for item in result.get("items", []):
        rule = RULES.get((case.get("id"), item.get("id")))
        if rule is None or item.get("pass") is None:
            continue
        required, direct_failures = rule
        observed = {identifier: statuses.get(identifier, "needs_review") for identifier in required}
        if all(status == "passed" for status in observed.values()):
            # For these atomic rules the independent program oracle checks the
            # same fact; a contrary negative cannot be trusted as a failure.
            # Do not promote it to success: preserve the disagreement.
            if (case.get("id"), item.get("id")) in {("B22", "b22-s1"), ("B43", "b43-s1")} and item.get("pass") is False:
                item["raw_pass"] = False
                item["pass"] = None
                item["qualification"] = VERSION
                item["evidence"] = "Judge判断与通过的直接程序检查冲突；此项待复核。原判断：" + item.get("evidence", "")
            continue
        if item.get("pass") is False and any(observed[key] == "failed" for key in direct_failures):
            continue
        item["raw_pass"] = item.get("pass")
        item["pass"] = None
        item["qualification"] = VERSION
        item["evidence"] = ("所需程序证据缺失、不完整或与语义判断不一致；"
                            "此项待复核。程序检查：" + str(observed) + "；原判断：" + item.get("evidence", ""))
    return result


def verify_guard_version(manifest):
    """Prevent newly frozen paid runs from silently changing this qualification."""
    if manifest.get("model_configuration", {}).get("program_evidence_guard_version") != VERSION:
        raise ValueError("Frozen program evidence guard version does not match the active evaluator")
