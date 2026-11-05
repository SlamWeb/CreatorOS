"""Conservatively qualify explicit terminal self-corrections in Judge evidence."""
from copy import deepcopy
import re

VERSION = "judge-explicit-contradiction-v1"
# This is deliberately not a general natural-language contradiction detector.
_TERMINAL = re.compile(r"(?:^|[。；;\n])\s*(?:本项不成立，)?pass应为(true|false)[。.!！\s]*$", re.I)
_PAIRS = {"“": "”", "‘": "’", "「": "」", "『": "』", '"': '"', "`": "`"}


def _outside_quotes(text, end):
    stack = []
    for char in text[:end]:
        if stack and char == stack[-1]:
            stack.pop()
        elif char in _PAIRS and (not stack or stack[-1] not in {'"', '`'}):
            stack.append(_PAIRS[char])
    return not stack


def qualify_explicit_contradictions(case, judge):
    result = deepcopy(judge)
    for item in result.get("items", []):
        evidence, value = item.get("evidence"), item.get("pass")
        if type(value) is not bool or not isinstance(evidence, str):
            continue
        match = _TERMINAL.search(evidence)
        if (match and _outside_quotes(evidence, match.start(1))
                and (match.group(1).lower() == "true") != value):
            item["raw_pass"] = value
            item["pass"] = None
            item["qualification"] = VERSION
            item["evidence"] += "；结构化判定与证据末尾明确自纠冲突，待复核。"
    return result
