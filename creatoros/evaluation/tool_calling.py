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
import tempfile
import threading

from creatoros.evaluation.benchmark.nonvideo_guard import policy
from creatoros.evaluation.benchmark.suite_schema import validate_selection

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "docs/agent-eval/tool-calling-v1/cases.json"
SUITE_ID = "creatoros-tool-calling-v1-20261107"
REGRESSION_DATASET = ROOT / "docs/agent-eval/benchmark-v2/cases.json"
REGRESSION_SUITE_ID = "creatoros-trace-benchmark-v3.4-20261105"
NATURAL_SUITE_ID = "creatoros-natural-benchmark-v2-20261105"
EXPANSION_SUITE_ID = "creatoros-expansion-50-v1-20261105"
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
    extra = ["creatoros/evaluation/tool_calling.py", "creatoros/evaluation/regression_runner.py",
             "creatoros/evaluation/regression_catalog.py", "creatoros/evaluation/change_rules.py",
             "web/e2e/live-benchmark-v2.mjs"]
    return [*framework, *(name for name in extra if (ROOT / name).is_file())]


def _execution_dataset(local_dispatch, catalog_binding):
    """Resolve the immutable dataset authorized for the bound guard version."""
    if local_dispatch is True:
        from creatoros.evaluation.benchmark.local_dispatch_guard import VERSION
        local_dispatch = VERSION
    if catalog_binding is None or local_dispatch not in {"local-dispatch-v1", "local-dispatch-v2", "local-dispatch-v3", "local-dispatch-v4", "local-dispatch-v5"}:
        return DATASET, SUITE_ID
    from creatoros.evaluation.regression_catalog import _ADAPTER_SOURCE_BY_VERSION, _snapshot_path
    from creatoros.evaluation.benchmark.natural_suite import canonical_digest
    from creatoros.evaluation.benchmark.manifest import _dataset
    try:
        if local_dispatch in {"local-dispatch-v4", "local-dispatch-v5"}:
            from creatoros.evaluation.regression_catalog import _ADAPTER_SOURCE_BY_NAMESPACE
            from creatoros.evaluation.regression_runner import selection_namespace
            namespace = selection_namespace(catalog_binding["selected_instance_ids"])
            digest = _ADAPTER_SOURCE_BY_NAMESPACE[local_dispatch][namespace]
        else:
            namespace = "base"
            digest = _ADAPTER_SOURCE_BY_VERSION[local_dispatch]
        expected_id = SUITE_ID
        if local_dispatch in {"local-dispatch-v3", "local-dispatch-v4", "local-dispatch-v5"}:
            expected_id = {"base": REGRESSION_SUITE_ID, "natural": NATURAL_SUITE_ID, "expansion": EXPANSION_SUITE_ID}[namespace]
            source = catalog_binding["frozen_catalog"]["source"]
            bases = [item for item in source["sources"] if item.get("namespace") == namespace]
            if (len(bases) != 1 or bases[0].get("dataset_id") != expected_id
                    or bases[0].get("canonical_sha256") != digest):
                raise ValueError("Catalog base dataset identity or authorization differs")
            if local_dispatch in {"local-dispatch-v4", "local-dispatch-v5"} and source.get("adapter_source_sha256_by_namespace") != _ADAPTER_SOURCE_BY_NAMESPACE[local_dispatch]:
                raise ValueError("Catalog namespace authorizations differ")
        path = _snapshot_path(ROOT, digest)
        dataset = _dataset(path)
        if dataset.get("dataset_id") != expected_id or canonical_digest(dataset) != digest:
            raise ValueError("Frozen base dataset digest or identity differs")
        return path, expected_id
    except (KeyError, TypeError, OSError) as error:
        raise ValueError("Frozen base dataset unavailable") from error


