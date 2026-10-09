"""Explicit zero-paid-call reliability baseline; never discovers live_* scripts.

Run: python -m tests.run_reliability
Each test has its own process and timeout. Failures do not skip the remainder.
"""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

CASES = [
    ("diagnostic_io", ["-m", "unittest", "tests.test_diagnostic_io", "-v"]),
    ("worker_finalization", ["-m", "unittest", "tests.test_worker_finalization", "-v"]),
    *[(name, ["-m", "tests." + name]) for name in (
        "smoke_codex_completed_turn", "smoke_production_progress", "smoke_extraction_activity",
        "smoke_codex_public_events", "smoke_skill_extraction", "smoke_skill_draft_files",
        "smoke_skill_workbench", "smoke_topic_research_sdk", "smoke_content_discussion",
        "smoke_native_production", "smoke_worker_delivery", "smoke_studio_production_progress",
        "smoke_studio_executor",
    )],
]


def main():
    workspace = Path(__file__).resolve().parents[1]
    at = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    evidence = workspace / "tmp" / ("reliability-" + at)
    evidence.mkdir(parents=True)
    results = []
    for name, args in CASES:
        started = monotonic()
        try:
            process = subprocess.run([sys.executable, *args], cwd=workspace, timeout=120,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            status = "passed" if process.returncode == 0 else "failed"
            output = process.stdout
        except subprocess.TimeoutExpired as error:
            status, output = "timeout", error.stdout or b""
        (evidence / (name + ".log")).write_bytes(output)
        results.append({"name": name, "status": status, "seconds": round(monotonic() - started, 2)})
        print(json.dumps(results[-1]), flush=True)
    report = {"paid_calls": False, "all_passed": all(r["status"] == "passed" for r in results), "results": results}
    (evidence / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"report={evidence / 'report.json'}", flush=True)
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
