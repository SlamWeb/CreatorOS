"""Per-trial real UI host. Import business modules only after DB isolation."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from threading import Lock, Thread
from time import monotonic, sleep
from uuid import uuid4


def research_inventory_for_judge(evidence):
    """Bound untrusted snippets; keep source identity and confidence explicit."""
    inventory = evidence.get("source_inventory") if isinstance(evidence, dict) else None
    if not isinstance(inventory, list):
        return None
    rows = []
    for item in inventory[:12]:
        if not isinstance(item, dict):
            continue
        snippets = item.get("search_snippets")
        snippets = snippets if isinstance(snippets, list) else []
        rows.append({"id": item.get("id"), "url": item.get("url"),
                     "title": str(item.get("title") or "")[:500],
                     "agent_open": item.get("agent_open"), "host_fetch": item.get("host_fetch"),
                     "search_snippets": [{"text": str(s.get("text") or "")[:1000],
                                          "event_line": s.get("event_line"),
                                          "excerpt_may_be_truncated": len(str(s.get("text") or "")) > 1000}
                                         for s in snippets[:2] if isinstance(s, dict)]})
    return rows


def rsi_round_status(iterations):
    """Submission/score flags do not prove image delivery or score identity."""
    if not isinstance(iterations, list) or any(not isinstance(row, dict) for row in iterations):
        return "needs_review"
    if len(iterations) != 1:
        return "failed"
    row = iterations[0]
    if not all(key in row for key in ("status", "round_count", "best_score", "active_round")) or not isinstance(row["active_round"], dict) or "production_submitted" not in row["active_round"]:
        return "needs_review"
    if (row["status"] not in {"completed", "exhausted"} or row["round_count"] != 1
            or row["best_score"] is None or row["active_round"]["production_submitted"] is not True):
        return "failed"
    return "needs_review"


def prioritize_control_routes(app):
    """The product SPA's GET fallback must not swallow test-only receipts."""
    app.router.routes.sort(key=lambda route: not getattr(route, "path", "").startswith("/__benchmark__/"))


def open_trial_listener(app, root: Path, case_id: str, port: int = 0):
    """Bind this trial's loopback socket before publishing its chosen address."""
    import uvicorn
    from creatoros.evaluation.benchmark.io import write_json

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False,
                            timeout_graceful_shutdown=4)
    sock = config.bind_socket()
    try:
        # The caller may request port 0. Every in-process tool probe must use
        # the actual bound port rather than the requested placeholder.
        app.state.benchmark_base_url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        write_json(root / "server-address.json", {"case_id": case_id, "run_id": root.name,
                                                  "pid": os.getpid(), "port": sock.getsockname()[1]})
    except BaseException:
        sock.close()
        raise
    return config, sock


def run_bound_probes(world, session_id):
    """Exercise the same isolated listener used by this trial's browser."""
    from creatoros.evaluation.benchmark.probes import run_probes

    return run_probes(world, session_id, world.app.state.benchmark_base_url)


