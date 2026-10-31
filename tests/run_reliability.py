"""Explicit zero-paid-call reliability baseline; never discovers live_* scripts.

Run: python -m tests.run_reliability
Each test has its own process and timeout. Failures do not skip the remainder.
Optional audio/video checks use existing ffmpeg/ffprobe on PATH or executable
paths in CREATOROS_FFMPEG / CREATOROS_FFPROBE; without them codec cases report
skipped while library, schema, scope and read-only visibility cases still run.
"""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

CASES = [
    ("safe_maintenance", ["-m", "unittest", "tests.test_maintenance_state",
                          "tests.test_maintenance_control", "tests.test_maintenance_cli", "tests.test_maintenance_process",
                          "tests.test_publication_feedback", "-v"]),
    ("judge_semantics", ["-m", "unittest", "tests.test_judge_regression_suite", "-v"]),
    ("topic_management", ["-m", "unittest", "tests.test_topic_management_http",
                          "tests.test_topic_edit_migration", "tests.test_topic_tool_contract",
                          "tests.test_host_tool_contract", "-v"]),
    ("chat_context_reasoning", ["-m", "unittest", "tests.test_chat_context_reasoning", "-v"]),
    ("chat_focus", ["-m", "unittest", "tests.test_chat_focus", "-v"]),
    ("offline_adoption", ["-m", "unittest", "tests.test_offline_adoption", "-v"]),
    ("codex_environment", ["-m", "unittest", "tests.test_codex_shell", "tests.test_composition_environment", "-v"]),
    ("topic_library_order", ["-m", "unittest", "tests.test_topic_library_order", "tests.test_topic_library_production", "-v"]),
    ("auxiliary_job_queues", ["-m", "unittest", "tests.test_auxiliary_job_queues", "-v"]),
    ("skill_job_queue", ["-m", "unittest", "tests.test_skill_job_queue", "-v"]),
    ("topic_research_queue", ["-m", "unittest", "tests.test_topic_research_queue", "-v"]),
    ("skill_calibration", ["-m", "unittest", "tests.test_skill_calibration", "-v"]),
    ("skill_version_catalog", ["-m", "unittest", "tests.test_skill_versions", "-v"]),
    ("skill_version_lifecycle", ["-m", "unittest", "tests.test_skill_version_service", "-v"]),
    ("skill_version_scope", ["-m", "unittest", "tests.test_skill_version_scope", "-v"]),
    ("selected_skill_run", ["-m", "unittest", "tests.test_selected_skill_run", "-v"]),
    ("skill_libraries", ["-m", "unittest", "tests.test_skill_library", "tests.test_staged_production_agent", "-v"]),
    ("staged_production", ["-m", "unittest", "tests.test_staged_tasks", "tests.test_task_video_execution",
                            "tests.test_managed_materials", "tests.test_production_task_tools", "-v"]),
    ("skill_iteration_agent", ["-m", "unittest", "tests.test_skill_iteration_agent", "-v"]),
    ("skill_iteration_service", ["-m", "unittest", "tests.test_skill_iteration_service", "-v"]),
    ("skill_iteration_quality_policy", ["-m", "unittest", "tests.test_skill_iteration_quality_policy", "-v"]),
    ("skill_iteration_recovery", ["-m", "unittest", "tests.test_skill_iteration_judge_recovery",
                                  "tests.test_skill_trial_publication", "-v"]),
    ("rsi_production_guard", ["-m", "unittest", "tests.test_rsi_production_guard", "-v"]),
    ("chat_stream", ["-m", "unittest", "tests.test_chat_stream", "tests.test_chat_links", "-v"]),
    ("skill_trial_paths", ["-m", "unittest", "tests.test_skill_trial_paths", "-v"]),
    ("skill_assets", ["-m", "unittest", "tests.test_skill_assets", "tests.test_skill_asset_tools", "-v"]),
    ("skill_context", ["-m", "unittest", "tests.test_model_projection", "tests.test_tool_index_context", "tests.test_skill_context_projection",
                       "tests.test_skill_edit_batch", "tests.test_saved_skill_routing", "-v"]),
    ("work_inbox", ["-m", "unittest", "tests.test_work_inbox", "tests.test_staged_task_visibility", "-v"]),
    ("production_queue", ["-m", "unittest", "tests.test_production_queue", "tests.test_batch_production", "-v"]),
    ("diagnostic_io", ["-m", "unittest", "tests.test_diagnostic_io", "-v"]),
    ("worker_finalization", ["-m", "unittest", "tests.test_worker_finalization", "-v"]),
    *[(name, ["-m", "tests." + name]) for name in (
        "smoke_codex_completed_turn", "smoke_production_progress", "smoke_extraction_activity",
        "smoke_codex_public_events", "smoke_skill_extraction", "smoke_skill_draft_files",
        "smoke_skill_workbench", "smoke_topic_research_sdk", "smoke_content_discussion",
        "smoke_native_production", "smoke_worker_delivery", "smoke_studio_production_progress",
        "smoke_studio_executor", "smoke_topic_remove", "smoke_topic_remove_migration",
        "smoke_account_context", "smoke_worker_task_projection", "smoke_observation",
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
                timeout = 300 if name in {"safe_maintenance", "skill_calibration", "skill_libraries", "staged_production"} or name.startswith(("skill_version_", "skill_iteration_")) else 120
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
