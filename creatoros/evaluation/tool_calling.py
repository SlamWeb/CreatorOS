"""Run the versioned tool-calling suite with pre-registration and isolated benchmark worlds.

Live execution invokes paid models; it must never run implicitly on import or test discovery.
The existing non-video guard is not a network or process sandbox.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import threading

from creatoros.evaluation.benchmark.nonvideo_guard import policy
from creatoros.evaluation.benchmark.suite_schema import validate_selection

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "docs/agent-eval/tool-calling-v1/cases.json"
SUITE_ID = "creatoros-tool-calling-v1-20261107"
SAFE_CASES = frozenset({"B05", "B06", "B07", "B10"})
_BUNDLE = {
    "fixture_hash": ("creatoros/evaluation/benchmark/world.py", "creatoros/evaluation/benchmark/nonvideo_guard.py"),
    "scoring_hash": ("creatoros/evaluation/benchmark/scoring.py", "creatoros/evaluation/benchmark/grader.py"),
    "judge_hash": ("creatoros/evaluation/benchmark/semantic_judge.py", "creatoros/evaluation/benchmark/judge_timing.py",
                   "creatoros/evaluation/benchmark/judge_contradiction_guard.py", "creatoros/evaluation/benchmark/program_evidence_guard.py",
                   "creatoros/evaluation/benchmark/grounding_guard.py"),
}
_RESTRICTED_KEYS = {"api_key", "apikey", "authorization", "password", "secret", "access_token", "refresh_token", "credential"}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_files(paths) -> str:
    digest = hashlib.sha256()
    for name in sorted(paths):
        data = (ROOT / name).read_bytes()
        digest.update(name.encode("utf-8") + b"\0" + len(data).to_bytes(8, "big") + data)
    return digest.hexdigest()


def _driver_files():
    # The report, metrics and evidence projection influence the displayed result;
    # changing any benchmark framework module makes old comparisons ineligible.
    framework = sorted(path.relative_to(ROOT).as_posix()
                       for path in (ROOT / "creatoros/evaluation/benchmark").glob("*.py"))
    return [*framework, "creatoros/evaluation/tool_calling.py", "web/e2e/live-benchmark-v2.mjs"]


def suite_and_metadata(case_ids):
    from creatoros.evaluation.change_rules import plan
    raw = DATASET.read_bytes()
    dataset = json.loads(raw)
    validate_selection(dataset, case_ids)
    if dataset.get("dataset_id") != SUITE_ID:
        raise ValueError("Unexpected tool suite identity")
    if not set(case_ids) <= SAFE_CASES:
        raise ValueError("Only B05, B06, B07 and B10 have the permitted non-video policy")
    policy(case_ids)
    meta = {name: _hash_files(paths) for name, paths in _BUNDLE.items()}
    meta["driver_hash"] = _hash_files(_driver_files())
    meta.update(dataset_sha256=_sha(raw), execution_mode="nonvideo-no-publication-v1", model="deepseek-flash",
                trial_policy="first_attempt", usage_scope="provider_actual_agent",
                latency_scope="benchmark_case_wall_seconds", tool_calls_scope="observed_model_tool_calls",
                change_scope="tool", suite_role="specialist", judge_calibration="not_human_calibrated",
                requires_manifest_binding=True,
                change_requirements=plan(["tool"]), source_worktree=str(ROOT), fixture_version="benchmark-world-v2-2",
                guard_version="nonvideo-no-publication-v1",
                sandbox="isolated SQLite/files/process; nonvideo guard is not network isolation")
    return dataset, meta


def _definition_hash(definition):
    return _sha(json.dumps(definition, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _omit_sensitive_keys(value):
    """Registry rejects credential-named keys even when their values are redacted."""
    if isinstance(value, dict):
        return {k: _omit_sensitive_keys(v) for k, v in value.items() if str(k).lower() not in _RESTRICTED_KEYS}
    if isinstance(value, list):
        return [_omit_sensitive_keys(v) for v in value]
    return value


def _read_bounded(batch: Path, relative: str, limit=1024 * 1024):
    """Reject path traversal and linked evidence before consuming a bounded file."""
    return json.loads(_read_bytes(batch, relative, limit=limit).decode("utf-8-sig"))


def _read_bytes(batch: Path, relative: str, limit=1024 * 1024):
    from creatoros.web.observation import safe_path
    path = safe_path(batch, batch / relative)
    with path.open("rb") as source:
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Evidence exceeds size limit")
    return raw


def _actual_user_requests(batch, report_path, report, case_id):
    """Match saved user messages with browser-submitted queries, excluding host injections."""
    match = re.fullmatch(r"records/([a-f0-9]{32})/report\.json", report_path)
    if not match or report.get("run_id") != match.group(1) or report.get("dataset_id") != SUITE_ID:
        raise ValueError("Report identity is not verifiable")
    record = f"records/{match.group(1)}"
    messages = _read_bounded(batch, f"{record}/messages.json")
    browser = _read_bounded(batch, f"{record}/browser.json")
    if not isinstance(messages, list) or not isinstance(browser, dict) or browser.get("case_id") != case_id:
        raise ValueError("User messages or browser case identity unavailable")
    turns = browser.get("turns")
    if not isinstance(turns, list) or not turns or len(turns) > 16:
        raise ValueError("User query evidence unavailable or exceeds limit")
    user_texts = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            user_texts.append(content)
        elif isinstance(content, list):
            user_texts.extend(part["text"] for part in content if isinstance(part, dict)
                              and part.get("type") == "text" and isinstance(part.get("text"), str))
    found = []
    for turn in turns:
        query = turn.get("query") if isinstance(turn, dict) else None
        if not isinstance(query, str) or not query or len(query) > 4096 or query not in user_texts:
            raise ValueError("User query not independently matched or exceeds limit")
        user_texts.remove(query)
        found.append(query)
    return found


def result_from_row(batch, row, definition):
    """Project supported saved fields; never make missing evidence a pass."""
    from creatoros.web.observation import clean
    status = row.get("status")
    if status not in {"passed", "failed", "needs_review", "not_run", "unknown"}:
        status = "unknown"
    if (row.get("execution_status") not in {"completed", "succeeded"}
            or row.get("observed_error") in {"guard_evidence_missing_or_mismatched", "duplicate_trial_policy_failed"}):
        # Launcher/browser failures do not prove a model made an incorrect decision.
        status = "unknown"
    scores = row.get("scores") if isinstance(row.get("scores"), dict) else {}
    trajectory = row.get("trajectory") if isinstance(row.get("trajectory"), dict) else {}
    metrics = {"program_status": row.get("auto_status"), "semantic_status": row.get("semantic_status"),
               "critical_status": scores.get("critical_status"), "tool_calls": trajectory.get("tool_calls"),
               "input_tokens": None, "output_tokens": None, "elapsed_seconds": None, "cost": None,
               "usage_scope": "provider_actual_agent", "tool_calls_scope": "observed_model_tool_calls",
               "latency_scope": "benchmark_case_wall_seconds"}
    evidence = {"dataset_case_id": definition["id"], "title": definition.get("title"),
                "user_requests": [step.get("text") for step in definition.get("steps", []) if step.get("kind") == "user"],
                "user_requests_kind": "dataset_template", "actual_user_requests": None,
                "program_status": row.get("auto_status"), "semantic_status": row.get("semantic_status"),
                "critical_status": scores.get("critical_status"), "execution_status": row.get("execution_status"),
                "observed_error": row.get("observed_error"), "source_batch": batch.name,
                "source_record_paths": row.get("record_paths", []), "calls": [], "judgments": [],
                "limitations": ["Semantic judge has not been human-calibrated", "Full raw evidence remains in the source batch"]}
    guard = row.get("benchmark_guard") if isinstance(row.get("benchmark_guard"), dict) else {}
    if status == "passed" and (row.get("auto_status") != "passed" or row.get("semantic_status") != "passed"
                               or scores.get("critical_status") != "passed" or guard.get("status") != "passed"):
        status = "needs_review"
        evidence["limitations"].append("Passing requires complete program, semantic, critical and guard evidence")
    for name in ("program", "semantic"):
        score = scores.get(name) if isinstance(scores.get(name), dict) else {}
        for item in score.get("items", []):
            if isinstance(item, dict):
                evidence["judgments"].append({k: item.get(k) for k in ("id", "label", "question", "status", "detail") if item.get(k) is not None})
    records = row.get("record_paths")
    if isinstance(records, list) and len(records) == 1 and isinstance(records[0], str):
        report_path = records[0]
        try:
            report = _read_bounded(batch, report_path)
            if report.get("case_id") != definition["id"]:
                raise ValueError("Evidence case identity differs")
            try:
                evidence["actual_user_requests"] = _actual_user_requests(batch, report_path, report, definition["id"])
            except (OSError, ValueError, TypeError, AttributeError):
                evidence["limitations"].append("Actual user request evidence unavailable")
            if report.get("execution_status") in {"completed", "succeeded"}:
                metrics["elapsed_seconds"] = report.get("elapsed_seconds")
            agent = report.get("metrics", {}).get("agent", {})
            usage = agent.get("usage", {}) if isinstance(agent, dict) else {}
            if isinstance(usage, dict) and isinstance(agent, dict) and type(agent.get("model_calls")) is int and agent["model_calls"] > 0:
                metrics["input_tokens"] = usage.get("input_tokens")
                metrics["output_tokens"] = usage.get("output_tokens")
            metrics["cost"] = agent.get("cost") if isinstance(agent, dict) else None
            calls_path = str(Path(report_path).parent / "calls.json").replace("\\", "/")
            try:
                calls = _read_bounded(batch, calls_path)
                if isinstance(calls, list):
                    evidence["calls"] = [{k: call.get(k) for k in ("tool", "arguments", "turn_index", "is_error", "error_type")}
                                         for call in calls if isinstance(call, dict)][:100]
            except (OSError, ValueError, TypeError):
                evidence["limitations"].append("Tool call evidence unavailable")
        except (OSError, ValueError, TypeError, AttributeError):
            evidence["limitations"].append("Report evidence unavailable or invalid")
            if status == "passed":
                status = "needs_review"
    elif status == "passed":
        status = "needs_review"
        evidence["limitations"].append("Exactly one source record is required")
    return {"case_id": definition["id"], "case_definition_hash": _definition_hash(definition), "status": status,
            "summary": str(definition.get("title") or definition["id"]), "metrics": _omit_sensitive_keys(clean(metrics)),
            "evidence": _omit_sensitive_keys(clean(evidence))}


def sync_summary(registry, run_id, batch, definitions):
    """Append only observed cases; missing or incomplete records stay unobserved."""
    if not (batch / "summary.json").is_file():
        return 0
    summary = _read_bounded(batch, "summary.json", limit=16 * 1024 * 1024)
    if summary.get("dataset_id") != SUITE_ID:
        raise ValueError("Summary dataset identity differs")
    count = 0
    for row in summary.get("cases", []):
        case_id = row.get("case_id") if isinstance(row, dict) else None
        if case_id not in definitions:
            raise ValueError("Summary case was not planned")
        if row.get("record_paths"):
            registry.add_case_result(run_id, result_from_row(batch, row, definitions[case_id]))
            count += 1
    return count


def recover_registered(registry, run_id):
    """Explicitly recover locally verified evidence; never execute or retry a model.

    Hashes bind local files to each other, not to an independent signature. A
    coherent local rewrite cannot be detected without separately stored proof.
    """
    from creatoros.evaluation.benchmark.manifest import verify_manifest
    from creatoros.evaluation.benchmark.nonvideo_guard import evidence as guard_evidence, validate as validate_guard
    from creatoros.evaluation.benchmark.report import _case_status, _error_kind, _safety
    from creatoros.web.observation import safe_path

    run = registry.get_run(run_id)
    if run["status"] not in {"failed", "interrupted", "unknown"}:
        raise ValueError("Recovery requires a terminal failed/interrupted/unknown run")
    if run.get("suite_id") != SUITE_ID or run.get("suite_version") != "v1":
        raise ValueError("Recovery suite identity differs")
    batch = safe_path(ROOT / "data/agent-benchmark-v2", ROOT / "data/agent-benchmark-v2" / run_id)
    if batch.name != run_id:
        raise ValueError("Recovery batch identity differs")
    raw_manifest = _read_bytes(batch, "manifest.json", limit=16 * 1024 * 1024)
    recorded_manifest = run.get("manifest_sha256")
    if not isinstance(recorded_manifest, str) or recorded_manifest != _sha(raw_manifest):
        raise ValueError("Original registered manifest binding is missing or differs")
    # Perform path checks for the receipt and snapshots before verify_manifest
    # resolves paths and reads them.
    for name in ("manifest.json.sha256", "frozen-cases.json", "source-snapshot.zip"):
        safe_path(batch, batch / name)
    manifest = verify_manifest(batch / "manifest.json")["manifest"]
    version = registry.round_detail(run["round_id"])["round"].get("code_version")
    git = manifest.get("git") if isinstance(manifest.get("git"), dict) else {}
    if (not isinstance(version, str) or not re.fullmatch(r"[a-f0-9]{40}", version)
            or git.get("sha") != version or git.get("dirty") is not False or git.get("status_available") is not True):
        raise ValueError("Recovery requires a clean, fully identified source version")
    cases = run["planned_cases"]
    dataset, current_metadata = suite_and_metadata(cases)
    if run.get("metadata") != current_metadata:
        raise ValueError("Registered metadata differs from current evaluation source")
    if (manifest.get("source_selection") != "default" or manifest.get("dataset_id") != SUITE_ID
            or manifest.get("selected_case_ids") != cases
            or manifest.get("dataset_snapshot", {}).get("sha256") != current_metadata["dataset_sha256"]
            or manifest.get("model_configuration", {}).get("benchmark_run_id") != run_id
            or manifest.get("model_configuration", {}).get("provider_request_model") != current_metadata["model"]
            or manifest.get("execution_policy") != {"retries": 0, "first_attempts_only": True}):
        raise ValueError("Frozen manifest differs from registered run")
    if not validate_guard(manifest):
        raise ValueError("Frozen guard missing")
    frozen = _read_bounded(batch, "frozen-cases.json", limit=16 * 1024 * 1024)
    if frozen != dataset:
        raise ValueError("Frozen case definitions differ")
    summary = _read_bounded(batch, "summary.json", limit=16 * 1024 * 1024)
    rows = summary.get("cases") if isinstance(summary, dict) else None
    if (summary.get("dataset_id") != SUITE_ID or summary.get("planned_case_ids") != cases
            or not isinstance(rows, list) or [row.get("case_id") for row in rows if isinstance(row, dict)] != cases):
        raise ValueError("Saved summary does not match the registered plan")
    definitions = {case["id"]: case for case in frozen["cases"] if case["id"] in cases}
    records_root = safe_path(batch, batch / "records")
    observed_records = {}
    if not records_root.is_dir():
        raise ValueError("Original records directory missing")
    for directory in records_root.iterdir():
        safe_path(batch, directory)
        if not directory.is_dir() or not re.fullmatch(r"[a-f0-9]{32}", directory.name):
            raise ValueError("Unrecognized original trial directory")
        relative = f"records/{directory.name}/report.json"
        if not (directory / "report.json").exists():
            continue
        observed = _read_bounded(batch, relative)
        observed_case = observed.get("case_id") if isinstance(observed, dict) else None
        if (observed_case not in definitions or observed.get("run_id") != directory.name
                or observed.get("dataset_id") != SUITE_ID or observed_case in observed_records):
            raise ValueError("Unplanned, mismatched or duplicate trial report")
        observed_records[observed_case] = relative
    existing = {result["case_id"] for result in run.get("results", [])}
    previous_recoveries = {item["case_id"] for item in run.get("recoveries", [])}
    prepared = []
    for row in rows:
        case_id = row["case_id"]
        paths = row.get("record_paths")
        if not paths:
            if case_id in observed_records:
                raise ValueError("Summary omitted an original trial report")
            continue
        if not isinstance(paths, list) or len(paths) != 1 or not isinstance(paths[0], str):
            raise ValueError("Recovery requires exactly one original trial")
        record_path = paths[0]
        if observed_records.get(case_id) != record_path:
            raise ValueError("Summary trial does not match original records")
        match = re.fullmatch(r"records/([a-f0-9]{32})/report\.json", record_path)
        if not match:
            raise ValueError("Invalid original trial path")
        claim = _read_bounded(batch, f"claims/{case_id}.json")
        trial_binding = {"benchmark_batch_id": run_id, "benchmark_manifest_sha256": recorded_manifest}
        if claim != {"case_id": case_id, "run_id": match.group(1), "first_attempt": True, **trial_binding}:
            raise ValueError("First-attempt claim does not match report")
        raw_report = _read_bytes(batch, record_path)
        report = json.loads(raw_report.decode("utf-8-sig"))
        if (not isinstance(report, dict) or report.get("case_id") != case_id
                or report.get("run_id") != match.group(1) or report.get("dataset_id") != SUITE_ID
                or any(report.get(key) != value for key, value in trial_binding.items())):
            raise ValueError("Report identity differs from registered case")
        guard = guard_evidence(manifest, case_id, batch / "records" / match.group(1))
        if not isinstance(guard, dict) or guard.get("status") != "passed" or row.get("benchmark_guard") != guard:
            raise ValueError("Guard receipt missing or mismatched")
        for field in ("auto_status", "semantic_status", "execution_status", "scores"):
            if row.get(field) != report.get(field):
                raise ValueError("Summary does not match original report")
        for field in ("dimensions", "trajectory"):
            if row.get(field, {}) != report.get(field, {}):
                raise ValueError("Summary evidence does not match original report")
        safety_failures, safety_unknown = _safety(report)
        expected_status = _case_status(report, safety_failures)
        if expected_status == "passed" and safety_unknown:
            expected_status = "needs_review"
        if row.get("status") != expected_status or row.get("observed_error") != _error_kind(report.get("error")):
            raise ValueError("Summary status does not match original report")
        # Already imported records must remain untouched. Previously recovered
        # records are rechecked through the registry for idempotency.
        if case_id not in existing or case_id in previous_recoveries:
            prepared.append((result_from_row(batch, row, definitions[case_id]), _sha(raw_report)))
    if not prepared and not existing:
        raise ValueError("No recoverable case reports")
    manifest_sha = _sha(raw_manifest)
    for result, record_sha in prepared:
        registry.recover_case_result(run_id, result, source_batch=run_id,
                                     manifest_sha256=manifest_sha, record_sha256=record_sha)
    return {"run_id": run_id, "status": run["status"], "verified_cases": len(prepared),
            "manifest_sha256": manifest_sha, "trust": "local hashes; not an external signature"}


def run_registered(registry, *, round_data, run_id, cases, allow_paid, invoke=None):
    """Register first, then freeze and execute; external work is never retried."""
    if not allow_paid:
        raise ValueError("Live tool evaluation requires --allow-paid")
    dataset, metadata = suite_and_metadata(cases)
    definitions = {c["id"]: c for c in dataset["cases"] if c["id"] in cases}
    batch = ROOT / "data/agent-benchmark-v2" / run_id
    if batch.exists():
        raise ValueError("Batch already exists; a new run ID is required")
    registry.register_round(round_data)
    registry.register_run({"id": run_id, "round_id": round_data["id"], "suite_id": SUITE_ID,
                           "suite_version": "v1", "planned_cases": cases, "metadata": metadata})
    invoke = invoke or (lambda args: subprocess.run(args, cwd=ROOT, check=False).returncode)
    prefix = [sys.executable, "-m", "creatoros.evaluation.benchmark.run"]
    # Atomically claim this registered run. A duplicate runner must not mutate
    # the first runner's status or start a second paid model call.
    registry.claim_execution(run_id)
    started = False
    stop_heartbeat = threading.Event()
    heartbeat_errors = []
    def beat():
        while not stop_heartbeat.is_set():
            try:
                registry.heartbeat(run_id)
            except Exception as exc:
                heartbeat_errors.append(type(exc).__name__)
            if stop_heartbeat.wait(12):
                break
    heartbeat = threading.Thread(target=beat, name="eval-heartbeat", daemon=True)
    heartbeat.start()
    finalizing = False
    def finish(status, error=None):
        nonlocal finalizing
        finalizing = True
        stop_heartbeat.set()
        heartbeat.join()
        if heartbeat_errors:
            print("Evaluation heartbeat failed; inspect registry liveness", file=sys.stderr)
        registry.transition(run_id, status, error=error)
    def sync_partial():
        if not started:
            return None
        try:
            sync_summary(registry, run_id, batch, definitions)
            return None
        except Exception as exc:
            return type(exc).__name__
    try:
        code = invoke(prefix + ["freeze", "--batch", run_id, "--dataset", str(DATASET), "--cases", ",".join(cases), "--nonvideo-guard"])
        if code:
            finish("failed", error=f"freeze exited {code}")
            return {"run_id": run_id, "status": "failed", "stage": "freeze", "exit_code": code}
        if suite_and_metadata(cases)[1] != metadata:
            raise RuntimeError("Evaluation source changed during freeze; no model call was started")
        manifest_hash = _sha(_read_bytes(batch, "manifest.json", limit=16 * 1024 * 1024))
        receipt = _read_bytes(batch, "manifest.json.sha256", limit=1024).decode("ascii").strip()
        if receipt != manifest_hash:
            raise ValueError("Frozen manifest receipt differs")
        registry.bind_manifest(run_id, manifest_hash)
        registry.transition(run_id, "running")
        started = True
        code = invoke(prefix + ["run", "--batch", run_id, "--workers", "1"])
        sync_summary(registry, run_id, batch, definitions)
        finish("failed" if code else "completed", error=f"run exited {code}" if code else None)
        return {"run_id": run_id, "status": "failed" if code else "completed", "exit_code": code}
    except KeyboardInterrupt:
        if not finalizing:
            sync_error = sync_partial()
            finish("interrupted", error="Interrupted; external call state may be unknown" +
                   (f"; partial result sync failed: {sync_error}" if sync_error else ""))
        raise
    except Exception as exc:
        if not finalizing:
            sync_error = sync_partial()
            finish("unknown" if started else "failed", error=f"{type(exc).__name__}; inspect isolated batch" +
                   (f"; partial result sync failed: {sync_error}" if sync_error else ""))
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recover-run-id", help="Recover verified local evidence from this terminal run; no model call")
    parser.add_argument("--round-id")
    parser.add_argument("--title")
    parser.add_argument("--code-version", help="Version identity; use full commit and recorded dirty snapshot")
    parser.add_argument("--branch")
    parser.add_argument("--parent-round-id")
    parser.add_argument("--run-id", help="New immutable run and benchmark batch identifier")
    parser.add_argument("--cases", help="Explicit comma-separated eligible case IDs")
    parser.add_argument("--allow-paid", action="store_true", help="Explicitly enable real model calls")
    args = parser.parse_args()
    from creatoros.evaluation.registry import EvalRegistry
    if args.recover_run_id:
        if any((args.round_id, args.title, args.code_version, args.branch, args.parent_round_id, args.run_id, args.cases, args.allow_paid)):
            parser.error("Recovery cannot be combined with execution options")
        print(json.dumps(recover_registered(EvalRegistry(), args.recover_run_id), ensure_ascii=False))
        return 0
    if not all((args.round_id, args.title, args.code_version, args.run_id, args.cases)):
        parser.error("Execution requires --round-id, --title, --code-version, --run-id and --cases")
    result = run_registered(EvalRegistry(), round_data={"id": args.round_id, "title": args.title,
        "code_version": args.code_version, "branch": args.branch, "parent_round_id": args.parent_round_id,
        "merge_status": "developing", "changes": ["tool"]}, run_id=args.run_id,
        cases=args.cases.split(","), allow_paid=args.allow_paid)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
