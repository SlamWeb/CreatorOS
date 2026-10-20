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
    ("codex_environment", ["-m", "unittest", "tests.test_codex_shell", "tests.test_composition_environment", "-v"]),
    ("topic_library_order", ["-m", "unittest", "tests.test_topic_library_order", "-v"]),
    ("auxiliary_job_queues", ["-m", "unittest", "tests.test_auxiliary_job_queues", "-v"]),
    ("skill_job_queue", ["-m", "unittest", "tests.test_skill_job_queue", "-v"]),
    ("topic_research_queue", ["-m", "unittest", "tests.test_topic_research_queue", "-v"]),
    ("skill_calibration", ["-m", "unittest", "tests.test_skill_calibration", "-v"]),
    ("work_inbox", ["-m", "unittest", "tests.test_work_inbox", "-v"]),
    ("production_queue", ["-m", "unittest", "tests.test_production_queue", "tests.test_batch_production", "-v"]),
    ("diagnostic_io", ["-m", "unittest", "tests.test_diagnostic_io", "-v"]),
    ("worker_finalization", ["-m", "unittest", "tests.test_worker_finalization", "-v"]),
    *[(name, ["-m", "tests." + name]) for name in (
        "smoke_codex_completed_turn", "smoke_production_progress", "smoke_extraction_activity",
        "smoke_codex_public_events", "smoke_skill_extraction", "smoke_skill_draft_files",
        "smoke_skill_workbench", "smoke_topic_research_sdk", "smoke_content_discussion",
        "smoke_native_production", "smoke_worker_delivery", "smoke_studio_production_progress",
        "smoke_studio_executor", "smoke_topic_remove", "smoke_topic_remove_migration",
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
        # Stream to disk while running: a slow/hung group can be inspected before
        # its deadline, and timeout never discards the last completed test name.
        with (evidence / (name + ".log")).open("wb") as output:
            try:
                # Calibration uses real migrations/files per case and already
                # exceeded the original 120s group budget before queue work.
                timeout = 300 if name == "skill_calibration" else 120
                process = subprocess.run([sys.executable, *args], cwd=workspace, timeout=timeout,
                                         stdout=output, stderr=subprocess.STDOUT)
                status = "passed" if process.returncode == 0 else "failed"
            except subprocess.TimeoutExpired:
                status = "timeout"
        results.append({"name": name, "status": status, "seconds": round(monotonic() - started, 2)})
        print(json.dumps(results[-1]), flush=True)
    report = {"paid_calls": False, "all_passed": all(r["status"] == "passed" for r in results), "results": results}
    (evidence / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"report={evidence / 'report.json'}", flush=True)
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
