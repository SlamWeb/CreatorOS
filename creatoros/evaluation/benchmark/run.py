"""Explicit paid freeze/run CLI. Every case gets its own process and SQLite."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from time import monotonic, sleep
from urllib.request import urlopen
from uuid import uuid4


REPO = Path(__file__).resolve().parents[3]
DATASET = REPO / "docs/agent-eval/benchmark-v2/cases.json"


def freeze(root, dataset_path, case_ids=None, *, nonvideo_guard=False, local_dispatch=False, catalog_binding=None):
    from creatoros.evaluation.benchmark.manifest import freeze_manifest
    from creatoros.integrations.codex import CODEX_MODEL, CODEX_EFFORT
    from creatoros.evaluation.benchmark.judge_timing import VERSION as JUDGE_EVIDENCE_VERSION
    from creatoros.evaluation.benchmark.judge_contradiction_guard import VERSION as JUDGE_CONTRADICTION_GUARD_VERSION
    from creatoros.evaluation.benchmark.program_evidence_guard import VERSION as PROGRAM_EVIDENCE_GUARD_VERSION
    from creatoros.evaluation.benchmark.rsi_evidence import VERSION as RSI_EVIDENCE_VERSION
    from creatoros.evaluation.benchmark.video_evidence import VERSION as VIDEO_EVIDENCE_VERSION
    from creatoros.evaluation.benchmark.research_evidence import VERSION as RESEARCH_EVIDENCE_VERSION
    from creatoros.evaluation.benchmark.grounding_guard import VERSION as RESEARCH_GUARD_VERSION
    from creatoros.evaluation.benchmark.expansion_visual_tasks import VERSION as VISUAL_EXPANSION_VERSION
    from importlib.metadata import version
    pricing = {"currency": "USD", "source": "https://api-docs.deepseek.com/quick_start/pricing/",
        "frozen_at": datetime.now(timezone.utc).isoformat(), "billing_note": "bounds span off-peak and peak; estimate, not invoice",
        "aliases": {"deepseek-v4-flash": "deepseek-flash"}, "models": {"deepseek-flash": {
            "cache_hit_per_million": [0.003, 0.006], "cache_miss_per_million": [0.15, 0.30],
            "output_per_million": [0.6, 1.2]}}}
    configuration = {"benchmark_run_id": root.name, "provider_request_model": "deepseek-flash", "provider": "deepseek", "retries": 0,
        "agent_reasoning": "product default high", "judge_model": "deepseek-flash", "judge_reasoning": "none",
        "judge_evidence_version": JUDGE_EVIDENCE_VERSION,
        "judge_contradiction_guard_version": JUDGE_CONTRADICTION_GUARD_VERSION,
        "program_evidence_guard_version": PROGRAM_EVIDENCE_GUARD_VERSION,
        "benchmark_evidence_versions": {"rsi": RSI_EVIDENCE_VERSION, "video": VIDEO_EVIDENCE_VERSION,
                                        "research": RESEARCH_EVIDENCE_VERSION, "research_qualification": RESEARCH_GUARD_VERSION,
                                        "visual_expansion": VISUAL_EXPANSION_VERSION},
        "codex_model": CODEX_MODEL, "codex_effort": CODEX_EFFORT, "codex_sdk_version": version("openai-codex"),
        "codex_billing": "subscription; monetary cost unknown", "browser": "Chrome real SPA",
        "max_concurrent_cases": 3, "live_codex_timeout_seconds": 2100}
    source_namespace = None
    if local_dispatch in {"local-dispatch-v4", "local-dispatch-v5"}:
        if catalog_binding is None:
            raise ValueError("v4 requires a catalog binding")
        from creatoros.evaluation.regression_runner import selection_namespace, validate_binding
        from creatoros.evaluation.tool_calling import _execution_dataset
        validate_binding(case_ids, catalog_binding)
        source_namespace = selection_namespace(catalog_binding["selected_instance_ids"])
        allowed_path, _ = _execution_dataset(local_dispatch, catalog_binding)
        if Path(dataset_path).resolve() != allowed_path.resolve():
            raise ValueError("v4 dataset path must match the authorized frozen source")
    if nonvideo_guard:
        from creatoros.evaluation.benchmark.nonvideo_guard import policy
        configuration["benchmark_guard"] = policy(case_ids, local_dispatch=local_dispatch, source_namespace=source_namespace)
    if catalog_binding is not None:
        from creatoros.evaluation.regression_runner import validate_binding
        configuration.update(validate_binding(case_ids, catalog_binding))
        selected = set(catalog_binding["selected_instance_ids"])
        adapters = {row["adapter"] for row in catalog_binding["frozen_catalog"]["membership"]["cases"]
                    if row["instance_id"] in selected}
        if adapters != {configuration.get("benchmark_guard", {}).get("local_dispatch")}:
            raise ValueError("Frozen catalog adapter and guard version differ")
    manifest = freeze_manifest(root / "manifest.json", dataset_path, pricing=pricing,
                               model_configuration=configuration, case_ids=case_ids)
    return manifest


def fallback_report(root, case, manifest, error, stage, *, trial_binding=None):
    from creatoros.evaluation.benchmark.io import now, write_json
    from creatoros.evaluation.benchmark.metrics import aggregate_metrics
    from creatoros.evaluation.benchmark.scoring import score_case
    if (root / "report.json").exists():
        return
    requests = json.loads((root / "requests.json").read_text(encoding="utf-8")) if (root / "requests.json").exists() else []
    metrics = {key: aggregate_metrics([r for r in requests if key == "all_deepseek" or r.get("purpose") == key], manifest["pricing"])
               for key in ("agent", "rsi", "judge", "all_deepseek")}
    write_json(root / "metrics.json", metrics)
    stamp = now()
    report = {"schema_version": 1, "run_id": root.name, "dataset_id": manifest["dataset_id"],
        "case_id": case["id"], "started_at": stamp, "finished_at": stamp, "elapsed_seconds": 0,
        "git_sha": manifest["git"]["sha"], "fixture_version": "benchmark-world-v2-2", "execution_mode": "live",
        "execution_status": "failed", "auto_status": "failed", "semantic_status": "needs_review", "review": None,
        "model": {"name": "deepseek-flash", "provider": "deepseek"}, "usage": None, "metrics": metrics,
        "error": {"kind": type(error).__name__, "message": str(error), "stage": stage},
        "scores": score_case(case, {"checks": []}, None),
        "dimensions": {"task_success": "failed", "boundary_enforced": "needs_review", "state_consistent": "needs_review", "protocol_valid": "failed"},
        "checks": [{"id": "launcher", "label": "真实前端执行完成", "status": "failed", "detail": str(error), "evidence": ["metrics.json"]}],
        "evidence_files": [{"name": "metrics.json", "label": "已观测用量；缺失不记0"}]}
    if trial_binding is not None:
        report.update(trial_binding)
    from creatoros.evaluation.benchmark.nonvideo_guard import evidence as guard_evidence
    guard_check = guard_evidence(manifest, case["id"], root)
    if guard_check is not None:
        report["benchmark_guard"] = guard_check
    write_json(root / "report.json", report)


def wait_for_trial(server, root: Path, case_id: str, *, timeout: float = 120) -> int:
    """Wait for the owned process and verify the HTTP endpoint identifies this trial."""
    deadline = monotonic() + timeout
    address = root / "server-address.json"
    while monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError(f"Isolated server exited {server.returncode}; see server.log")
        if not address.is_file():
            sleep(0.2)
            continue
        data = json.loads(address.read_text(encoding="utf-8"))
        if (data.get("pid") != server.pid or data.get("case_id") != case_id
                or data.get("run_id") != root.name or not isinstance(data.get("port"), int)
                or not 0 < data["port"] < 65536):
            raise RuntimeError("Isolated server address identifies a different trial")
        try:
            with urlopen(f"http://127.0.0.1:{data['port']}/__benchmark__/scenario", timeout=2) as reply:
                scenario = json.load(reply)
        except (OSError, ValueError):
            sleep(0.2)
            continue
        if scenario.get("case_id") != case_id or scenario.get("run_id") != root.name:
            raise RuntimeError("Isolated HTTP endpoint belongs to a different trial")
        return data["port"]
    raise TimeoutError("Isolated server startup timed out")


def execute_case(batch, case, index, manifest, dataset_path):
    from creatoros.evaluation.benchmark.manifest import verify_manifest
    from creatoros.evaluation.benchmark.program_evidence_guard import verify_guard_version
    verify_guard_version(manifest)
    verified_manifest = verify_manifest(batch / "manifest.json")["manifest"]
    verify_guard_version(verified_manifest)
    if verified_manifest.get("model_configuration", {}).get("benchmark_run_id", batch.name) != batch.name:
        raise ValueError("Frozen manifest belongs to a different batch")
    trial_binding = {"benchmark_batch_id": batch.name,
                     "benchmark_manifest_sha256": hashlib.sha256((batch / "manifest.json").read_bytes()).hexdigest()}
    root = batch / "records" / uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    (batch / "claims").mkdir(exist_ok=True)
    with (batch / "claims" / (case["id"] + ".json")).open("x", encoding="utf-8") as out:
        json.dump({"case_id": case["id"], "run_id": root.name, "first_attempt": True, **trial_binding}, out)
    env = {**os.environ, "PYTHONUTF8": "1", "DATABASE_URL": f"sqlite:///{(root / 'bootstrap.db').as_posix()}"}
    log = (root / "server.log").open("wb")
    server = subprocess.Popen([sys.executable, "-m", "creatoros.evaluation.benchmark.server", "--case", case["id"],
        "--root", str(root), "--dataset", str(dataset_path), "--manifest", str(batch / "manifest.json"), "--port", "0"],
        cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT)
    stage = "server_start"
    try:
        port = wait_for_trial(server, root, case["id"])
        stage = "real_browser"
        with (root / "driver.log").open("wb") as output:
            driver = subprocess.run(["node", str(REPO / "web/e2e/live-benchmark-v2.mjs"),
                f"http://127.0.0.1:{port}", str(root)], cwd=REPO, env=env, stdout=output,
                stderr=subprocess.STDOUT, timeout=4600 if case["live_codex"] else 800)
        if not (root / "report.json").exists():
            raise RuntimeError(f"Browser exited {driver.returncode} before a report was saved")
    except Exception as error:
        fallback_report(root, case, manifest, error, stage, trial_binding=trial_binding)
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=20)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
        log.close()
        from creatoros.evaluation.benchmark.io import now, write_json
        write_json(root / "isolation.json", {"trial_root": str(root), "isolated_server_pid": server.pid,
            "server_stopped": server.poll() is not None, "server_exit_code": server.returncode,
            "closed_at": now(), "world_retained_for_audit": True,
            "teardown": "owned process stopped; independent database/files retained, never resumed automatically",
            "external_side_effects": "API charges and shared SDK session/cache writes cannot be rolled back"})
    report = json.loads((root / "report.json").read_text(encoding="utf-8"))
    print(json.dumps({"case": case["id"], "execution": report["execution_status"], "automatic": report["auto_status"],
        "semantic": report.get("semantic_status"), "error": report.get("error"), "path": str(root)}, ensure_ascii=False), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["freeze", "run", "report"])
    parser.add_argument("--batch", required=True)
    parser.add_argument("--workers", type=int, default=3, choices=range(1, 4))
    parser.add_argument("--dataset", type=Path, help="Dataset for freeze; run/report use the frozen dataset")
    parser.add_argument("--cases", help="Only when freezing: explicit comma-separated protocol preflight subset")
    parser.add_argument("--nonvideo-guard", action="store_true", help="Freeze the restricted nonvideo policy; requires explicit cases")
    parser.add_argument("--local-dispatch-guard", action="store_true", help="Freeze local-only Agent dispatch guard with background workers disabled")
    parser.add_argument("--local-dispatch-version", choices=["local-dispatch-v1", "local-dispatch-v2", "local-dispatch-v3", "local-dispatch-v4", "local-dispatch-v5"], help="Explicit local dispatch policy version")
    parser.add_argument("--catalog-binding", help="JSON frozen suite identity; only for explicit regression freeze")
    parser.add_argument("--catalog-binding-file", type=Path, help="UTF-8 JSON frozen suite identity for large catalogs")
    args = parser.parse_args()
    if not args.batch or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in args.batch):
        parser.error("Use a simple immutable batch name")
    root = REPO / "data/agent-benchmark-v2" / args.batch
    if args.action == "freeze":
        if args.catalog_binding and args.catalog_binding_file:
            parser.error("Choose one catalog binding input")
        if args.local_dispatch_version and not args.local_dispatch_guard:
            parser.error("Local dispatch version requires its guard")
        if args.local_dispatch_guard and not args.nonvideo_guard:
            parser.error("Local dispatch requires the nonvideo guard")
        local_dispatch = (args.local_dispatch_version or True) if args.local_dispatch_guard else False
        if args.nonvideo_guard and local_dispatch not in {"local-dispatch-v4", "local-dispatch-v5"}:
            from creatoros.evaluation.benchmark.nonvideo_guard import policy
            policy(args.cases.split(",") if args.cases else None, local_dispatch=local_dispatch)
        root.mkdir(parents=True, exist_ok=False)
        raw_binding = args.catalog_binding
        if args.catalog_binding_file:
            with args.catalog_binding_file.open("rb") as stream:
                data = stream.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                parser.error("Catalog binding exceeds size limit")
            raw_binding = data.decode("utf-8")
        binding = json.loads(raw_binding) if raw_binding else None
        manifest = freeze(root, args.dataset or DATASET, args.cases.split(",") if args.cases else None, nonvideo_guard=args.nonvideo_guard, local_dispatch=local_dispatch,
                          catalog_binding=binding)
        print(json.dumps({"batch": args.batch, "selected_cases": manifest["selected_case_ids"],
                          "frozen_files": len(manifest["source_hashes"]), "path": str(root)}, ensure_ascii=False))
    else:
        if args.nonvideo_guard or args.local_dispatch_guard or args.local_dispatch_version or args.catalog_binding or args.catalog_binding_file or args.cases:
            parser.error("Guard and case selection must be frozen, not changed at run/report time")
        from creatoros.evaluation.benchmark.manifest import frozen_dataset_path, verify_manifest
        from creatoros.evaluation.benchmark.report import summarize_batch
        # Historical reporting must not depend on today's source tree; the
        # report reader checks the frozen dataset copy against its saved hash.
        manifest = (verify_manifest(root / "manifest.json")["manifest"] if args.action == "run"
                    else json.loads((root / "manifest.json").read_text(encoding="utf-8")))
        dataset_path = frozen_dataset_path(root / "manifest.json", manifest).resolve()
        original_path = (REPO / manifest["dataset_path"]).resolve()
        if args.dataset is not None and args.dataset.resolve() not in {dataset_path, original_path}:
            raise ValueError("Run/report must use the frozen dataset")
        if args.action == "run":
            from creatoros.evaluation.benchmark.nonvideo_guard import validate
            from creatoros.evaluation.benchmark.program_evidence_guard import verify_guard_version
            verify_guard_version(manifest)
            validate(manifest)
            cases = json.loads(dataset_path.read_text(encoding="utf-8"))["cases"]
            selected = [case for case in cases if case["id"] in manifest["selected_case_ids"]]
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                futures = [executor.submit(execute_case, root, case, i, manifest, dataset_path) for i, case in enumerate(selected)]
                for future in as_completed(futures):
                    future.result()
                    summarize_batch(root, dataset_path)
            verify_manifest(root / "manifest.json")
        result = summarize_batch(root, dataset_path)
        print(json.dumps({"summary": str(root / "summary.json"), "report": str(root / "RESULTS.md")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