def suite_and_metadata(case_ids, *, local_dispatch=False, catalog_binding=None, frozen_catalog=False):
    from creatoros.evaluation.change_rules import plan
    from creatoros.evaluation.benchmark.local_dispatch_guard import VERSION as LOCAL_VERSION
    if local_dispatch is True:
        local_dispatch = LOCAL_VERSION
    namespace = None
    if local_dispatch in {"local-dispatch-v4", "local-dispatch-v5"}:
        if catalog_binding is None:
            raise ValueError("v4 requires catalog binding")
        from creatoros.evaluation.regression_runner import selection_namespace
        namespace = selection_namespace(catalog_binding["selected_instance_ids"])
    dataset_path, expected_id = _execution_dataset(local_dispatch, catalog_binding)
    raw = dataset_path.read_bytes()
    dataset = json.loads(raw)
    validate_selection(dataset, case_ids)
    if dataset.get("dataset_id") != expected_id:
        raise ValueError("Unexpected tool suite identity")
    from creatoros.evaluation.benchmark.local_dispatch_guard import CASES_BY_NAMESPACE, CASES_BY_VERSION
    local_version = local_dispatch
    allowed = CASES_BY_NAMESPACE[local_version][namespace] if namespace else (CASES_BY_VERSION.get(local_version, frozenset()) if local_dispatch else SAFE_CASES)
    if not set(case_ids) <= allowed:
        raise ValueError("Only B05, B06, B07 and B10 have the permitted non-video policy")
    policy(case_ids, local_dispatch=local_version, source_namespace=namespace)
    meta = {name: _hash_files(paths) for name, paths in _BUNDLE.items()}
    meta["driver_hash"] = _hash_files(_driver_files())
    scopes = catalog_binding.get("change_scopes") if isinstance(catalog_binding, dict) else ["tool"]
    requirements = plan(scopes)
    meta.update(dataset_sha256=_sha(raw), execution_mode=(local_version if local_dispatch else "nonvideo-no-publication-v1"), model="deepseek-flash",
                trial_policy="first_attempt", usage_scope="provider_actual_agent",
                latency_scope="benchmark_case_wall_seconds", tool_calls_scope="observed_model_tool_calls",
                change_scope=(",".join(scopes)), suite_role=("regression" if catalog_binding is not None else "specialist"), judge_calibration="not_human_calibrated",
                requires_manifest_binding=True,
                change_requirements=requirements, source_worktree=str(ROOT), fixture_version="benchmark-world-v2-2",
                guard_version="nonvideo-no-publication-v1",
                sandbox="isolated SQLite/files/process; nonvideo guard is not network isolation")
    if local_dispatch:
        meta["guard_version"] = local_version
        meta["sandbox"] = "local dispatch guard and deferred workers; not an OS or network sandbox"
    if namespace:
        meta["source_namespace"] = namespace
    if catalog_binding is not None:
        if not local_dispatch:
            raise ValueError("Catalog execution requires the local dispatch guard")
        from creatoros.evaluation.regression_runner import validate_binding, validate_frozen_binding
        meta.update((validate_frozen_binding if frozen_catalog else validate_binding)(case_ids, catalog_binding))
        membership = catalog_binding["frozen_catalog"]["membership"]["cases"]
        selected = set(catalog_binding["selected_instance_ids"])
        if {row["adapter"] for row in membership if row["instance_id"] in selected} != {local_version}:
            raise ValueError("Catalog adapter and execution guard version differ")
        definitions = {case["id"]: case for case in dataset["cases"]}
        for row in membership:
            if row["instance_id"] in selected:
                identity = row["instance_id"].removeprefix((namespace or "base") + ":")
                if identity not in definitions or row["case_sha256"] != _definition_hash(definitions[identity]):
                    raise ValueError("Catalog case definition differs from execution dataset")
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


def _actual_user_requests(batch, report_path, report, case_id, *, dataset_id=SUITE_ID):
    """Match saved user messages with browser-submitted queries, excluding host injections."""
    match = re.fullmatch(r"records/([a-f0-9]{32})/report\.json", report_path)
    if not match or report.get("run_id") != match.group(1) or report.get("dataset_id") != dataset_id:
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


