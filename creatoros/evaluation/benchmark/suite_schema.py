"""Versioned suite membership without changing legacy 50-case contracts."""
from __future__ import annotations

import re

VERSION = "benchmark-suite-v1"
CASE_ID = re.compile(r"^[A-Z][A-Z0-9_-]{0,39}$")
LEGACY_IDS = [f"B{number:02}" for number in range(1, 51)]


def case_ids(document):
    if not isinstance(document, dict) or not isinstance(document.get("cases"), list):
        raise ValueError("Dataset cases must be a list")
    cases = document["cases"]
    ids = [case.get("id") if isinstance(case, dict) else None for case in cases]
    if not ids or any(not isinstance(item, str) or not CASE_ID.fullmatch(item) for item in ids):
        raise ValueError("Suite case IDs must be nonempty safe identifiers")
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate dataset case IDs")
    suite = document.get("suite")
    if suite is None:
        if ids != LEGACY_IDS:
            raise ValueError("Legacy dataset requires all ordered cases B01..B50")
    else:
        if not isinstance(suite, dict) or set(suite) != {"version", "eligible_case_ids"} or suite.get("version") != VERSION:
            raise ValueError("Unsupported or malformed suite definition")
        if suite.get("eligible_case_ids") != ids:
            raise ValueError("Suite eligible_case_ids must exactly match ordered cases; placeholders are forbidden")
        if not isinstance(document.get("dataset_id"), str) or not document["dataset_id"].strip():
            raise ValueError("Versioned suite requires a dataset_id")
    return ids


def validate_selection(document, selected):
    ids = case_ids(document)
    if not isinstance(selected, list) or not selected or any(not isinstance(item, str) for item in selected):
        raise ValueError("Selected case IDs must be a nonempty list")
    if len(selected) != len(set(selected)) or any(item not in ids for item in selected):
        raise ValueError("Selected case IDs must be a unique subset of the suite")
    return ids
