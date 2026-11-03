"""Explicit zero-paid-call reliability baseline; never discovers live_* scripts.

Run: python -m tests.run_reliability
Each test has its own process and timeout. Failures do not skip the remainder.
Requires working ffmpeg/ffprobe on PATH or CREATOROS_FFMPEG / CREATOROS_FFPROBE.
Missing codecs or skipped unittest cases are never reported as full coverage.
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from tests.ci_environment import verify_media

CASES = [
    ("reliability_runner", ["-m", "unittest", "tests.test_reliability_runner", "tests.test_ci_environment", "-v"]),
    ("plan_capabilities", ["-m", "unittest", "tests.test_plan_capabilities", "-v"]),
    ("plan_duplicate_steps", ["-m", "unittest", "tests.test_plan_duplicate_steps", "-v"]),
    ("production_observation", ["-m", "unittest", "tests.test_production_observation", "-v"]),
    ("run_import_boundary", ["-m", "unittest", "tests.test_runs_import_boundary", "-v"]),
    ("delivery_boundary", ["-m", "unittest", "tests.test_delivery_boundary", "-v"]),
    ("video_offline_correction", ["-m", "unittest", "tests.test_video_offline_correction", "-v"]),
    ("video_receipts", ["-m", "unittest", "tests.test_video_delivery_contract", "tests.test_video_submit_tool",
                        "tests.test_video_recovery_links", "-v"]),
    ("background_trace_routes", ["-m", "unittest", "tests.test_background_trace_routes", "-v"]),
    ("image_result_recovery", ["-m", "unittest", "tests.test_received_image_handoff", "-v"]),
    ("media_export_recovery", ["-m", "unittest", "tests.test_media_export_recovery", "-v"]),
    ("safe_maintenance", ["-m", "unittest", "tests.test_maintenance_state",
                          "tests.test_maintenance_control", "tests.test_maintenance_cli", "tests.test_maintenance_process",
                          "tests.test_maintenance_cleanup",
                          "tests.test_publication_feedback", "-v"]),
    ("judge_semantics", ["-m", "unittest", "tests.test_judge_regression_suite", "-v"]),
    ("topic_management", ["-m", "unittest", "tests.test_topic_management_http",
                          "tests.test_topic_edit_migration", "tests.test_topic_tool_contract",
                          "tests.test_host_tool_contract", "-v"]),
    ("reference_skill_boundary", ["-m", "unittest", "tests.test_reference_skill_boundary", "-v"]),
    ("production_submission", ["-m", "unittest", "tests.test_production_submission",
                                "tests.test_production_skill_lock_order", "-v"]),
    ("reference_production", ["-m", "unittest", "tests.test_reference_production_service",
        "tests.test_reference_skill_selection", "tests.test_reference_production_api",
        "tests.test_reference_tool_handoff", "tests.test_topic_references",
        "tests.test_reference_protocol_audit", "-v"]),
    ("skill_extraction_delivery", ["-m", "unittest", "tests.test_extraction_batch",
        "tests.test_extraction_host_delivery", "-v"]),
    ("windows_skill_publication", ["-m", "unittest", "tests.test_freeze_skill_windows", "-v"]),
    ("skill_git_install", ["-m", "unittest", "tests.test_git_skill_installer_longpaths", "-v"]),
    ("chat_context_reasoning", ["-m", "unittest", "tests.test_chat_context_reasoning",
                                "tests.test_session_snapshot_io", "tests.test_chat_view_atomic", "-v"]),
    ("chat_focus", ["-m", "unittest", "tests.test_chat_focus", "-v"]),
    ("creator_directory", ["-m", "unittest", "tests.test_creator_directory", "-v"]),
    ("offline_adoption", ["-m", "unittest", "tests.test_offline_adoption", "-v"]),
    ("codex_environment", ["-m", "unittest", "tests.test_codex_shell", "tests.test_composition_environment", "-v"]),
    ("topic_library_order", ["-m", "unittest", "tests.test_topic_library_order", "tests.test_topic_library_production", "-v"]),
    ("auxiliary_job_queues", ["-m", "unittest", "tests.test_auxiliary_job_queues", "-v"]),
    ("skill_job_queue", ["-m", "unittest", "tests.test_skill_job_queue", "-v"]),
    ("topic_research_queue", ["-m", "unittest", "tests.test_topic_research_queue", "tests.test_topic_research_skill_context", "-v"]),
    ("skill_calibration", ["-m", "unittest", "tests.test_skill_calibration", "-v"]),
    ("skill_version_catalog", ["-m", "unittest", "tests.test_skill_versions", "-v"]),
    ("skill_version_lifecycle", ["-m", "unittest", "tests.test_skill_version_service",
                                 "tests.test_skill_switch_intent", "-v"]),
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
    ("production_queue", ["-m", "unittest", "tests.test_production_queue", "tests.test_queue_history", "tests.test_queue_owner_transfer", "tests.test_recovery_evidence", "tests.test_batch_production", "-v"]),
    ("diagnostic_io", ["-m", "unittest", "tests.test_diagnostic_io", "-v"]),
    ("worker_finalization", ["-m", "unittest", "tests.test_worker_finalization", "-v"]),
    # Controlled transport + real isolated files; reviewed and run without model credentials.
    ("native_delivery_protocol", ["-m", "unittest", "tests.test_codex_dynamic_tools",
        "tests.test_artifact_submission", "tests.test_file_delivery", "tests.test_native_delivery_recovery",
        "tests.test_native_image_assets", "tests.test_native_thread_continuity",
        "tests.test_graphic_delivery_contract", "tests.test_structured_handoff", "-v"]),
    ("plan_workflows", ["-m", "unittest", "tests.test_agent_plans", "tests.test_plan_tool_contract",
        "tests.test_plan_production_outcomes",
        "tests.test_plan_replanning_safety", "tests.test_plan_incremental_contract", "tests.test_plan_continuation", "tests.test_plan_continuation_recovery",
        "tests.test_plan_unknown_edit", "tests.test_plan_continuation_schedule_gate", "tests.test_plan_continuation_budget_recovery",
        "tests.test_plan_continuation_unknown_write",
        "tests.test_background_agent", "tests.test_background_wait", "-v"]),
    ("media_task_lifecycle", ["-m", "unittest", "tests.test_bgm_analysis", "tests.test_media_collection_agent",
        "tests.test_media_collections", "tests.test_production_task_collections", "tests.test_production_tasks",
        "tests.test_pending_tasks", "tests.test_task_navigation_contract", "tests.test_task_stop_remove",
        "tests.test_simple_skill_tasks", "tests.test_shared_skill_fork", "-v"]),
    ("skill_chat_feedback", ["-m", "unittest", "tests.test_authored_skill_actions", "tests.test_chat_attachments", "tests.test_skill_write_receipts",
        "tests.test_feedback_tool", "tests.test_skill_authoring", "tests.test_skill_example_context", "-v"]),
    ("business_workflows", ["-m", "unittest", "tests.test_fresh_content_run", "tests.test_fresh_production_api",
        "tests.test_fresh_skill_ids", "tests.test_manage_production_task_tool", "tests.test_publication_matching",
        "tests.test_publication_mcp_client", "tests.test_research_model_contract", "tests.test_series_rename",
        "tests.test_single_task_preview", "tests.test_topic_delivery", "-v"]),
    ("parallel_run_ownership", ["-m", "unittest", "tests.test_concurrent_execution_guard",
        "tests.test_parallel_production", "tests.test_parallel_production_api", "-v"]),
    *[(name, ["-m", "tests." + name]) for name in (
        "smoke_codex_completed_turn", "smoke_production_progress", "smoke_extraction_activity",
        "smoke_codex_public_events", "smoke_skill_extraction", "smoke_skill_draft_files",
        "smoke_skill_workbench", "smoke_topic_research_sdk", "smoke_content_discussion",
        "smoke_native_production", "smoke_worker_delivery", "smoke_studio_production_progress",
        "smoke_studio_executor", "smoke_topic_remove", "smoke_topic_remove_migration",
        "smoke_account_context", "smoke_worker_task_projection", "smoke_observation",
    )],
]

BENCHMARK_CASES = [("benchmark_contracts", ["-m", "unittest", *[
    "tests." + name for name in (
        "test_benchmark_metrics_v2", "test_benchmark_grader_v2", "test_benchmark_manifest_v2",
        "test_benchmark_report_v2", "test_benchmark_server_v2", "test_benchmark_cases_v2", "test_benchmark_nonvideo_guard",
        "test_benchmark_capture_v2", "test_benchmark_isolation", "test_benchmark_probes_v2",
        "test_benchmark_world_v2",
        "test_benchmark_scoring", "test_benchmark_b12_title_matching", "test_benchmark_store_v3", "test_benchmark_media_oracles_v3",
        "test_benchmark_oracle_revision", "test_action_contract", "test_production_contract",
    )], "-q"])]


# Independent server/build/fixture contracts.  These use Node and Chrome in
# addition to Python and codecs, so CI runs them in a separate job.
ISOLATION_CASES = [
    ("parallel_worktrees", ["-m", "unittest", "tests.test_isolated_build", "tests.test_isolated_dev", "-v"]),
    ("browser_fixture_isolation", ["-m", "unittest", "tests.test_e2e_all",
        "tests.test_e2e_producer", "tests.test_media_e2e_isolation", "-v"]),
    ("agent_fixture_isolation", ["-m", "unittest", "tests.test_batch_images_isolation",
        "tests.test_live_production_tasks_isolation", "tests.test_live_reference_entry",
        "tests.test_plan_continuation_isolation", "tests.test_skill_edit_batch_isolation", "-v"]),
]


def module_inventory(workspace, selected):
    def modules(cases):
        return {arg for _, command in cases for arg in command if arg.startswith("tests.test_")}
    all_modules = {"tests." + path.stem for path in (Path(workspace) / "tests").glob("test_*.py")}
    backend, benchmark, isolation, current = modules(CASES), modules(BENCHMARK_CASES), modules(ISOLATION_CASES), modules(selected)
    return {"all_test_modules": sorted(all_modules), "declared_backend_modules": sorted(backend),
            "declared_benchmark_modules": sorted(benchmark), "declared_isolation_modules": sorted(isolation),
            "selected_modules": sorted(current),
            "not_run_in_this_invocation": sorted(all_modules - current),
            "not_in_any_python_gate": sorted(all_modules - backend - benchmark - isolation),
            "declared_modules_missing_from_checkout": sorted((backend | benchmark | isolation) - all_modules),
            "note": "Inventory reads filenames only; unreviewed modules are never imported or executed."}


def select_cases(cases, shard):
    if shard is None:
        return list(cases)
    try:
        index, total = map(int, shard.split("/"))
    except (ValueError, AttributeError) as error:
        raise ValueError("Shard must be index/total") from error
    if not 1 <= index <= total or total > len(cases):
        raise ValueError("Shard must select a nonempty part of the allowlist")
    return list(cases)[index - 1::total]


def classify_unittest(returncode, receipt):
    if returncode != 0:
        return "failed"
    required = {"discovered", "run", "skipped", "failures", "errors", "expected_failures", "unexpected_successes"}
    if not isinstance(receipt, dict) or any(type(receipt.get(key)) is not int or receipt[key] < 0 for key in required):
        return "incomplete"
    if receipt["failures"] or receipt["errors"] or receipt["unexpected_successes"]:
        return "failed"
    if not receipt["discovered"] or receipt["run"] != receipt["discovered"] or receipt["skipped"] or receipt["expected_failures"]:
        return "incomplete"
    return "passed"


def reject_operational_plan_fixture(workspace, command):
    # These two historical modules could dispatch tools to the operational port.
    # Never execute their old checkout versions before the fixture fix is integrated.
    for module in {"tests.test_agent_plans", "tests.test_plan_tool_contract"}.intersection(command):
        source = (Path(workspace) / Path(*module.split("."))).with_suffix(".py")
        if "http://127.0.0.1:8765" in source.read_text(encoding="utf-8"):
            raise ValueError(f"Unsafe operational URL in {module}; integrate the isolated plan fixture before running it")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard", help="Deterministic nonoverlapping allowlist partition, e.g. 1/2")
    parser.add_argument("--suite", choices=("backend", "benchmark", "isolation"), default="backend")
    parser.add_argument("--inventory", action="store_true", help="Read-only module inventory; no tests or media tools run")
    args = parser.parse_args(argv)
    suite = {"backend": CASES, "benchmark": BENCHMARK_CASES, "isolation": ISOLATION_CASES}[args.suite]
    selected = select_cases(suite, args.shard)
    workspace = Path(__file__).resolve().parents[1]
    inventory = module_inventory(workspace, selected)
    if args.inventory:
        print(json.dumps(inventory, indent=2), flush=True)
        return 0
    at = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    evidence = workspace / "tmp" / ("reliability-" + at)
    evidence.mkdir(parents=True)
    results = []
    try:
        verify_media(evidence / "media")
    except Exception as error:
        report = {"paid_calls": False, "all_passed": False, "status": "failed", "results": [],
                  "preflight_error": f"{type(error).__name__}: {error}", "shard": args.shard,
                  "unrun_groups": [name for name, _ in selected], "module_inventory": inventory}
        (evidence / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report), flush=True)
        return 1
    for name, command in selected:
        started = monotonic()
        receipt_path = evidence / (name + ".unittest.json")
        is_unittest = command[:2] == ["-m", "unittest"]
        if is_unittest:
            command = ["-m", "tests.reliability_unittest", "--result", str(receipt_path), *command[2:]]
        receipt = None
        # Stream to disk while running: a slow/hung group can be inspected before
        # its deadline, and timeout never discards the last completed test name.
        with (evidence / (name + ".log")).open("wb") as output:
            try:
                reject_operational_plan_fixture(workspace, command)
                # Calibration and queue-owner tests use isolated migrations,
                # real loopback services and synchronized worker processes.
                timeout = 600 if name in {"native_delivery_protocol", "plan_workflows", "media_task_lifecycle", "skill_chat_feedback", "business_workflows", "parallel_run_ownership", "reference_production", "skill_extraction_delivery", "production_submission", "parallel_worktrees", "browser_fixture_isolation", "agent_fixture_isolation", "safe_maintenance"} else 300 if name in {"production_queue", "skill_calibration", "skill_libraries", "staged_production", "video_offline_correction", "video_receipts", "benchmark_contracts"} or name.startswith(("skill_version_", "skill_iteration_")) else 120
                process = subprocess.run([sys.executable, *command], cwd=workspace, timeout=timeout,
                                         stdout=output, stderr=subprocess.STDOUT)
                if is_unittest:
                    try:
                        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        pass
                    status = classify_unittest(process.returncode, receipt)
                else:
                    status = "passed" if process.returncode == 0 else "failed"
            except subprocess.TimeoutExpired:
                status = "timeout"
            except ValueError as error:
                output.write((str(error) + "\n").encode("utf-8"))
                status = "failed"
        results.append({"name": name, "status": status, "seconds": round(monotonic() - started, 2),
                        "coverage_kind": "unittest" if is_unittest else "assertion_script", "unittest": receipt})
        print(json.dumps(results[-1]), flush=True)
    report = {"paid_calls": False, "all_passed": bool(results) and all(r["status"] == "passed" for r in results), "results": results,
              "scope": "full_allowlist" if args.shard is None else "allowlist_shard", "shard": args.shard,
              "suite": args.suite, "coverage_claim": "selected_allowlist_only", "module_inventory": inventory,
              "selected_groups": [name for name, _ in selected], "allowlist_groups": len(suite)}
    (evidence / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"report={evidence / 'report.json'}", flush=True)
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