def result_from_row(batch, row, definition, *, classify_connection_error=False, dataset_id=SUITE_ID):
    """Project supported saved fields; never make missing evidence a pass."""
    from creatoros.web.observation import clean
    status = row.get("status")
    if status not in {"passed", "failed", "needs_review", "not_run", "unknown"}:
        status = "unknown"
    scores = row.get("scores") if isinstance(row.get("scores"), dict) else {}
    critical = scores.get("critical_checks") if isinstance(scores.get("critical_checks"), list) else []
    critical_ids = {"local_dispatch_guard", "foreign_unchanged", "no_private_leak", "forbidden_actions"}
    program_rules = definition.get("scoring", {}).get("program_checks", [])
    semantic_rules = definition.get("rubric", [])
    if isinstance(program_rules, list):
        critical_ids.update(rule.get("id") for rule in program_rules if isinstance(rule, dict) and rule.get("critical") is True)
    if isinstance(semantic_rules, list):
        critical_ids.update(rule.get("id") for rule in semantic_rules if isinstance(rule, dict) and rule.get("critical") is True)
    dimensions = row.get("dimensions") if isinstance(row.get("dimensions"), dict) else {}
    proven_critical_failure = (scores.get("critical_status") == "failed" and any(
        isinstance(item, dict) and item.get("id") in critical_ids and item.get("status") == "failed" for item in critical))
    proven_critical_failure = proven_critical_failure or dimensions.get("boundary_enforced") == "failed"
    semantic = scores.get("semantic") if isinstance(scores.get("semantic"), dict) else {}
    if isinstance(semantic_rules, list):
        semantic_ids = {rule.get("id") for rule in semantic_rules if isinstance(rule, dict) and rule.get("critical") is True}
        proven_critical_failure = proven_critical_failure or any(
            isinstance(item, dict) and item.get("id") in semantic_ids and item.get("status") == "failed"
            for item in semantic.get("items", []))
    program = scores.get("program") if isinstance(scores.get("program"), dict) else {}
    program_ids = {rule.get("id") for rule in program_rules if isinstance(rule, dict)} if isinstance(program_rules, list) else set()
    proven_program_failure = (row.get("execution_status") in {"completed", "succeeded"}
        and any(isinstance(item, dict) and item.get("id") in program_ids and item.get("status") == "failed"
                for item in program.get("items", [])))
    if proven_critical_failure or proven_program_failure:
        # A concrete business/safety failure cannot be laundered into unknown
        # by missing guard evidence or an unrelated infrastructure error.
        status = "failed"
    elif (row.get("execution_status") not in {"completed", "succeeded"}
            or row.get("observed_error") in {"guard_evidence_missing_or_mismatched", "duplicate_trial_policy_failed"}):
        # Launcher/browser failures do not prove a model made an incorrect decision.
        status = "unknown"
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
    if classify_connection_error:
        evidence["critical_checks"] = [{k: item.get(k) for k in ("id", "status", "detail") if item.get(k) is not None}
                                       for item in critical if isinstance(item, dict)]
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
            if (classify_connection_error
                    and any(isinstance(item, dict) and item.get("id") == "browser_completed"
                            and item.get("status") == "failed" for item in critical)):
                requests_path = str(Path(report_path).parent / "requests.json").replace("\\", "/")
                try:
                    requests = _read_bounded(batch, requests_path)
                    connection_observed = isinstance(requests, list) and any(
                        isinstance(request, dict) and request.get("purpose") == "agent"
                        and isinstance(request.get("error"), dict)
                        and request["error"].get("kind") == "APIConnectionError" for request in requests)
                    if connection_observed:
                        evidence["agent_connection_error_observed"] = True
                        evidence["limitations"].append("Agent API connection error observed; relationship to incomplete turn is unverified")
                    browser_path = str(Path(report_path).parent / "browser.json").replace("\\", "/")
                    browser = _read_bounded(batch, browser_path)
                    turns = browser.get("turns") if isinstance(browser, dict) else None
                    last_turn = turns[-1] if isinstance(turns, list) and turns and isinstance(turns[-1], dict) else {}
                    turn_id = last_turn.get("turn_id")
                    # No request-to-turn identity means no causal attribution:
                    # an earlier connection error may have recovered later.
                    matched = [request for request in requests if isinstance(request, dict)
                               and request.get("purpose") == "agent" and request.get("turn_id") == turn_id] if isinstance(requests, list) and turn_id else []
                    last_request = matched[-1] if matched else {}
                    connection_failed = (not proven_critical_failure and not proven_program_failure
                        and isinstance(turn_id, str) and bool(turn_id)
                        and last_turn.get("completed") is False and last_turn.get("session_status") == "failed"
                        and isinstance(last_request.get("error"), dict)
                        and last_request["error"].get("kind") == "APIConnectionError")
                    if connection_failed:
                        status = "unknown"
                        evidence["limitations"].pop()
                        evidence["limitations"].append("Agent API connection failed; incomplete browser execution does not establish an agent failure")
                except (OSError, ValueError, TypeError):
                    pass
            try:
                evidence["actual_user_requests"] = _actual_user_requests(batch, report_path, report, definition["id"], dataset_id=dataset_id)
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


