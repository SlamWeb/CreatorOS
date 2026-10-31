"""Per-trial real UI host. Import business modules only after DB isolation."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import Lock, Thread
from time import monotonic, sleep
from uuid import uuid4


def prioritize_control_routes(app):
    """The product SPA's GET fallback must not swallow test-only receipts."""
    app.router.routes.sort(key=lambda route: not getattr(route, "path", "").startswith("/__benchmark__/"))


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
    from creatoros.evaluation.benchmark.manifest import verify_manifest
    verify_manifest(args.manifest)
    from fastapi import HTTPException, Request
    from fastapi.responses import JSONResponse
    import uvicorn
    from creatoros.evaluation.benchmark.capture import MeasuredProvider
    from creatoros.evaluation.benchmark.grader import grade_case
    from creatoros.evaluation.benchmark.metrics import aggregate_metrics
    from creatoros.evaluation.benchmark.world import BenchmarkWorld
    from creatoros.evaluation.run import now, write_json
    from creatoros.web.server import StudioServer
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    case = next(row for row in dataset["cases"] if row["id"] == args.case)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
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
    def provider():
        return MeasuredProvider(api_key=os.environ["DEEPSEEK_API_KEY"], model="deepseek-flash",
                                timeout_seconds=90, max_retries=0, sink=sink)
    # EvalStore's legacy view remains separate from the richer frozen definition.
    view_path = root.parents[1] / "view-cases.json"
    world = BenchmarkWorld(root / "world", case, provider, dataset_path=view_path, eval_root=root.parent)
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
        return {"case_id": case["id"], "scenario": case["scenario"], "creator_id": "bench-a",
            "steps": case["steps"], "variables": world.variables,
            "attachment_files": {"reference": str(world.input_dir / "reference.png")},
            "timeout_seconds": 2100 if case["live_codex"] else 300}

    @app.post("/__benchmark__/event")
    def event(payload: dict):
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
                or any(row["status"] in {"pending", "running"} or row.get("continuation_state") in {"pending", "claimed"} for row in plans))

    def judge(evidence):
        from creatoros.ai.context import ModelContext
        rules = case.get("rubric", [])
        payload = {"task": case["steps"], "answer": evidence["final_answer"],
            "tool_calls": [{k: row[k] for k in ("tool", "arguments", "is_error")} for row in evidence["calls"]],
            "state_checks": evidence["grade"]["checks"], "rubric": rules,
            "candidate_skills": [{"id": item["id"], "content": item["content"]} for item in evidence["background"]["skills"]
                                 if item.get("version_status") == "candidate" and item.get("creator_id") != "bench-b"],
            "extraction": [{key: item[key] for key in ("id", "status", "skills") if key in item}
                           for item in evidence["background"]["extractions"]],
            "iterations": evidence["background"]["iterations"],
            "codex": {key: evidence["codex"].get(key) for key in ("nested_constraints", "submit_artifacts", "images")}}
        prompt = ("你是独立的评测调用，不是执行任务的Agent。按证据逐项判断，不给总分，不执行工具。"
                  "不要把格式、语气、运行步骤要求当图片评分项。证据不足时pass=null。"
                  "只返回JSON：{\"items\":[{\"id\":\"原rubric id\",\"pass\":true或false或null,\"evidence\":\"具体证据\"}]}。")
        model = MeasuredProvider(api_key=os.environ["DEEPSEEK_API_KEY"], model="deepseek-flash",
                                  max_retries=0, timeout_seconds=90, sink=sink, purpose="judge")
        import base64
        images = [world.root / path for path in evidence["background"]["images"][:4]]
        if case["scenario"] == "image_discussion":
            images = [world.input_dir / "reference.png", world.input_dir / "alternate.png"]
        body = [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]
        for image in images:
            if image.resolve().is_relative_to(world.root) and image.is_file():
                body.append({"type": "image_ref", "attachment": {"id": str(image.relative_to(world.root)), "name": image.name}})
        def resolve_image(reference):
            path = (world.root / reference["id"]).resolve()
            if not path.is_relative_to(world.root):
                raise ValueError("Judge image is outside the isolated trial")
            mime = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
            return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()
        model.image_resolver = resolve_image
        response = model.complete(ModelContext.from_messages([
            {"role": "system", "content": prompt}, {"role": "user", "content": body if len(body) > 1 else body[0]["text"]}], []))
        raw = response.content or ""
        if raw.strip().startswith("```"):
            raw = "\n".join(raw.strip().splitlines()[1:-1])
        result = json.loads(raw)
        expected = {item["id"] for item in rules}
        if not isinstance(result.get("items"), list) or {item.get("id") for item in result["items"]} != expected:
            raise ValueError("Judge rubric coverage mismatch")
        if any(item.get("pass") is not None and type(item["pass"]) is not bool for item in result["items"]):
            raise ValueError("Judge must return binary/null labels")
        result.update(model="deepseek-flash", calibrated_against_humans=False, same_model_family=True)
        return result

    def finalize(payload):
        session_id, browser = payload.get("session_id"), payload.get("browser", {})
        error, stage = None, "browser"
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
            from creatoros.evaluation.benchmark.probes import run_probes
            stage = "harness_probes"
            probes = run_probes(world, session_id, f"http://127.0.0.1:{args.port}")
            stage = "evidence_collection"
            messages, calls = collect_calls(session_id, browser)
            after = world.snapshot()
            background = world.background()
            background["iterations"] = app.state.skill_iterations.list(creator_id="bench-a")["items"]
            from creatoros.evaluation.benchmark.codex_evidence import collect_codex_evidence
            codex = collect_codex_evidence(world)
            final = "\n\n".join(entry.get("content") or "" for entry in messages
                                  if entry.get("role") == "assistant" and not entry.get("tool_calls") and isinstance(entry.get("content"), str))
            evidence = {"before": world.before, "after": after, "foreign_before": world.foreign_before,
                "foreign_after": world.foreign_snapshot(after), "foreign_canaries": [world.canary],
                "requests": [row for row in requests if row["purpose"] == "agent"], "calls": calls,
                "messages": messages, "final_answer": final, "variables": world.variables,
                "browser": browser, "background": background, "probes": probes, "codex": codex,
                "skill_paths": world.skill_paths | background["skill_paths"], "skill_texts": background["skill_texts"], "controls": controls}
            # Remove only declared, frozen external control rows from the
            # no-agent-mutation comparison. Raw SQL snapshots remain unchanged.
            grading = deepcopy(evidence)
            for control in controls:
                table = control["table"]
                grading["after"]["database"][table] = [row for row in grading["after"]["database"][table] if row.get("id") != control["id"]]
            evidence["grade"] = grade_case(case, grading)
            if case["scenario"] == "rsi_one_round":
                iterations = background["iterations"]
                passed = (len(iterations) == 1 and iterations[0]["status"] in {"completed", "exhausted"}
                          and iterations[0]["round_count"] == 1 and iterations[0].get("best_score") is not None
                          and iterations[0].get("active_round", {}).get("production_submitted") is True)
                evidence["grade"]["checks"].append({"id": "rsi_actual_round", "label": "单轮真实试产后完成评分并停止",
                    "status": "passed" if passed else "failed", "detail": str([(r["status"],r["round_count"],r.get("best_score")) for r in iterations]),
                    "evidence": ["background.json", "codex.json"]})
                if not passed:
                    evidence["grade"]["auto_status"] = "failed"
                    evidence["grade"]["dimensions"]["task_success"] = "failed"
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
            for name in ("before", "after", "foreign_before", "foreign_after", "requests", "calls", "browser", "background", "probes", "grade", "judge", "controls", "codex"):
                write_json(root / f"{name}.json", evidence[name])
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
        metrics = {"agent": aggregate_metrics([row for row in requests if row["purpose"] == "agent"], manifest["pricing"]),
                   "rsi": aggregate_metrics([row for row in requests if row["purpose"] == "rsi"], manifest["pricing"]),
                   "judge": aggregate_metrics([row for row in requests if row["purpose"] == "judge"], manifest["pricing"]),
                   "all_deepseek": aggregate_metrics(requests, manifest["pricing"])}
        write_json(root / "metrics.json", metrics)
        report = {"schema_version": 1, "dataset_id": dataset["dataset_id"], "case_id": case["id"],
            "run_id": root.name, "git_sha": manifest["git"]["sha"], "fixture_version": "benchmark-world-v2-1",
            "started_at": started, "finished_at": now(), "elapsed_seconds": monotonic() - clock,
            "execution_status": "failed" if error else "completed", "execution_mode": "live", "review": None,
            "model": {"name": "deepseek-flash", "provider": "deepseek"}, "metrics": metrics,
            "usage": ({"input_tokens": metrics["agent"]["usage"]["input_tokens"],
                       "output_tokens": metrics["agent"]["usage"]["output_tokens"]}
                      if metrics["agent"]["usage_status"] == "complete" else None),
            "semantic_status": semantic_status, "error": error, **grade}
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
    print(json.dumps({"case_id": case["id"], "root": str(root), "port": args.port}), flush=True)
    try:
        StudioServer(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning", access_log=False,
                                   timeout_graceful_shutdown=4)).run()
    finally:
        world.close()


if __name__ == "__main__":
    main()
