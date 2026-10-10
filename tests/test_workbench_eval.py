"""Unpaid structural/fault-injection checks, not the paid GUI baseline.

Only grader inputs and collection faults are synthetic. The world uses real
SQLite/catalog/services; no paid model, SDK turn, or image producer is invoked.
"""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from creatoros.evaluation import workbench
from creatoros.evaluation.store import EvalStore


def agent_facts(case):
    from creatoros.context import RuntimeContext
    from creatoros.tools.definitions import tool_registry
    from creatoros.tools.host_contract import model_tool_schemas
    from creatoros.web.chat import ACCOUNT_TOOLS
    from creatoros.tools.model_projection import project_model_content
    from creatoros.tools.results import ToolResult
    names = ["compose_series"] if case == "A14" else ["queue_topics", "start_content_run"]
    arguments = {"compose_series": {"name": "每日辨词", "skill_name": "single"},
                 "queue_topics": {"series_id": "target", "topics": [{"title": "bring take fetch carry"}]},
                 "start_content_run": {"topic_id": "topic"}}
    calls = [{"id": f"call-{index}", "name": name, "arguments": json.dumps(arguments[name])} for index, name in enumerate(names)]
    responses = [{"role": "assistant", "content": None, "tool_calls": calls}, {"role": "assistant", "content": "已完成"}]
    results = [{"role": "tool", "tool_call_id": call["id"], "content": "{}"} for call in calls]
    users = [{"role": "user", "content": "真实格式的本地判分器故障夹具"}]
    runtime = RuntimeContext(project_root=workbench.PROJECT_ROOT, allowed_tools=ACCOUNT_TOOLS, creator_id="a", archive_only_reads=True)
    tools = model_tool_schemas([tool_registry[name].to_schema() for name in ACCOUNT_TOOLS], runtime)
    projected = [{**row, "content": project_model_content(call["name"], row["content"])} for call, row in zip(calls, results)]
    contexts = [{"messages": users, "tools": tools}, {"messages": users + [responses[0]] + projected, "tools": tools}]
    requests, transport, snapshots, trace = [], [], [], []
    for index, (context, response) in enumerate(zip(contexts, responses)):
        finish = "tool_calls" if index == 0 else "stop"
        events = ([{"type": "ToolCallDelta", "index": i, **call} for i, call in enumerate(calls)] if index == 0 else [{"type": "TextDelta", "content": "已完成"}]) + [{"type": "StreamEnd", "finish_reason": finish}]
        requests.append({"trace_request_id": f"r{index}", "method": "stream", "context": deepcopy(context), "events": events, "finish_reason": finish})
        wire_messages = deepcopy(context["messages"])
        for message in wire_messages:
            if message.get("tool_calls"):
                message["tool_calls"] = [{"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}} for c in message["tool_calls"]]
        wire_response = {"content": response["content"], "tool_calls": [{"index": i, "id": c["id"], "function": {"name": c["name"], "arguments": c["arguments"]}} for i, c in enumerate(response.get("tool_calls", []))]}
        transport.append({"trace_request_id": f"r{index}", "request": {"messages": wire_messages, "tools": tools, "stream": True},
                          "finish_reason": finish, "public_outputs": [{"choices": [{"index": 0, "output": wire_response, "finish_reason": finish}]}]})
        snapshot_results = [{"tool_call_id": call["id"], "name": call["name"], "raw_content": "{}", "content": projected[i]["content"], "is_error": False} for i, call in enumerate(calls)] if index == 0 else []
        snapshots.append({"request_id": f"r{index}", "context": deepcopy(context), "response": deepcopy(response), "tool_results": snapshot_results})
        trace.append({"event": "finished", "sent": True, "request_id": f"r{index}", "snapshot_available": True, "request_kind": "main"})
    return {"requests": requests, "transport": transport, "snapshots": snapshots, "trace": trace,
            "messages": users + [responses[0]] + results + [responses[1]], "archives": {},
            "execution": {"status": "idle", "entries": [{"kind": "assistant", "complete": True, "text": "已完成"}]}}


def completed_sdk(source="sdk-files/outputs/attempt/", thread="thread", turn="turn"):
    base = {"source": source + "codex_public_events.jsonl", "thread_id": thread, "turn_id": turn}
    return [{**base, "method": "turn/completed", "status": "completed"},
            {**base, "method": "capture/finished", "status": "completed", "payload": {"capture_write_failed": False}}]


def grade_facts(case):
    tables = {name: [] for name in ("series", "topics", "content_runs", "content_revisions", "content_attempts", "manual_publications")}
    if case in {"P01", "P02"}:
        tables["series"] = [{"id": "target", "creator_id": "a", "skill_name": None if case == "P02" else "single",
                            "mind_skill_id": "mind", "production_skill_id": "production"}]
    before = {"database": tables, "files": {"sources/skill/SKILL.md": "unchanged"}}
    after = deepcopy(before)
    browser = {"completed": True, "posts_after_refresh": 0, "page_errors": [], "trace_visible": True,
        "copied_reply": "已完成", "visible_reply": "已完成", "restored_reply": "已完成",
        "merge_posts": int(case == "S13"), "binding_visible": True, "delivery_clicked": True,
        "draft_visible": True, "edited_and_restored": True, "assets_loaded": 2, "bindings_opened": 2,
        "prompt_visible": True, "cover_loaded": True, "cover_sha256": "0"}
    oracle = {"creator_id": "a", "single_skill_id": "single", "source_skill_ids": ["mind", "production"],
              "source_skill_md": {"single": "1", "mind": "1", "production": "2"}, "target_series_id": "target",
              "topic_keywords": ["bring", "take", "fetch", "carry"],
              "expected_images": 3 if case == "P02" else 1, "foreign_markers": ["FOREIGN_PRIVATE"]}
    sdk = {"events": [], "image_generation_events": []}
    network = [] if case == "S13" else [{"method": "POST", "path": "/api/agent/sessions/1/turns"}]
    evidence = {"before": before, "after": after, "browser": browser, "oracle": oracle,
        "sdk": sdk, "external_attempts": [], "execution_counts": {"producer_instances": 0},
        "network": network, "network_counts_ok": True, "extraction_prefix": "extractions/jobs/",
        "final_answer": "已完成", "collection_errors": []}
    evidence.update(agent_facts(case) if case != "S13" else {"requests": [], "snapshots": [], "messages": []})
    if case == "A14":
        after["database"]["series"] = [{"id": "new", "creator_id": "a", "name": "每日辨词", "audience": "高中英语学习者", "skill_name": "single"}]
    elif case == "S13":
        evidence.update(jobs=[{"id": "job", "thread_id": "thread", "status": "ready", "saved_skills": []}], source_manifest={"verified": True})
        sdk["events"] = completed_sdk("sdk-files/extractions/jobs/job/")
    else:
        snapshot = {"creator_id": "a", "series_id": "target", "topic_id": "topic", "topic_title": "bring take fetch carry", "skill_name": None if case == "P02" else "single",
                    "composition": {"mind": {"id": "mind"}, "production": {"id": "production"}}}
        after["database"]["topics"] = [{"id": "topic", "series_id": "target", "title": "bring take fetch carry"}]
        after["database"]["content_runs"] = [{"id": "run", "topic_id": "topic", "status": "awaiting_approval", "producer_thread_id": "thread", "input_snapshot_json": snapshot}]
        after["database"]["content_revisions"] = [{"id": "revision", "content_run_id": "run", "production_input_json": snapshot}]
        after["database"]["content_attempts"] = [{"id": "attempt", "revision_id": "revision", "status": "succeeded", "producer_thread_id": "thread"}]
        sdk["events"] = completed_sdk()
        generated = [{"method": "item/completed", "item_type": "imageGeneration", "status": "completed", "thread_id": "thread", "turn_id": "turn", "source": "sdk-files/outputs/attempt/codex_public_events.jsonl", "generation_artifact": {"verified": True, "sha256": str(i)}} for i in range(oracle["expected_images"])]
        sdk["events"][0:0] = generated
        sdk["image_generation_events"] = generated
        sdk["receipts"] = [{"protocol": "creatoros-worker-v1", "source": "sdk-files/outputs/attempt/worker_receipt.json", "thread_id": "thread", "turns": [{"id": "turn", "phase": "production", "status": "completed"}]}]
        evidence["outputs"] = {"images": [{"sha256": str(i), "digest_matches": True, "width": 20, "height": 20, "image_prompt": f"完整Prompt{i}"}
            for i in range(oracle["expected_images"])], "receipt_completed": True, "evidence_complete": True,
            "sdk_prefix": "sdk-files/outputs/attempt/", "thread_id": "thread", "delivery_turn_id": "turn", "attempt_id": "attempt", "revision_id": "revision",
            "frozen_skill_files": {"mind": "1", "production": "2"} if case == "P02" else {"single": "1"}, "composition_review": {"status": "ready"}}
        browser.update(images_loaded=oracle["expected_images"], image_sha256s=[str(i) for i in range(oracle["expected_images"])], visible_prompts=[f"完整Prompt{i}" for i in range(oracle["expected_images"])])
    return evidence


class WorkbenchGraderTests(unittest.TestCase):
    def test_all_fact_chains_remain_pending_independent_quality(self):
        for case in sorted(workbench.CASES):
            with self.subTest(case=case):
                report = workbench.grade_slice(grade_facts(case), case)
                self.assertEqual(report["auto_status"], "needs_review")
                self.assertFalse(any(row["status"] == "failed" for row in report["checks"]))

    def test_audited_false_passes_are_rejected(self):
        mutations = (
            ("P01", lambda e: e["after"]["database"]["topics"][0].update(series_id="foreign-series", title="Wrong topic")),
            ("P01", lambda e: e["after"]["database"]["content_runs"][0].update(creator_id="foreign-account", topic_id="wrong-topic")),
            ("P01", lambda e: e["after"]["database"]["content_revisions"][0].update(content_run_id="foreign-run")),
            ("P01", lambda e: e["after"]["database"]["content_attempts"][0].update(revision_id="foreign-revision")),
            ("A14", lambda e: e["requests"][0].update(finish_reason="length", error_type="ConnectionError")),
            ("A14", lambda e: e.update(execution={"status": "failed", "error": "model disconnected", "entries": []})),
            ("S13", lambda e: e["sdk"]["events"].append({**e["sdk"]["events"][-1], "status": "failed", "turn_id": "new-failed-turn"})),
            ("S13", lambda e: e["jobs"][0].update(thread_id="foreign-thread")),
            ("P01", lambda e: e["sdk"].update(image_generation_events=[{"item_type": "imageGeneration", "status": "failed", "thread_id": "unrelated"}])),
            ("P01", lambda e: e["sdk"]["receipts"][0].update(thread_id="unrelated")),
            ("P01", lambda e: e["outputs"].update(delivery_turn_id="historical")),
            ("A14", lambda e: e["snapshots"][0].update(request_id="unrelated")),
            ("A14", lambda e: e["snapshots"][0]["context"].update(messages=[])),
            ("A14", lambda e: e["snapshots"][0]["tool_results"][0].update(raw_content="wrong")),
            ("S13", lambda e: e["sdk"]["events"][-1]["payload"].update(capture_write_failed=True)),
            ("P01", lambda e: e["sdk"]["events"][0].update(truncated=True)),
            ("P01", lambda e: e["browser"].update(visible_prompts=["wrong text"])),
            ("P01", lambda e: e["browser"].update(cover_sha256="wrong")),
            ("P01", lambda e: e["outputs"]["frozen_skill_files"].update(single="wrong")),
        )
        for case, mutate in mutations:
            with self.subTest(case=case, mutation=mutate):
                evidence = grade_facts(case)
                mutate(evidence)
                self.assertEqual(workbench.grade_slice(evidence, case)["auto_status"], "failed")

    def test_ui_fault_does_not_falsely_claim_boundary_failure(self):
        evidence = grade_facts("P01")
        evidence["browser"]["cover_loaded"] = False
        result = workbench.grade_slice(evidence, "P01")
        self.assertEqual(result["dimensions"]["task_success"], "failed")
        self.assertEqual(result["dimensions"]["boundary_enforced"], "passed")
        self.assertEqual(result["dimensions"]["protocol_valid"], "passed")

    def test_negative_image_receipt_prompt_reference_and_gui_facts(self):
        for field, value in (("receipt_completed", False), ("evidence_complete", False), ("images", [])):
            evidence = grade_facts("P01")
            evidence["outputs"][field] = value
            self.assertEqual(workbench.grade_slice(evidence, "P01")["auto_status"], "failed")
        for field, value in (("image_sha256s", ["wrong"]), ("images_loaded", 0), ("prompt_visible", False), ("posts_after_refresh", 1)):
            evidence = grade_facts("P01")
            evidence["browser"][field] = value
            self.assertEqual(workbench.grade_slice(evidence, "P01")["auto_status"], "failed")

    def test_three_images_require_distinct_hashes_pair_binding_and_two_frozen_sources(self):
        for fault in ("duplicate", "missing_frozen", "binding", "coordination"):
            evidence = grade_facts("P02")
            if fault == "duplicate":
                evidence["outputs"]["images"][1]["sha256"] = "0"
            elif fault == "missing_frozen":
                evidence["outputs"]["frozen_skill_files"].pop("mind")
            elif fault == "binding":
                evidence["after"]["database"]["series"][0]["mind_skill_id"] = "wrong"
            else:
                evidence["outputs"]["composition_review"]["status"] = "needs_input"
            self.assertEqual(workbench.grade_slice(evidence, "P02")["auto_status"], "failed")

    def test_foreign_record_mutation_missing_capture_and_tool_or_reply_mismatch_fail(self):
        for fault in ("mutated", "model", "snapshots", "tool", "copied", "foreign"):
            evidence = grade_facts("A14")
            if fault == "mutated":
                evidence["after"]["files"]["sources/skill/SKILL.md"] = "changed"
            elif fault == "model":
                evidence["requests"] = []
            elif fault == "snapshots":
                evidence["snapshots"] = []
            elif fault == "tool":
                evidence["messages"] = []
            elif fault == "copied":
                evidence["browser"]["copied_reply"] = "false reply"
            else:
                evidence["messages"].append({"content": "FOREIGN_PRIVATE"})
            self.assertEqual(workbench.grade_slice(evidence, "A14")["auto_status"], "failed")

    def test_source_loss_fake_sdk_finish_or_accidental_adoption_fail(self):
        for fault in ("source", "sdk", "adoption", "image"):
            evidence = grade_facts("S13")
            if fault == "source":
                evidence["source_manifest"]["verified"] = False
            elif fault == "sdk":
                evidence["sdk"]["events"] = [{"method": "capture/finished", "status": "failed"}]
            elif fault == "adoption":
                evidence["jobs"][0]["saved_skills"] = [{"id": "saved"}]
            else:
                evidence["sdk"]["image_generation_events"] = [{"item_type": "imageGeneration"}]
            self.assertEqual(workbench.grade_slice(evidence, "S13")["auto_status"], "failed")


class WorkbenchHostTests(unittest.TestCase):
    def test_view_is_get_only_and_preserves_existing_db_report_claim_and_all_files(self):
        with TemporaryDirectory() as temporary:
            host = workbench.WorkbenchEvaluation("S13", output_root=temporary)
            host.safe_finish({"completed": False, "posts_after_refresh": 0, "page_errors": []})
            host.fixture.close()
            root = Path(temporary)
            (root / "claim.json").write_text('{"run_id":"old-first-attempt"}', encoding="utf-8")
            before = {path.relative_to(root).as_posix(): workbench.digest(path) for path in root.rglob("*") if path.is_file()}
            with patch.object(workbench, "WorkbenchWorld", side_effect=AssertionError("viewer must not recreate a fixture")):
                with TestClient(workbench.readonly_view(root, host.root.name)) as client:
                    self.assertEqual(client.get("/api/eval").status_code, 200)
                    self.assertEqual(client.get(f"/api/eval/runs/{host.root.name}").status_code, 200)
                    self.assertEqual(client.get(f"/api/eval/runs/{host.root.name}/evidence?name=before.json").status_code, 200)
                    self.assertEqual(client.get("/").status_code, 200)
                    self.assertEqual(client.post(f"/api/eval/runs/{host.root.name}/review", json={}).status_code, 405)
                    self.assertEqual(client.post("/api/agent/sessions", json={}).status_code, 405)
            after = {path.relative_to(root).as_posix(): workbench.digest(path) for path in root.rglob("*") if path.is_file()}
            self.assertEqual(before, after)

    def test_four_real_worlds_are_isolated_and_create_no_external_work(self):
        with TemporaryDirectory() as temporary, patch.object(workbench.CodexSdkProducer, "from_defaults", side_effect=AssertionError("paid call")):
            root = Path(temporary)
            for case in sorted(workbench.CASES):
                world = workbench.WorkbenchWorld(root / case, case, lambda: (_ for _ in ()).throw(AssertionError("model")), root / "reports")
                try:
                    before = world.state()
                    self.assertEqual(world.execution_counts["producer_instances"], 0)
                    self.assertEqual(len(before["database"]["creators"]), 2)
                    self.assertTrue(all(path.resolve().is_relative_to(world.root) for path in world.business_roots))
                    self.assertEqual(before, world.state())
                    with self.assertRaises(ValueError):
                        world.extractions.trial("not-authorized", "id", "digest", "topic")
                    self.assertEqual(world.extractions.list(), [])
                    self.assertEqual(world.oracle()["reference_kind"], "synthetic_palette" if case in {"S13", "P02"} else "user_supplied_reference_copy")
                finally:
                    world.close()

    def test_unpaid_incomplete_reports_are_schema_valid_original_ui_readable(self):
        with TemporaryDirectory() as temporary:
            for case in sorted(workbench.CASES):
                host = workbench.WorkbenchEvaluation(case, output_root=temporary)
                try:
                    with TestClient(host.app) as client:
                        self.assertEqual(client.get("/").status_code, 200)
                        self.assertEqual(client.get("/api/eval").status_code, 200)
                        self.assertEqual(client.get("/__live_eval__/scenario").json()["case_id"], case)
                        report = host.safe_finish({"completed": False, "posts_after_refresh": 0, "page_errors": []})
                        self.assertEqual(report["auto_status"], "failed")
                        store = EvalStore(temporary, cases_path=workbench.CASES_PATH)
                        self.assertEqual(store.detail(host.root.name)["auto_status"], "failed")
                        self.assertEqual(host.fixture.execution_counts["producer_instances"], 0)
                finally:
                    host.fixture.close()

    def test_collection_failure_is_terminal_not_false_pass_and_view_cannot_upgrade_failure(self):
        with TemporaryDirectory() as temporary:
            host = workbench.WorkbenchEvaluation("A14", output_root=temporary)
            try:
                with patch.object(host.fixture, "state", side_effect=OSError("snapshot denied")):
                    report = host.safe_finish({"completed": True, "page_errors": [], "posts_after_refresh": 0})
                self.assertTrue(host.finished)
                self.assertEqual(report["execution_status"], "failed")
                self.assertEqual(EvalStore(temporary, cases_path=workbench.CASES_PATH).detail(host.root.name)["auto_status"], "failed")
                with TestClient(host.app) as client:
                    first = client.post("/__live_eval__/view", json={"run_id": host.root.name, "completed": False, "evidence_loaded": False}).json()
                    second = client.post("/__live_eval__/view", json={"run_id": host.root.name, "completed": True, "evidence_loaded": True}).json()
                    self.assertEqual(first, second)
                    self.assertEqual(second["auto_status"], "failed")
            finally:
                host.fixture.close()

    def test_exclusive_claim_and_manifest_drift_fail_before_external_execution(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "snapshot.json"
            manifest.write_text(json.dumps({"source_hashes": {"one": "same"}, "execution_keys": sorted(workbench.CASES)}), encoding="utf-8")
            with patch.object(workbench, "manifest_path", return_value=manifest), patch.object(workbench, "frozen_hashes", return_value={"one": "same"}):
                workbench.claim(root, "test", "snapshot", "P01", "first")
                with self.assertRaises(FileExistsError):
                    workbench.claim(root, "test", "snapshot", "P01", "second")
            with patch.object(workbench, "manifest_path", return_value=manifest), patch.object(workbench, "frozen_hashes", return_value={"one": "changed"}):
                with self.assertRaises(ValueError):
                    workbench.claim(root, "test", "snapshot", "P02", "blocked")
            self.assertFalse((root / "batches/test/baseline/P02.json").exists())


if __name__ == "__main__":
    unittest.main()
