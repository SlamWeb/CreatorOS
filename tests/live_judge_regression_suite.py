"""Opt-in paid DeepSeek boundary suite; no database, Codex or image generation.

Run explicitly with --allow-paid. The offline reliability runner must never
discover this module automatically. Each run requires a new ignored tmp root.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

from creatoros.config import PROJECT_ROOT
from creatoros.integrations import skill_iteration_judge as judge
from tests.judge_regression_suite import DATASET, DATASET_SHA256, SCORER_VERSION, digest, grade, load_dataset


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def validate_root(path):
    root = Path(path).resolve()
    temporary = (PROJECT_ROOT / "tmp").resolve()
    if root == temporary or not root.is_relative_to(temporary) or root.exists():
        raise ValueError("Use a NEW ignored PROJECT_ROOT/tmp subdirectory; old evidence is never overwritten")
    return root


def source_fingerprints():
    paths = [DATASET, Path(__file__), Path(__file__).with_name("judge_regression_suite.py"),
             PROJECT_ROOT / "creatoros" / "integrations" / "skill_iteration_judge.py",
             PROJECT_ROOT / "creatoros" / "ai" / "deepseek.py",
             PROJECT_ROOT / "creatoros" / "ai" / "context.py"]
    return {path.relative_to(PROJECT_ROOT).as_posix(): digest(path) for path in paths}


def git_state():
    def run(*args):
        result = subprocess.run(["git", *args], cwd=PROJECT_ROOT, capture_output=True,
                                text=True, encoding="utf-8", timeout=10)
        return result.stdout.strip() if result.returncode == 0 else "unavailable"
    return dict(head=run("rev-parse", "HEAD"), status_porcelain=run("status", "--short"))


def totals(results):
    known = [item["usage"] for item in results if item.get("usage") is not None]
    return dict(input_tokens=sum(item["input_tokens"] for item in known),
                output_tokens=sum(item["output_tokens"] for item in known),
                cache_hit_tokens=sum(item.get("cache_hit_tokens", 0) for item in known),
                usage_known_requests=len(known), usage_unknown_requests=len(results) - len(known))


def run_suite(root):
    root = validate_root(root)
    data = load_dataset()
    sources = source_fingerprints()
    root.mkdir(parents=True)
    shutil.copyfile(DATASET, root / DATASET.name)
    manifest = dict(dataset_id=data["dataset_id"], dataset_sha256=DATASET_SHA256, scorer_version=SCORER_VERSION,
        started_at=datetime.now(timezone.utc).isoformat(), sources=sources, git=git_state(),
        cases=[case["id"] for case in data["cases"]], model_requests="one request per case; no automatic repair",
        images=[], limitations="Text-reference boundary tests, not vision quality, UI E2E, RSI convergence or a universal semantic grader")
    write_new(root / "manifest.json", manifest)
    results = []
    for case in data["cases"]:
        directory = root / case["id"]
        directory.mkdir()
        system = judge.system_prompt(case["kind"], case["payload"])
        request = dict(case=case, images=[], system=system,
            system_sha256=hashlib.sha256(system.encode("utf-8")).hexdigest(),
            messages=[dict(role="system", content=system), dict(role="user", content=[
                dict(type="text", text=json.dumps(case["payload"], ensure_ascii=False))])],
            max_output_tokens=8192)
        write_new(directory / "request.json", request)
        received = {}
        started = time.monotonic()

        def receipt(raw):
            received.update(raw)
            write_new(directory / "raw-response.json", raw)

        def provider():
            current = judge.default_provider()
            write_new(directory / "provider.json", dict(model=current.model,
                reasoning_effort=current.reasoning_effort,
                actual_reasoning_effort=current.actual_reasoning_effort,
                max_retries=current.client.max_retries, timeout_seconds=180))
            return current

        error = None
        try:
            judge.evaluate(case["kind"], case["payload"], [], provider_factory=provider, response_sink=receipt)
        except Exception as exception:
            error = dict(type=type(exception).__name__, message=str(exception)[:6000])
            write_new(directory / "error.json", error)
        if received:
            grading = grade(case, received)
            # A local storage/client failure is never hidden by a semantically
            # good response. No retries are made after an unknown request.
            if error is not None:
                grading["passed"] = False
                grading["checks"].append(dict(check="request_completed", expected="no execution error",
                                              actual=error, passed=False))
        else:
            grading = dict(passed=False, parsed=None, checks=[dict(check="model_response",
                expected="received public response and recorded usage", actual=error or "missing", passed=False)])
        item = dict(id=case["id"], name=case["name"], kind=case["kind"],
            passed=grading["passed"], elapsed_seconds=round(time.monotonic() - started, 3),
            usage=received.get("usage"), expected=case["expected"], checks=grading["checks"],
            result=grading["parsed"], error=error)
        write_new(directory / "result.json", item)
        results.append(item)
        print(json.dumps({key: item[key] for key in ("id", "name", "passed", "elapsed_seconds", "usage")},
                         ensure_ascii=False), flush=True)
    unchanged = sources == source_fingerprints()
    passed = sum(item["passed"] for item in results)
    report = dict(dataset_id=data["dataset_id"], dataset_sha256=DATASET_SHA256, scorer_version=SCORER_VERSION,
        finished_at=datetime.now(timezone.utc).isoformat(), source_files_unchanged=unchanged,
        passed=passed, failed=len(results) - passed, total=len(results),
        pass_rate=passed / len(results), usage=totals(results),
        results=results, manifest="manifest.json", success=unchanged and passed == len(results),
        limitations=manifest["limitations"])
    write_new(root / "report.json", report)
    print(json.dumps(dict(root=str(root), passed=passed, total=len(results),
                         usage=report["usage"], source_files_unchanged=unchanged), ensure_ascii=False), flush=True)
    return 0 if report["success"] else 1


def regrade_suite(source, root):
    """Re-evaluate saved replies, never create/call a provider or change old files."""
    source = Path(source).resolve(strict=True)
    root = validate_root(root)
    temporary = (PROJECT_ROOT / "tmp").resolve()
    if source == temporary or not source.is_relative_to(temporary) or not source.is_dir():
        raise ValueError("Only regrade a retained isolated tmp evidence directory")
    if root.is_relative_to(source):
        raise ValueError("Never place a regrade inside the original evidence directory")
    data = load_dataset()
    report_path = source / "report.json"
    previous = json.loads(report_path.read_text(encoding="utf-8"))
    if previous["dataset_sha256"] != DATASET_SHA256:
        raise ValueError("Saved report is not this frozen dataset")
    if [item["id"] for item in previous["results"]] != [case["id"] for case in data["cases"]]:
        raise ValueError("Saved report has missing or reordered cases")
    before = {path.relative_to(source).as_posix(): digest(path) for path in source.rglob("*") if path.is_file()}
    fingerprints = source_fingerprints()
    root.mkdir(parents=True)
    write_new(root / "manifest.json", dict(kind="offline_regrade", source=str(source),
        source_files=before, dataset_sha256=DATASET_SHA256, scorer_version=SCORER_VERSION,
        sources=fingerprints, git=git_state(), paid_model_requests=0))
    results = []
    for case, old in zip(data["cases"], previous["results"]):
        request = json.loads((source / case["id"] / "request.json").read_text(encoding="utf-8"))
        if request["case"] != case:
            raise ValueError("Saved input/expected answer differs from the frozen dataset")
        raw_path = source / case["id"] / "raw-response.json"
        if raw_path.exists():
            grading = grade(case, json.loads(raw_path.read_text(encoding="utf-8")))
        else:
            grading = dict(passed=False, parsed=None, checks=[dict(check="retained_response", expected="saved raw",
                actual="missing; no provider request is made", passed=False)])
        # The replay is not allowed to erase an original transport or receipt
        # persistence error just because a usable raw body happens to exist.
        if old.get("error"):
            grading["passed"] = False
            grading["checks"].append(dict(check="original_execution_error", expected=None,
                                         actual=old["error"], passed=False))
        item = dict(id=case["id"], name=case["name"], passed=grading["passed"],
            original_passed=old["passed"], expected=case["expected"], checks=grading["checks"],
            result=grading["parsed"], raw_response=str(raw_path), original_usage=old.get("usage"))
        directory = root / case["id"]
        directory.mkdir()
        write_new(directory / "result.json", item)
        results.append(item)
    after = {path.relative_to(source).as_posix(): digest(path) for path in source.rglob("*") if path.is_file()}
    unchanged = before == after and fingerprints == source_fingerprints()
    passed = sum(item["passed"] for item in results)
    report = dict(kind="offline_regrade", dataset_id=data["dataset_id"], dataset_sha256=DATASET_SHA256,
        scorer_version=SCORER_VERSION, source_report=str(report_path), source_report_sha256=before["report.json"],
        original_passed=previous["passed"], passed=passed, total=len(results), failed=len(results) - passed,
        pass_rate=passed / len(results), paid_model_requests=0, additional_usage=totals([]),
        original_usage=previous["usage"], old_evidence_unchanged=before == after,
        source_files_unchanged=unchanged, success=unchanged and passed == len(results), results=results,
        limitations="Offline scoring of the SAME eight responses; not another live model round or product-quality gain")
    write_new(root / "report.json", report)
    print(json.dumps(dict(root=str(root), kind="offline_regrade", passed=passed, total=len(results),
                         original_passed=previous["passed"], paid_model_requests=0,
                         old_evidence_unchanged=before == after), ensure_ascii=False), flush=True)
    return 0 if report["success"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--allow-paid", action="store_true", help="Explicitly authorize real DeepSeek API requests")
    parser.add_argument("--regrade-from", type=Path, help="Offline only: score retained replies into a new root; zero model requests")
    args = parser.parse_args(argv)
    if args.regrade_from:
        return regrade_suite(args.regrade_from, validate_root(args.root))
    if not args.allow_paid:
        parser.error("Real DeepSeek calls require --allow-paid; offline tests never pay")
    root = validate_root(args.root)
    return run_suite(root)


if __name__ == "__main__":
    raise SystemExit(main())
