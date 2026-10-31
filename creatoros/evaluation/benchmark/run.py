"""Explicit paid freeze/run CLI. Every case gets its own process and SQLite."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
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


def view_dataset(dataset):
    return {"schema_version": 1, "dataset_id": dataset["dataset_id"], "cases": [
        {"id": c["id"], "title": c["title"], "category": c["category"], "split": c["split"],
         "steps": [{**step, **({"details": "冻结的外部控制事件，不是Agent行动"} if step["kind"] == "event" else {})} for step in c["steps"]],
         "assertions": [{"id": f"outcome-{n}", "pass": json.dumps(exp, ensure_ascii=False),
                         "evidence": ["before.json", "after.json"]} for n, exp in enumerate(c["expectations"])],
         "manual_checks": ["未作人工质量校准；自动语义Judge与程序判分分别展示。"]}
        for c in dataset["cases"]]}


def freeze(root, dataset_path, case_ids=None):
    from creatoros.evaluation.benchmark.manifest import freeze_manifest
    from creatoros.integrations.codex import CODEX_MODEL, CODEX_EFFORT
    from importlib.metadata import version
    pricing = {"currency": "USD", "source": "https://api-docs.deepseek.com/quick_start/pricing/",
        "frozen_at": datetime.now(timezone.utc).isoformat(), "billing_note": "bounds span off-peak and peak; estimate, not invoice",
        "aliases": {"deepseek-v4-flash": "deepseek-flash"}, "models": {"deepseek-flash": {
            "cache_hit_per_million": [0.003, 0.006], "cache_miss_per_million": [0.15, 0.30],
            "output_per_million": [0.6, 1.2]}}}
    configuration = {"provider_request_model": "deepseek-flash", "provider": "deepseek", "retries": 0,
        "agent_reasoning": "product default high", "judge_model": "deepseek-flash", "judge_reasoning": "none",
        "codex_model": CODEX_MODEL, "codex_effort": CODEX_EFFORT, "codex_sdk_version": version("openai-codex"),
        "codex_billing": "subscription; monetary cost unknown", "browser": "Chrome real SPA",
        "max_concurrent_cases": 3, "live_codex_timeout_seconds": 2100}
    manifest = freeze_manifest(root / "manifest.json", dataset_path, pricing=pricing,
                               model_configuration=configuration, case_ids=case_ids)
    (root / "view-cases.json").write_text(json.dumps(view_dataset(json.loads(dataset_path.read_text(encoding="utf-8")))), encoding="utf-8")
    return manifest


def fallback_report(root, case, manifest, error, stage):
    from creatoros.evaluation.run import now, write_json
    from creatoros.evaluation.benchmark.metrics import aggregate_metrics
    if (root / "report.json").exists():
        return
    requests = json.loads((root / "requests.json").read_text(encoding="utf-8")) if (root / "requests.json").exists() else []
    metrics = {key: aggregate_metrics([r for r in requests if key == "all_deepseek" or r.get("purpose") == key], manifest["pricing"])
               for key in ("agent", "rsi", "judge", "all_deepseek")}
    write_json(root / "metrics.json", metrics)
    stamp = now()
    write_json(root / "report.json", {"schema_version": 1, "run_id": root.name, "dataset_id": manifest["dataset_id"],
        "case_id": case["id"], "started_at": stamp, "finished_at": stamp, "elapsed_seconds": 0,
        "git_sha": manifest["git"]["sha"], "fixture_version": "benchmark-world-v2-1", "execution_mode": "live",
        "execution_status": "failed", "auto_status": "failed", "semantic_status": "needs_review", "review": None,
        "model": {"name": "deepseek-flash", "provider": "deepseek"}, "usage": None, "metrics": metrics,
        "error": {"kind": type(error).__name__, "message": str(error), "stage": stage},
        "dimensions": {"task_success": "failed", "boundary_enforced": "needs_review", "state_consistent": "needs_review", "protocol_valid": "failed"},
        "checks": [{"id": "launcher", "label": "真实前端执行完成", "status": "failed", "detail": str(error), "evidence": ["metrics.json"]}],
        "evidence_files": [{"name": "metrics.json", "label": "已观测用量；缺失不记0"}]})


def execute_case(batch, case, index, manifest, dataset_path):
    from creatoros.evaluation.benchmark.manifest import verify_manifest
    verify_manifest(batch / "manifest.json")
    root = batch / "records" / uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    (batch / "claims").mkdir(exist_ok=True)
    with (batch / "claims" / (case["id"] + ".json")).open("x", encoding="utf-8") as out:
        json.dump({"case_id": case["id"], "run_id": root.name, "first_attempt": True}, out)
    port = 9100 + index
    env = {**os.environ, "PYTHONUTF8": "1", "DATABASE_URL": f"sqlite:///{(root / 'bootstrap.db').as_posix()}"}
    log = (root / "server.log").open("wb")
    server = subprocess.Popen([sys.executable, "-m", "creatoros.evaluation.benchmark.server", "--case", case["id"],
        "--root", str(root), "--dataset", str(dataset_path), "--manifest", str(batch / "manifest.json"), "--port", str(port)],
        cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT)
    stage = "server_start"
    try:
        deadline = monotonic() + 120
        while monotonic() < deadline:
            if server.poll() is not None:
                raise RuntimeError(f"Isolated server exited {server.returncode}; see server.log")
            try:
                with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as reply:
                    if json.load(reply)["status"] == "ok":
                        break
            except (OSError, ValueError):
                sleep(0.5)
        else:
            raise TimeoutError("Isolated server startup timed out")
        stage = "real_browser"
        with (root / "driver.log").open("wb") as output:
            driver = subprocess.run(["node", str(REPO / "web/e2e/live-benchmark-v2.mjs"),
                f"http://127.0.0.1:{port}", str(root)], cwd=REPO, env=env, stdout=output,
                stderr=subprocess.STDOUT, timeout=4600 if case["live_codex"] else 800)
        if not (root / "report.json").exists():
            raise RuntimeError(f"Browser exited {driver.returncode} before a report was saved")
    except Exception as error:
        fallback_report(root, case, manifest, error, stage)
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=20)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
        log.close()
    report = json.loads((root / "report.json").read_text(encoding="utf-8"))
    print(json.dumps({"case": case["id"], "execution": report["execution_status"], "automatic": report["auto_status"],
        "semantic": report.get("semantic_status"), "error": report.get("error"), "path": str(root)}, ensure_ascii=False), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["freeze", "run", "report"])
    parser.add_argument("--batch", required=True)
    parser.add_argument("--workers", type=int, default=3, choices=range(1, 4))
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--cases", help="Only when freezing: explicit comma-separated protocol preflight subset")
    args = parser.parse_args()
    if not args.batch or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in args.batch):
        parser.error("Use a simple immutable batch name")
    root = REPO / "data/agent-benchmark-v2" / args.batch
    if args.action == "freeze":
        root.mkdir(parents=True, exist_ok=False)
        manifest = freeze(root, args.dataset, args.cases.split(",") if args.cases else None)
        print(json.dumps({"batch": args.batch, "selected_cases": manifest["selected_case_ids"],
                          "frozen_files": len(manifest["source_hashes"]), "path": str(root)}, ensure_ascii=False))
    else:
        from creatoros.evaluation.benchmark.manifest import verify_manifest
        from creatoros.evaluation.benchmark.report import summarize_batch
        if args.action == "run":
            manifest = verify_manifest(root / "manifest.json")["manifest"]
            cases = json.loads(args.dataset.read_text(encoding="utf-8"))["cases"]
            selected = [case for case in cases if case["id"] in manifest["selected_case_ids"]]
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                futures = [executor.submit(execute_case, root, case, i, manifest, args.dataset) for i, case in enumerate(selected)]
                for future in as_completed(futures):
                    future.result()
                    summarize_batch(root, args.dataset)
            verify_manifest(root / "manifest.json")
        result = summarize_batch(root, args.dataset)
        print(json.dumps({"summary": str(root / "summary.json"), "report": str(root / "RESULTS.md")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