def capture_run_snapshot(world):
    """Record real SQL statuses and the observation interval, not reply-time truth."""
    from sqlalchemy import text
    from creatoros.evaluation.benchmark.io import now

    started_at = now()
    with world.database.engine.connect() as connection:
        runs = [dict(row) for row in connection.execute(text("SELECT id,status FROM content_runs")).mappings()
                if row["id"] not in {world.variables["run_a"], world.variables["run_b"]}]
    return {"runs": runs, "runs_observation_started_at": started_at, "runs_observation_finished_at": now()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    repo = Path(__file__).resolve().parents[3]
    if not root.is_relative_to(repo / "data/agent-benchmark-v2"):
        parser.error("Trial must live in the dedicated benchmark namespace")
    root.mkdir(parents=True, exist_ok=True)
    os.environ["DATABASE_URL"] = f"sqlite:///{(root / 'world/world.db').as_posix()}"
    from creatoros.evaluation.benchmark.manifest import frozen_dataset_path, verify_manifest
    manifest = verify_manifest(args.manifest)["manifest"]
    batch_root = root.parent.parent
    if (root.parent.name != "records" or args.manifest.resolve() != (batch_root / "manifest.json").resolve()
            or manifest.get("model_configuration", {}).get("benchmark_run_id", batch_root.name) != batch_root.name):
        parser.error("Trial and manifest must belong to the same batch")
    trial_binding = {"benchmark_batch_id": batch_root.name,
                     "benchmark_manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest()}
    from creatoros.evaluation.benchmark.judge_timing import verify_judge_evidence_version
    verify_judge_evidence_version(manifest)
    from creatoros.evaluation.benchmark.program_evidence_guard import verify_guard_version
    verify_guard_version(manifest)
    from creatoros.evaluation.benchmark.nonvideo_guard import validate, install_video_guard, DenyPublication
    guarded = validate(manifest, args.case)
    from creatoros.evaluation.benchmark.local_dispatch_guard import VERSION as LOCAL_VERSION
    local_guard = (manifest["model_configuration"].get("benchmark_guard") or {}).get("local_dispatch") == LOCAL_VERSION
    if args.case not in manifest["selected_case_ids"]:
        parser.error("Case is outside the frozen selection")
    if guarded:
        install_video_guard()
    dispatch_guard = None
    if local_guard:
        from creatoros.evaluation.benchmark.local_dispatch_guard import install
        dispatch_guard = install()
    dataset_path = frozen_dataset_path(args.manifest, manifest).resolve()
    if args.dataset.resolve() != dataset_path:
        parser.error("Trial must use the verified frozen dataset")
    from fastapi import HTTPException, Request
    from fastapi.responses import JSONResponse
    import uvicorn
    from creatoros.evaluation.benchmark.capture import MeasuredProvider
    from creatoros.evaluation.benchmark.grader import grade_case
    from creatoros.evaluation.benchmark.metrics import aggregate_metrics
    from creatoros.evaluation.benchmark.scoring import score_case
    from creatoros.evaluation.benchmark.judge_timing import build_temporal_evidence
    from creatoros.evaluation.benchmark.world import BenchmarkWorld
    from creatoros.evaluation.benchmark.io import now, write_json
    from creatoros.web.server import StudioServer
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    case = next(row for row in dataset["cases"] if row["id"] == args.case)
    requests, lock, finished = [], Lock(), {}
    started, clock = now(), monotonic()
    def sink(record):
        with lock:
            record = {**record, "case_id": case["id"]}
            index = next((n for n, previous in enumerate(requests) if previous["request_id"] == record["request_id"]), None)
            if index is None:
                requests.append(record)
            else:
                requests[index] = record
            write_json(root / "requests.json", requests)
    write_json(root / "requests.json", requests)
    def provider():
        return MeasuredProvider(api_key=os.environ["DEEPSEEK_API_KEY"], model="deepseek-flash",
                                timeout_seconds=90, max_retries=0, sink=sink)
    world = BenchmarkWorld(root / "world", case, provider, dataset_path=dataset_path, eval_root=root.parents[2],
                           publication_client=DenyPublication() if guarded else None,
                           defer_background_start=local_guard)
    if guarded:
        from creatoros.integrations import video_production
        from creatoros.evaluation.benchmark.nonvideo_guard import deny_video
        if video_production.produce_video is not deny_video or not isinstance(world.app.state.publishing.client, DenyPublication):
            raise RuntimeError("Benchmark guards not installed")
        receipt = {"policy": manifest["model_configuration"]["benchmark_guard"],
            "case_id": case["id"], "video_dispatch_installed": True, "publication_client_installed": True,
            "network_sandbox": False}
        if local_guard:
            from creatoros.evaluation.benchmark.local_dispatch_guard import verify_installed
            verify_installed(dispatch_guard, world.app)
            receipt.update(local_dispatch_installed=True, background_services_deferred=True)
        write_json(root / "guard.json", receipt)
    world.app.state.skill_iterations.provider_factory = lambda: MeasuredProvider(
        api_key=os.environ["DEEPSEEK_API_KEY"], model="deepseek-v4-flash", timeout_seconds=180,
        reasoning_effort="high", max_retries=0, sink=sink, purpose="rsi")
    write_json(root / "before.json", world.before)
    write_json(root / "foreign_before.json", world.foreign_before)
    write_json(root / "case.json", case)
    write_json(root / "variables.json", world.variables)
    controls = []
    state = {"finish_claimed": False}
    app = world.app

    @app.get("/__benchmark__/scenario")
    def scenario():
        return {"case_id": case["id"], "run_id": root.name, "scenario": case["scenario"], "creator_id": "bench-a",
            "live_codex": case["live_codex"],
            "steps": case["steps"], "variables": world.variables,
            "attachment_files": {"reference": str(world.input_dir / "reference.png")},
            "timeout_seconds": 2100 if case["live_codex"] else 300}

    @app.post("/__benchmark__/event")
    def event(payload: dict):
        from creatoros.evaluation.benchmark.expansion_recovery import EVENT, SCENARIOS, host_event
        if payload.get("name") == EVENT and case["scenario"] in SCENARIOS:
            try:
                result = host_event(world, payload["name"], payload.get("session_id"))
            except (KeyError, TypeError, ValueError) as error:
                raise HTTPException(422, "Invalid recovery control event") from error
            controls.extend(result["controls"])
            world.variables.update(result["variables"])
            write_json(root / "controls.json", controls)
            write_json(root / "variables.json", world.variables)
            return {"variables": result["variables"], "declared_control": True}
        if payload.get("name") != "add_external_topic" or case["scenario"] != "external_change":
            raise HTTPException(422, "Unfrozen control event")
        from creatoros.storage import TopicSource
        world.content.add_topic(topic_id="bench-external", series_id="bench-english",
            title="外部新增：日常借书", brief="此项由预定页面外部事件加入。", source=TopicSource.MANUAL)
        controls.append({"name": payload["name"], "table": "topics", "id": "bench-external"})
        write_json(root / "controls.json", controls)
        return {"variables": {"external_topic": "bench-external"}, "declared_control": True}

    def collect_calls(session_id, browser):
        messages_path = world.root / "sessions" / session_id / "messages.json"
        messages = json.loads(messages_path.read_text(encoding="utf-8"))
        turns = {row.get("turn_id"): index for index, row in enumerate(browser.get("turns", []), 1)}
        trace_dir = messages_path.with_suffix(".request-trace")
        calls, seen = [], set()
        for path in sorted(trace_dir.glob("*.json"), key=lambda path: path.stat().st_mtime_ns):
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            for call in (snapshot.get("response") or {}).get("tool_calls", []):
                identity = (snapshot["request_id"], call["id"])
                if identity in seen:
                    continue
                seen.add(identity)
                result = next((row for row in messages if row.get("role") == "tool" and row.get("tool_call_id") == call["id"]), {})
                raw = result.get("content", "")
                error_type = raw.split("]", 1)[0].removeprefix("[tool_error type=") if raw.startswith("[tool_error type=") else None
                try:
                    arguments = json.loads(call["arguments"]) if isinstance(call["arguments"], str) else call["arguments"]
                except (ValueError, TypeError):
                    arguments = call["arguments"]
                calls.append({"tool": call["name"], "arguments": arguments, "tool_call_id": call["id"],
                    "request_id": snapshot["request_id"], "turn_id": snapshot.get("turn_id"),
                    "turn_index": turns.get(snapshot.get("turn_id")), "raw_result": raw,
                    "is_error": error_type is not None, "error_type": error_type})
        return messages, calls

    def waiting(session_id):
        from sqlalchemy import text
        doc = app.state.chat.get(session_id)
        with world.database.engine.connect() as connection:
            runs = [dict(row) for row in connection.execute(text("SELECT id,status FROM content_runs")).mappings()
                    if row["id"] not in {world.variables["run_a"], world.variables["run_b"]}]
            plans = [dict(row) for row in connection.execute(text("SELECT status,continuation_state FROM agent_plans")).mappings()]
        jobs = world.extractions.list()
        if isinstance(jobs, dict):
            jobs = jobs.get("items", [])
        research = world.research.list("bench-english")
        iterations = app.state.skill_iterations.list(creator_id="bench-a")["items"]
        active = {"queued", "researching", "running", "producing", "validating", "extracting"}
        return (doc["status"] == "running" or any(row["status"] in active for row in runs + jobs + research)
                or any(row["status"] in {"queued", "running", "waiting_production"} for row in iterations)
                or any(row["status"] in {"pending", "running"} or row.get("continuation_state") in {"pending", "claimed", "executing"} for row in plans))

    @app.get("/__benchmark__/task-state")
    def task_state(session_id: str):
        snapshot = capture_run_snapshot(world)
        return {"busy": waiting(session_id), **snapshot}

    def judge(evidence):
        from creatoros.evaluation.benchmark.grader import _selected_candidate, MissingEvidence
        rules = case.get("rubric", [])
        candidate_skills, candidate_error = [], None
        if any(rule["kind"].startswith("candidate_") for rule in case["expectations"]):
            try:
                selected, _ = _selected_candidate(evidence, world.variables["skill_a"])
                candidate_skills = [{"id": selected["id"], "content": selected["content"]}]
            except MissingEvidence as error:
                candidate_error = str(error)
        temporal = build_temporal_evidence(evidence["browser"], evidence["calls"], evidence["background"],
                                           background_observed_at=evidence.get("background_observed_at"))
        payload = {"task": case["steps"], "intent": case.get("intent"),
            "expected_outcome": case.get("expected_outcome"), "answer": evidence["final_answer"],
            "actual_queries": [turn["query"] for turn in evidence["browser"].get("turns", [])],
            "conversation": [{key: entry[key] for key in ("role", "content", "tool_calls", "tool_call_id") if key in entry}
                             for entry in evidence["messages"] if entry.get("role") in {"user", "assistant"}],
            "variables": world.variables,
            "tool_calls": [{k: row[k] for k in ("tool", "arguments", "is_error", "raw_result", "turn_index", "tool_call_id") if k in row} for row in evidence["calls"]],
            "state_checks": evidence["grade"]["checks"], "rubric": rules, "temporal_evidence": temporal,
            "candidate_skills": candidate_skills, "candidate_evidence_error": candidate_error,
            "original_skill": evidence["initial_skills"]["skill_texts"].get(world.variables["skill_a"]),
            "extraction": [{key: item[key] for key in ("id", "status", "skills") if key in item}
                           for item in evidence["background"]["extractions"]],
            "iterations": evidence["background"]["iterations"],
            "codex": {key: evidence["codex"].get(key) for key in ("nested_constraints", "submit_artifacts", "images")}}
        from creatoros.evaluation.benchmark.semantic_judge import scoped_business_oracle
        payload["business_oracle"] = {name: scoped_business_oracle(evidence[name], world.variables["creator_a"], evidence["before"] if name == "after" else None)
                                      for name in ("before", "after")}
        new_run_ids = {row["id"] for row in evidence["background"]["runs"]}
        payload["accepted_revisions"] = [
            {"run_id": row["content_run_id"], "revision": row["revision_number"],
             "validation": json.loads(row["validation_json"]) if row.get("validation_json") else None,
             "frozen_input": json.loads(row["production_input_json"]) if row.get("production_input_json") else None}
            for row in evidence["after"]["database"]["content_revisions"] if row["content_run_id"] in new_run_ids]
        payload["plans"] = evidence["background"]["plans"]
        payload["export_contents"] = evidence["background"].get("export_contents", [])
        payload["initial_collections"] = evidence["background"].get("initial_collections", {})
        payload["collections"] = evidence["background"].get("collections", {})
        payload["production_pages"] = evidence["browser"].get("production_pages", [])
        if "video_evidence" in evidence:
            payload["video_evidence"] = evidence["video_evidence"]
        research_evidence = evidence.get("research_evidence")
        inventory = research_inventory_for_judge(research_evidence)
        if inventory is not None:
            # Diagnostic excerpts are untrusted search-result snippets, not
            # proof that the Agent successfully opened a complete page.
            payload["research_source_inventory"] = inventory
        if isinstance(research_evidence, dict) and isinstance(research_evidence.get("host_sources"), list):
            # Post-task host fetches are independent observations, not the
            # researcher's page contents. Attach the exact bounded records.
            payload["research_host_source_data"] = deepcopy(research_evidence["host_sources"])
        if isinstance(research_evidence, dict):
            payload["research_event_summary"] = {
                key: research_evidence.get(key) for key in
                ("status", "candidate_id", "candidate_title", "candidate_angle", "thread_id", "turn_id",
                 "search_event_lines", "provenance", "limitations")}
        if isinstance(research_evidence, dict) and research_evidence.get("status") == "complete":
            payload["research_source_data"] = research_evidence.get("sources", [])
        from creatoros.evaluation.benchmark.semantic_judge import evaluate_judge
        from creatoros.evaluation.benchmark.visual_evidence import collect_visual_evidence, load_visual_attachments, UnavailableVisualEvidence
        supported = (case.get("id") in {"B04", "B29", "B41", "B42", "B44", "B45", "B49"}
                     or (case.get("id") == "I01" and case.get("scenario") == "visual_reference_expansion"))
        visual_manifest, visual_attachments = None, None
        if supported:
            if case.get("id") == "B04":
                visual_manifest = (evidence.get("rsi_evidence") or {}).get("visual_manifest")
            elif case.get("id") == "I01" and case.get("scenario") == "visual_reference_expansion":
                from creatoros.evaluation.benchmark.expansion_visual_tasks import collect_visual
                visual_manifest = collect_visual(world.root, evidence)
            else:
                visual_manifest = collect_visual_evidence(world.root, case, evidence)
            visual_manifest = visual_manifest or {"status":"needs_review", "bindings":[], "errors":["RSI evidence incomplete"]}
            payload["visual_evidence"] = visual_manifest
            try:
                visual_attachments = load_visual_attachments(world.root, visual_manifest)
            except (UnavailableVisualEvidence, OSError, KeyError, TypeError) as error:
                visual_manifest = {**visual_manifest, "status": "needs_review", "errors": [*visual_manifest.get("errors", []), str(error)]}
                payload["visual_evidence"] = visual_manifest
                visual_attachments = []
        images = [world.root / path for path in evidence["background"]["images"][:4]]
        if case["scenario"] == "image_discussion":
            images = [world.input_dir / "reference.png", world.input_dir / "alternate.png"]
        result = evaluate_judge(case, payload, images, world.root, sink,
                                visual_manifest=visual_manifest, visual_attachments=visual_attachments,
                                research_evidence=research_evidence)
        if case.get("id") == "B48" and isinstance(research_evidence, dict) and research_evidence.get("host_sources"):
            from creatoros.evaluation.benchmark.semantic_judge import evaluate_host_research_sources
            try:
                result["host_source_assessments"] = evaluate_host_research_sources(payload, world.root, sink, research_evidence)
            except Exception as error:
                result["host_source_assessments"] = {"error": {"kind": type(error).__name__, "message": str(error)}}
        return result

    def finalize(payload):
        session_id, browser = payload.get("session_id"), payload.get("browser", {})
        error, stage = None, "browser"
        semantic_judge = None
        try:
            if not session_id:
                raise ValueError("Browser never created an account session")
            if case["live_codex"]:
                stage = "await_background"
                deadline, quiet = monotonic() + 2100, 0
                while monotonic() < deadline:
                    quiet = 0 if waiting(session_id) else quiet + 1
                    if quiet >= 2:
                        break
                    sleep(1)
                else:
                    raise TimeoutError("Background task did not reach a terminal state")
            stage = "harness_probes"
            probes = run_bound_probes(world, session_id)
            stage = "evidence_collection"
            messages, calls = collect_calls(session_id, browser)
            after = world.snapshot()
            background = world.background()
            background_observed_at = now()
            background["iterations"] = app.state.skill_iterations.list(creator_id="bench-a")["items"]
            from creatoros.evaluation.benchmark.codex_evidence import collect_codex_evidence
            codex = collect_codex_evidence(world)
            final = "\n\n".join(entry.get("content") or "" for entry in messages
                                  if entry.get("role") == "assistant" and not entry.get("tool_calls") and isinstance(entry.get("content"), str))
            evidence = {"before": world.before, "after": after, "foreign_before": world.foreign_before,
                "foreign_after": world.foreign_snapshot(after), "foreign_canaries": [world.canary],
                "requests": [row for row in requests if row["purpose"] == "agent"], "calls": calls,
                "rsi_requests": [row for row in requests if row["purpose"] == "rsi"],
                "messages": messages, "final_answer": final, "variables": world.variables,
                "browser": browser, "background": background, "background_observed_at": background_observed_at,
                "probes": probes, "codex": codex,
                "initial_skills": world.initial_skill_evidence,
                "skill_paths": world.skill_paths | background["skill_paths"], "skill_texts": background["skill_texts"], "controls": controls}
            # Remove only declared, frozen external control rows from the
            # no-agent-mutation comparison. Raw SQL snapshots remain unchanged.
            grading = deepcopy(evidence)
            for control in controls:
                if control.get("preserve_in_grading") is True:
                    continue
                table = control["table"]
                grading["after"]["database"][table] = [row for row in grading["after"]["database"][table] if row.get("id") != control["id"]]
            evidence["grade"] = grade_case(case, grading)
            if case.get("scenario") == "visual_reference_expansion" and case.get("id") in {"I02", "I03", "I04", "I05"}:
                from creatoros.evaluation.benchmark.expansion_visual_tasks import preconditions
                visual_check = preconditions(world.root, case, evidence)
                evidence["grade"]["checks"].append({"id":"visual_preconditions", "label":"任务媒体身份及缺失条件",
                    "status":visual_check["status"], "detail":visual_check.get("detail", ""),
                    "evidence":["before.json", "browser.json"]})
            if case["scenario"] == "video_delivery":
                from creatoros.evaluation.benchmark.video_evidence import collect_video_evidence
                evidence["video_evidence"] = collect_video_evidence(world.root, evidence)
            if case["scenario"] == "live_research":
                from creatoros.evaluation.benchmark.research_evidence import collect_research_evidence
                evidence["research_evidence"] = collect_research_evidence(world.root, evidence, independent_fetch=True)
            if case["scenario"] == "rsi_one_round":
                from creatoros.evaluation.benchmark.rsi_evidence import collect_rsi_evidence
                evidence["rsi_evidence"] = collect_rsi_evidence(world, evidence)
                round_status = evidence["rsi_evidence"]["status"]
                evidence["grade"]["checks"].append({"id": "rsi_actual_round", "label": "单轮真实试产后完成评分并停止",
                    "status": round_status, "detail": evidence["rsi_evidence"]["detail"],
                    "evidence": ["background.json", "codex.json", "rsi_evidence.json"]})
                if round_status == "failed":
                    evidence["grade"]["auto_status"] = "failed"
                    evidence["grade"]["dimensions"]["task_success"] = "failed"
                elif evidence["grade"]["auto_status"] != "failed":
                    evidence["grade"]["auto_status"] = "needs_review"
                    evidence["grade"]["dimensions"]["task_success"] = "needs_review"
            if case["scenario"] == "delivery_repair":
                nested = codex["nested_constraints"]
                evidence["grade"]["checks"].append({"id": "nested_delivery_repair", "label": "同次Codex执行中拒绝后修复交付",
                    "status": nested["status"], "detail": str(nested), "evidence": ["codex.json"]})
                if nested["status"] != "passed":
                    if evidence["grade"]["auto_status"] != "failed":
                        evidence["grade"]["auto_status"] = nested["status"]
                    evidence["grade"]["dimensions"]["protocol_valid"] = nested["status"]
            if any(exp["kind"] in {"image_min", "video_min"} for exp in case["expectations"]):
                delivered = bool(background["runs"]) and all(row["status"] == "awaiting_approval" for row in background["runs"])
                evidence["grade"]["checks"].append({"id": "production_delivered", "label": "生产交付终态与文件一致",
                    "status": "passed" if delivered else "failed", "detail": str([(row["id"], row["status"]) for row in background["runs"]]),
                    "evidence": ["background.json", "after.json"]})
                if not delivered:
                    evidence["grade"]["auto_status"] = "failed"
                    evidence["grade"]["dimensions"]["state_consistent"] = "failed"
            try:
                evidence["judge"] = judge(evidence)
            except Exception as failure:
                evidence["judge"] = {"error": {"kind": type(failure).__name__, "message": str(failure)}, "items": []}
            semantic_judge = evidence["judge"]
            if "rsi_evidence" in evidence:
                write_json(root / "rsi_evidence.json", evidence["rsi_evidence"])
            for name in ("video_evidence", "research_evidence"):
                if name in evidence:
                    write_json(root / (name + ".json"), evidence[name])
            for name in ("before", "after", "foreign_before", "foreign_after", "calls", "browser", "background", "initial_skills", "probes", "grade", "judge", "controls", "codex"):
                write_json(root / f"{name}.json", evidence[name])
            # Keep all paid calls, including Judge/RSI. Security grading above
            # intentionally inspects only the executing account Agent's context.
            write_json(root / "requests.json", requests)
            write_json(root / "messages.json", messages)
            (root / "answer.txt").write_text(final, encoding="utf-8")
            grade = evidence["grade"]
            items = evidence["judge"].get("items", [])
            semantic_status = ("needs_review" if not items else "failed" if any(item["pass"] is False for item in items)
                               else "needs_review" if any(item["pass"] is None for item in items) else "passed")
        except Exception as failure:
            error = {"kind": type(failure).__name__, "message": str(failure), "stage": stage}
            grade = {"auto_status": "failed", "checks": [{"id": "execution", "label": "Execution evidence complete",
                     "status": "failed", "detail": str(failure), "evidence": ["browser.json"]}],
                     "dimensions": {"task_success": "failed", "boundary_enforced": "needs_review",
                                    "state_consistent": "needs_review", "protocol_valid": "failed"}}
            write_json(root / "browser.json", browser)
            semantic_status = "needs_review"
        from creatoros.evaluation.benchmark.nonvideo_guard import evidence as guard_evidence
        guard_check = guard_evidence(manifest, case["id"], root)
        if guard_check is not None and guard_check["status"] != "passed" and error is None:
            error = {"kind": "GuardEvidenceError", "message": "Frozen guard evidence missing or mismatched", "stage": "guard_evidence"}
        scores = score_case(case, grade, semantic_judge)
        if local_guard:
            from creatoros.evaluation.benchmark.local_dispatch_guard import apply_gate, verify_installed
            calls = evidence.get("calls") if error is None else None
            dispatch_gate = dispatch_guard.score(calls, background_started=world.app.state.background_services_started)
            try:
                verify_installed(dispatch_guard, world.app)
            except RuntimeError:
                if dispatch_gate["status"] != "failed":
                    dispatch_gate = {"id": "local_dispatch_guard", "status": "needs_review",
                                     "detail": "Guard wiring or deferred background state no longer verifiable"}
            apply_gate(scores, dispatch_gate)
        grade["diagnostic_status"] = grade["auto_status"]
        grade["auto_status"] = scores["program"]["status"]
        semantic_status = scores["semantic"]["status"]
        metrics = {"agent": aggregate_metrics([row for row in requests if row["purpose"] == "agent"], manifest["pricing"]),
                   "rsi": aggregate_metrics([row for row in requests if row["purpose"] == "rsi"], manifest["pricing"]),
                   "judge": aggregate_metrics([row for row in requests if row["purpose"] == "judge"], manifest["pricing"]),
                   "all_deepseek": aggregate_metrics(requests, manifest["pricing"])}
        write_json(root / "metrics.json", metrics)
        report = {"schema_version": 1, "dataset_id": dataset["dataset_id"], "case_id": case["id"],
            **trial_binding,
            "run_id": root.name, "git_sha": manifest["git"]["sha"], "fixture_version": "benchmark-world-v2-2",
            "started_at": started, "finished_at": now(), "elapsed_seconds": monotonic() - clock,
            "execution_status": "failed" if error else "completed", "execution_mode": "live", "review": None,
            "model": {"name": "deepseek-flash", "provider": "deepseek"}, "metrics": metrics,
            "usage": ({"input_tokens": metrics["agent"]["usage"]["input_tokens"],
                       "output_tokens": metrics["agent"]["usage"]["output_tokens"]}
                      if metrics["agent"]["usage_status"] == "complete" else None),
            "semantic_status": semantic_status, "scores": scores, "error": error, **grade}
        if guard_check is not None:
            report["benchmark_guard"] = guard_check
        report["evidence_files"] = [{"name": path.name, "label": path.stem} for path in root.glob("*.json") if path.name != "report.json"]
        report["evidence_files"].append({"name": "answer.txt", "label": "最终回复"}) if (root / "answer.txt").exists() else None
        write_json(root / "report.json", report)
        finished.update(report)

    @app.post("/__benchmark__/finish")
    def finish(payload: dict):
        with lock:
            if not state["finish_claimed"]:
                state["finish_claimed"] = True
                Thread(target=finalize, args=(payload,), daemon=True).start()
        return JSONResponse({"run_id": root.name, "status": "observing_real_results"}, status_code=202)

    @app.get("/__benchmark__/result")
    def result():
        return finished if finished else JSONResponse({"status": "running"}, status_code=202)

    prioritize_control_routes(app)
    config, sock = open_trial_listener(app, root, case["id"], args.port)
    print(json.dumps({"case_id": case["id"], "root": str(root), "port": sock.getsockname()[1]}), flush=True)
    try:
        StudioServer(config).run(sockets=[sock])
    finally:
        sock.close()
        world.close()


if __name__ == "__main__":
    main()
