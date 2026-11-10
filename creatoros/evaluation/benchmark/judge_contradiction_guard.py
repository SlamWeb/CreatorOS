"""Conservatively qualify explicit terminal self-corrections in Judge evidence."""
from copy import deepcopy
import re

VERSION = "judge-explicit-contradiction-v2"
# This is deliberately not a general natural-language contradiction detector.
_TERMINAL = re.compile(r"(?:^|[。；;\n])\s*(?:本项不成立，)?pass应为(true|false)[。.!！\s]*$", re.I)
_ENGLISH_TERMINAL = re.compile(r"(?:^|[。.!；;\n])\s*(?:correction:\s*)?pass\s+should\s+be\s+(true|false)[。.!！\s]*$", re.I)
_RETRACTION = re.compile(
    r"(?:^|[。；;\n])\s*(?:撤回\s*(true|false)\s*[,，;；]\s*改判\s*(true|false)"
    r"|(?:retract|withdraw)\s+(true|false)\s*[,;]\s*(?:change (?:the )?(?:verdict|pass) to|correct(?:ed)? to)\s+(true|false))"
    r"[。.!！\s]*$", re.I)
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
        match = _TERMINAL.search(evidence) or _ENGLISH_TERMINAL.search(evidence)
        corrected = match.group(1) if match else None
        if not match:
            match = _RETRACTION.search(evidence)
            if match:
                old, new = (match.group(1), match.group(2)) if match.group(1) else (match.group(3), match.group(4))
                if old.lower() != new.lower():
                    corrected = new
        if (corrected and _outside_quotes(evidence, match.start())
                and (corrected.lower() == "true") != value):
            item["raw_pass"] = value
            item["pass"] = None
            item["qualification"] = VERSION
            item["evidence"] += "；结构化判定与证据末尾明确自纠冲突，待复核。"
    return result