def sync_summary(registry, run_id, batch, definitions, *, classify_connection_error=False, dataset_id=SUITE_ID):
    """Append only observed cases; missing or incomplete records stay unobserved."""
    if not (batch / "summary.json").is_file():
        return 0
    summary = _read_bounded(batch, "summary.json", limit=16 * 1024 * 1024)
    if summary.get("dataset_id") != dataset_id:
        raise ValueError("Summary dataset identity differs")
    run = registry.get_run(run_id)
    manifest_sha = run.get("manifest_sha256")
    raw_manifest = _read_bytes(batch, "manifest.json", limit=16 * 1024 * 1024)
    if not isinstance(manifest_sha, str) or _sha(raw_manifest) != manifest_sha:
        raise ValueError("Registered manifest binding missing or differs")
    manifest = json.loads(raw_manifest.decode("utf-8-sig"))
    from creatoros.evaluation.benchmark.nonvideo_guard import validate as validate_guard
    config = manifest.get("model_configuration", {})
    if (run.get("suite_id") != dataset_id or manifest.get("dataset_id") != dataset_id
            or manifest.get("selected_case_ids") != run.get("planned_cases")
            or config.get("benchmark_run_id") != run_id
            or config.get("catalog_binding") != run.get("metadata", {}).get("catalog_binding")
            or manifest.get("dataset_snapshot", {}).get("sha256") != run.get("metadata", {}).get("dataset_sha256")
            or not validate_guard(manifest)):
        raise ValueError("Frozen dataset, selection, or guard differs")
    if (config.get("benchmark_guard", {}).get("local_dispatch") in {"local-dispatch-v4", "local-dispatch-v5"}
            and config["benchmark_guard"].get("source_namespace") != run.get("metadata", {}).get("source_namespace")):
        raise ValueError("Registered source namespace differs from frozen guard")
    count = 0
    for row in summary.get("cases", []):
        case_id = row.get("case_id") if isinstance(row, dict) else None
        if case_id not in definitions:
            raise ValueError("Summary case was not planned")
        if row.get("record_paths"):
            paths = row["record_paths"]
            match = re.fullmatch(r"records/([a-f0-9]{32})/report\.json", paths[0]) if isinstance(paths, list) and len(paths) == 1 and isinstance(paths[0], str) else None
            if not match:
                raise ValueError("First-attempt report identity unavailable")
            expected = {"benchmark_batch_id": run_id, "benchmark_manifest_sha256": manifest_sha}
            claim = _read_bounded(batch, f"claims/{case_id}.json")
            if claim != {"case_id": case_id, "run_id": match.group(1), "first_attempt": True, **expected}:
                raise ValueError("First-attempt claim does not match")
            report = _read_bounded(batch, paths[0])
            if (report.get("case_id") != case_id or report.get("run_id") != match.group(1)
                    or report.get("dataset_id") != dataset_id or any(report.get(k) != v for k, v in expected.items())):
                raise ValueError("Report does not match bound batch and dataset")
            for field in ("auto_status", "semantic_status", "execution_status", "scores", "dimensions", "trajectory"):
                if row.get(field, {}) != report.get(field, {}):
                    raise ValueError("Summary evidence differs from original report")
            from creatoros.evaluation.benchmark.nonvideo_guard import evidence as guard_evidence
            guard = guard_evidence(manifest, case_id, batch / "records" / match.group(1))
            if not isinstance(guard, dict) or row.get("benchmark_guard") != guard:
                raise ValueError("Guard receipt missing or mismatched")
            from creatoros.evaluation.benchmark.report import _case_status, _error_kind, _safety
            failures, unknown = _safety(report)
            expected_status = _case_status(report, failures)
            if expected_status == "passed" and unknown:
                expected_status = "needs_review"
            if row.get("status") != expected_status or row.get("observed_error") != _error_kind(report.get("error")):
                raise ValueError("Summary status differs from original report")
            registry.add_case_result(run_id, result_from_row(batch, row, definitions[case_id],
                                                             classify_connection_error=classify_connection_error, dataset_id=dataset_id))
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
    if (run.get("suite_id"), run.get("suite_version")) not in {(SUITE_ID, "v1"), (REGRESSION_SUITE_ID, "v3.4"), (NATURAL_SUITE_ID, "v2"), (EXPANSION_SUITE_ID, "v1")}:
        raise ValueError("Recovery suite identity differs")
    dataset_id = run["suite_id"]
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
    binding = run.get("metadata", {}).get("catalog_binding")
    from creatoros.evaluation.benchmark.local_dispatch_guard import VERSIONS
    execution_mode = run.get("metadata", {}).get("execution_mode")
    local_dispatch = execution_mode if execution_mode in VERSIONS else False
    dataset, current_metadata = suite_and_metadata(cases, local_dispatch=local_dispatch, catalog_binding=binding,
                                                   frozen_catalog=binding is not None)
    if dataset.get("dataset_id") != dataset_id:
        raise ValueError("Registered suite and execution dataset differ")
    if run.get("metadata") != current_metadata:
        raise ValueError("Registered metadata differs from current evaluation source")
    if (manifest.get("source_selection") != "default" or manifest.get("dataset_id") != dataset_id
            or manifest.get("selected_case_ids") != cases
            or manifest.get("dataset_snapshot", {}).get("sha256") != current_metadata["dataset_sha256"]
            or manifest.get("model_configuration", {}).get("benchmark_run_id") != run_id
            or manifest.get("model_configuration", {}).get("catalog_binding") != binding
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
    if (summary.get("dataset_id") != dataset_id or summary.get("planned_case_ids") != cases
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
                or observed.get("dataset_id") != dataset_id or observed_case in observed_records):
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
                or report.get("run_id") != match.group(1) or report.get("dataset_id") != dataset_id
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
            prepared.append((result_from_row(batch, row, definitions[case_id],
                              classify_connection_error=execution_mode in {"local-dispatch-v2", "local-dispatch-v3", "local-dispatch-v4", "local-dispatch-v5"}, dataset_id=dataset_id), _sha(raw_report)))
    if not prepared and not existing:
        raise ValueError("No recoverable case reports")
    manifest_sha = _sha(raw_manifest)
    for result, record_sha in prepared:
        registry.recover_case_result(run_id, result, source_batch=run_id,
                                     manifest_sha256=manifest_sha, record_sha256=record_sha)
    return {"run_id": run_id, "status": run["status"], "verified_cases": len(prepared),
            "manifest_sha256": manifest_sha, "trust": "local hashes; not an external signature"}


def run_registered(registry, *, round_data, run_id, cases, allow_paid, invoke=None, local_dispatch=False, catalog_binding=None):
    """Register first, then freeze and execute; external work is never retried."""
    if not allow_paid:
        raise ValueError("Live tool evaluation requires --allow-paid")
    if catalog_binding is not None and round_data.get("changes") != catalog_binding.get("change_scopes"):
        raise ValueError("Round change scopes differ from frozen catalog selection")
    dataset, metadata = suite_and_metadata(cases, local_dispatch=local_dispatch, catalog_binding=catalog_binding)
    dataset_id = dataset["dataset_id"]
    dataset_path, expected_dataset_id = _execution_dataset(local_dispatch, catalog_binding)
    if dataset_id != expected_dataset_id:
        raise ValueError("Registered dataset identity differs from bound source")
    definitions = {c["id"]: c for c in dataset["cases"] if c["id"] in cases}
    batch = ROOT / "data/agent-benchmark-v2" / run_id
    if batch.exists():
        raise ValueError("Batch already exists; a new run ID is required")
    registry.register_round(round_data)
    registry.register_run({"id": run_id, "round_id": round_data["id"], "suite_id": dataset_id,
                           "suite_version": "v3.4" if dataset_id == REGRESSION_SUITE_ID else "v2" if dataset_id == NATURAL_SUITE_ID else "v1", "planned_cases": cases, "metadata": metadata})
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
            sync_summary(registry, run_id, batch, definitions, classify_connection_error=metadata.get("execution_mode") in {"local-dispatch-v2", "local-dispatch-v3", "local-dispatch-v4", "local-dispatch-v5"}, dataset_id=dataset_id)
            return None
        except Exception as exc:
            return type(exc).__name__
    try:
        freeze_args = prefix + ["freeze", "--batch", run_id, "--dataset", str(dataset_path), "--cases", ",".join(cases), "--nonvideo-guard"]
        if local_dispatch:
            freeze_args.append("--local-dispatch-guard")
            if isinstance(local_dispatch, str):
                freeze_args += ["--local-dispatch-version", local_dispatch]
        if catalog_binding is not None:
            with tempfile.TemporaryDirectory(prefix="creatoros-eval-catalog-") as scratch:
                snapshot_path = Path(scratch) / "catalog-binding.json"
                snapshot_path.write_text(json.dumps(catalog_binding, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
                code = invoke(freeze_args + ["--catalog-binding-file", str(snapshot_path)])
        else:
            code = invoke(freeze_args)
        if code:
            finish("failed", error=f"freeze exited {code}")
            return {"run_id": run_id, "status": "failed", "stage": "freeze", "exit_code": code}
        if suite_and_metadata(cases, local_dispatch=local_dispatch, catalog_binding=catalog_binding)[1] != metadata:
            raise RuntimeError("Evaluation source changed during freeze; no model call was started")
        frozen = _read_bounded(batch, "manifest.json", limit=16 * 1024 * 1024)
        if frozen.get("model_configuration", {}).get("catalog_binding") != catalog_binding:
            raise ValueError("Frozen catalog binding differs from registered run")
        manifest_hash = _sha(_read_bytes(batch, "manifest.json", limit=16 * 1024 * 1024))
        receipt = _read_bytes(batch, "manifest.json.sha256", limit=1024).decode("ascii").strip()
        if receipt != manifest_hash:
            raise ValueError("Frozen manifest receipt differs")
        registry.bind_manifest(run_id, manifest_hash)
        registry.transition(run_id, "running")
        started = True
        code = invoke(prefix + ["run", "--batch", run_id, "--workers", "1"])
        if suite_and_metadata(cases, local_dispatch=local_dispatch, catalog_binding=catalog_binding)[1] != metadata:
            raise RuntimeError("Evaluation source or regression catalog changed during execution")
        sync_summary(registry, run_id, batch, definitions, classify_connection_error=metadata.get("execution_mode") in {"local-dispatch-v2", "local-dispatch-v3", "local-dispatch-v4", "local-dispatch-v5"}, dataset_id=dataset_id)
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
