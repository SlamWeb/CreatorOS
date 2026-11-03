"""Time-aware qualifications of Judge verdicts, never rewriting raw evidence."""
from copy import deepcopy
from datetime import datetime
import json


VERSION = "judge-temporal-evidence-v1"


def verify_judge_evidence_version(manifest):
    """Prevent a newly executed trial from silently changing Judge semantics."""
    if manifest.get("model_configuration", {}).get("judge_evidence_version") != VERSION:
        raise ValueError("Frozen Judge evidence version does not match the active evaluator")


def build_temporal_evidence(browser, calls, background, *, background_observed_at=None):
    """Keep tool receipts, near-reply snapshots and final runs in distinct phases.

    A near-reply snapshot is captured *after* the reply becomes visible. It is
    not a transactionally exact snapshot of the instant the reply was made.
    """
    turns = []
    for index, turn in enumerate(browser.get("turns", []), 1):
        observations = []
        for call in calls:
            if call.get("turn_index") != index:
                continue
            tool = call.get("tool")
            if tool in {"get_content_run", "get_creator_tasks", "manage_production_task", "manage_plan", "manage_media_collection"}:
                # Its potentially newer state is still present in raw tool_calls,
                # but is not projected here; do not pretend the earlier receipt
                # is necessarily the last state the model saw.
                observations.append({"tool_call_id": call.get("tool_call_id"), "tool": tool, "unprojected_state": True})
                continue
            if tool != "start_content_run":
                continue
            if call.get("is_error"):
                observations.append({"tool_call_id": call.get("tool_call_id"), "tool": tool, "unprojected_state": True})
                continue
            try:
                result = json.loads(call.get("raw_result", ""))
            except (ValueError, TypeError):
                observations.append({"tool_call_id": call.get("tool_call_id"), "tool": tool, "unprojected_state": True})
                continue
            if not isinstance(result, dict):
                observations.append({"tool_call_id": call.get("tool_call_id"), "tool": tool, "unprojected_state": True})
                continue
            rows = result.get("items", [])
            if not isinstance(rows, list):
                observations.append({"tool_call_id": call.get("tool_call_id"), "tool": tool, "unprojected_state": True})
                continue
            runs = [{"run_id": row["run_id"], "status": row["status"]} for row in rows
                    if isinstance(row, dict) and isinstance(row.get("run_id"), str)
                    and isinstance(row.get("status"), str)]
            if runs and len(runs) == len(rows) and len({row["run_id"] for row in runs}) == len(runs):
                observations.append({"tool_call_id": call.get("tool_call_id"), "tool": call.get("tool"), "runs": runs})
            else:
                observations.append({"tool_call_id": call.get("tool_call_id"), "tool": tool, "unprojected_state": True})
        snapshot = turn.get("reply_observation")
        turns.append({"turn_index": index, "query": turn.get("query"), "assistant_reply": turn.get("response"),
                      "reply_observed_at": turn.get("reply_observed_at"), "tool_observations": observations,
                      "near_reply_observation": deepcopy(snapshot) if isinstance(snapshot, dict) else None})
    return {"version": VERSION, "turns": turns, "background_final": {
        "observed_at": background_observed_at,
        "runs": [{"id": row.get("id"), "status": row.get("status")} for row in background.get("runs", [])
                 if isinstance(row, dict)]}}


def _instant(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except ValueError:
        return None


def _has_consistent_reply_evidence(item, evidence):
    """Require named runs to agree across the receipt and nearby observation."""
    if not isinstance(evidence, dict) or evidence.get("version") != VERSION:
        return False
    index = item.get("turn_index")
    turns = evidence.get("turns")
    if type(index) is not int or not isinstance(turns, list) or not 1 <= index <= len(turns):
        return False
    turn = turns[index - 1]
    nearby = turn.get("near_reply_observation")
    if not isinstance(nearby, dict) or not isinstance(turn.get("assistant_reply"), str):
        return False
    answer_at = _instant(turn.get("reply_observed_at"))
    started_at = _instant(nearby.get("runs_observation_started_at"))
    finished_at = _instant(nearby.get("runs_observation_finished_at"))
    after_at = _instant((evidence.get("background_final") or {}).get("observed_at"))
    if (not all((answer_at, started_at, finished_at, after_at))
            or not answer_at <= started_at <= finished_at <= after_at
            or (finished_at - answer_at).total_seconds() > 5):
        return False
    observed = {}
    for call in turn.get("tool_observations", []):
        if call.get("unprojected_state"):
            return False
        for row in call.get("runs", []):
            if row["run_id"] in observed:
                return False
            observed[row["run_id"]] = row["status"]
    rows = nearby.get("runs", [])
    if not isinstance(rows, list):
        return False
    snapshot = {}
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                or not isinstance(row.get("status"), str) or row["id"] in snapshot):
            return False
        snapshot[row["id"]] = row["status"]
    ids = item.get("run_ids")
    if not isinstance(ids, list) or not ids or any(not isinstance(key, str) for key in ids) or len(ids) != len(set(ids)):
        return False
    if item.get("pass") is True and set(ids) != set(observed):
        return False
    return all(key in observed and key in snapshot and observed[key] == snapshot[key] for key in ids)


def qualify_temporal_judgments(case, judge):
    result = deepcopy(judge)
    if case.get("id") != "B42":
        return result
    for item in result.get("items", []):
        if item.get("id") != "b42-s3" or item.get("pass") is None:
            continue
        basis = item.get("temporal_basis")
        independent_failure = basis == "independent_violation" and item.get("pass") is False
        supported_reply = basis == "reply_observed" and _has_consistent_reply_evidence(item, judge.get("temporal_evidence"))
        if not (independent_failure or supported_reply):
            item["raw_pass"] = item.get("pass")
            item["pass"] = None
            item["evidence"] = "缺少可对应的回复附近状态证据；需复核。原判断：" + item.get("evidence", "")
    return result
