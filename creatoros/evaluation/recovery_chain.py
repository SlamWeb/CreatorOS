"""P05 observations and declared post-delivery fault; never fabricate SDK output."""
from contextlib import asynccontextmanager
from pathlib import Path

from .run import now, write_json


class RecoveryProbe:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.injected = False
        self.calls = []
        self.fault = None

    def instrument(self, producer):
        original_produce = producer.produce_to

        def observed(**kwargs):
            from creatoros.integrations import native_production as native
            from creatoros.integrations.codex import CodexProducerError
            from creatoros.integrations.worker_protocol import completed_delivery_turn
            from creatoros.runs.artifacts import validate_artifact
            original_execute, original_client = native.execute, native._production_client
            phase = "recovery" if self.injected else "initial"
            directory = Path(kwargs["directory"]).resolve()
            if not directory.is_relative_to(self.root):
                raise ValueError("恢复题目录越界。")

            def record(operation):
                self.calls.append({"at": now(), "phase": phase, "operation": operation,
                                   "directory": directory.relative_to(self.root).as_posix()})
                write_json(self.root.parent / "recovery_calls.json", self.calls)

            async def execute(*args, **kw):
                record("native_execute")
                return await original_execute(*args, **kw)

            @asynccontextmanager
            async def client(*args, **kw):
                record("sdk_client_open")
                async with original_client(*args, **kw) as real_client:
                    yield real_client

            # This isolated world has one serialized producer. Both wrappers
            # delegate unchanged to the real implementation, then are restored.
            native.execute, native._production_client = execute, client
            try:
                produced = original_produce(**kwargs)
            finally:
                native.execute, native._production_client = original_execute, original_client
            if not self.injected:
                cp = native.load_checkpoint(directory)
                if cp is None or not cp.turn_completed or not completed_delivery_turn(directory, cp.thread_id):
                    raise ValueError("真实完整交付不足，不能注入P05完成后故障。")
                checked = validate_artifact(directory, production_protocol="native-v1")
                from .workbench import digest
                self.fault = {"actor": "controller", "point": "post_sdk_completed_final_ingest",
                    "kind": "controlled_acceptance_failure", "at": now(), "count": 1,
                    "directory": directory.relative_to(self.root).as_posix(), "thread_id": cp.thread_id,
                    "checkpoint_sha256": digest(directory / "native_checkpoint.json"),
                    "receipt_sha256": digest(directory / "worker_receipt.json"),
                    "images": [p.model_dump(mode="json") for p in checked.images],
                    "note": "真实交付已返回，宿主后处理拒收一次；未改文件、SDK结果或恢复逻辑。"}
                write_json(self.root.parent / "recovery_fault.json", self.fault)
                self.injected = True
                raise CodexProducerError("P05声明故障：SDK已完成，宿主交付后处理失败一次；原图保留。",
                                         error_type="native_delivery_failed")
            return produced

        producer.produce_to = observed
        return producer


def recovery_checks(evidence):
    """Only observed facts; controlled grader inputs in tests are not model scores."""
    from .workbench import _rows_preserved, sdk_terminal, _json
    checks = []
    def check(identifier, label, good, files):
        checks.append({"id": identifier, "label": label, "status": "passed" if good else "failed",
                       "detail": label, "evidence": files})
    before, after = evidence["before"], evidence["after"]
    stages = evidence.get("recovery_stages", [])
    browser, oracle = evidence["browser"], evidence["oracle"]
    allowed = {"topics", "pending_operations", "operation_events", "write_receipts", "content_runs",
               "content_revisions", "content_attempts", "content_run_events"}
    check("bounded_database", "原记录与未授权表保持不变", _rows_preserved(before["database"], after["database"], allowed), ["before.json", "after.json"])
    check("bounded_files", "既有Skill/资产不改，新增文件仅本任务outputs", all(after["files"].get(k) == v for k,v in before["files"].items())
          and all(k.startswith("outputs/") for k in after["files"] if k not in before["files"]), ["before.json", "after.json"])
    check("browser_chain", "原网页生产/恢复/读图/刷新且零自动重提", browser.get("completed") is True
          and browser.get("posts_after_refresh") == 0 and not browser.get("page_errors"), ["browser.json"])
    no_agent = not any(evidence.get(k) for k in ("requests", "messages", "snapshots", "external_attempts"))
    check("no_agent_or_external", "本题直接网页执行，无DS/场景外工具/发布", no_agent
          and evidence.get("network_counts_ok") and not after["database"]["manual_publications"], ["requests.json", "network.json", "after.json"])
    facts = dict.fromkeys(("declared_fault", "same_run_revision", "original_attempt_preserved", "production_terminal",
                           "real_image", "offline_no_sdk", "original_files_preserved", "artifact_evidence", "gui_post_count"), False)
    try:
        if [s["name"] for s in stages] != ["failed", "restored"]:
            raise ValueError("恢复阶段证据不完整")
        failed, restored = stages
        first, final = failed["state"]["database"], restored["state"]["database"]
        fault, output, initial = evidence["recovery_fault"], evidence["outputs"], failed["outputs"]
        old = first["content_attempts"][0]
        attempts = sorted(final["content_attempts"], key=lambda a: a["attempt_number"])
        run, rev = final["content_runs"][0], final["content_revisions"][0]
        topic = next(t for t in final["topics"] if t["id"] == run["topic_id"])
        target = next(s for s in final["series"] if s["id"] == oracle["target_series_id"])
        original_prefix = initial["sdk_prefix"]
        receipts = [r for r in failed["sdk"]["receipts"] if r["source"] == original_prefix + "worker_receipt.json"]
        receipt = receipts[0]
        facts["declared_fault"] = fault["actor"] == "controller" and fault["count"] == 1 and fault["point"] == "post_sdk_completed_final_ingest" and old["status"] == "failed" and first["content_runs"][0]["status"] == "failed" and old["error_type"] == "native_delivery_failed" and first["content_runs"][0]["retryable"] == 1
        facts["same_run_revision"] = len(first["content_runs"]) == len(first["content_revisions"]) == len(first["content_attempts"]) == 1 and len(final["content_runs"]) == len(final["content_revisions"]) == 1 and len(attempts) == 2 and [a["attempt_number"] for a in attempts] == [1,2] and attempts[0] == old and all(a["revision_id"] == rev["id"] for a in attempts) and rev["content_run_id"] == run["id"] == first["content_runs"][0]["id"] and rev["id"] == first["content_revisions"][0]["id"] and run["status"] == "awaiting_approval" and attempts[-1]["status"] == "succeeded" and not attempts[-1].get("error_type") and topic["series_id"] == target["id"] and target["creator_id"] == oracle["creator_id"] and topic["status"] == "ready" and not run.get("approved_revision_id") and not rev.get("approved_at") and all(word in topic["title"].lower() for word in oracle["topic_keywords"])
        facts["same_run_revision"] &= all(_json(v)["topic_id"] == topic["id"] and _json(v)["creator_id"] == oracle["creator_id"] and _json(v)["series_id"] == target["id"] for v in (run["input_snapshot_json"], rev["production_input_json"]))
        facts["original_attempt_preserved"] = attempts[0] == old and final == after["database"] and initial["attempt_id"] == old["id"] and output["attempt_id"] == attempts[-1]["id"] and initial["revision_id"] == output["revision_id"] == rev["id"]
        facts["production_terminal"] = sdk_terminal(failed["sdk"], source_prefix=original_prefix, thread_id=fault["thread_id"], turn_id=initial["delivery_turn_id"]) and receipt["protocol"] == "creatoros-worker-v1" and receipt["thread_id"] == fault["thread_id"] and receipt["turns"][-1]["id"] == initial["delivery_turn_id"] and receipt["turns"][-1]["status"] == "completed" and receipt["turns"][-1]["phase"] in {"production", "delivery_repair"} and initial["receipt_completed"] is True and run["producer_thread_id"] == attempts[-1]["producer_thread_id"] == output["thread_id"] == fault["thread_id"]
        generated = [e for e in failed["sdk"]["image_generation_events"] if e.get("method") == "item/completed" and e.get("status") == "completed" and e.get("thread_id") == fault["thread_id"] and str(e.get("source", "")).startswith(original_prefix) and e.get("turn_id") in {t["id"] for t in receipt["turns"] if t["status"] == "completed" and t["phase"] in {"production", "delivery_repair"}} and not e.get("truncated")]
        known = {e.get("generation_artifact", {}).get("sha256") for e in generated if e.get("generation_artifact", {}).get("verified")}
        hashes = [p["sha256"] for p in initial["images"]]
        facts["real_image"] = len(hashes) == 1 and hashes == [p["sha256"] for p in output["images"]] == browser.get("image_sha256s") == [p["sha256"] for p in fault["images"]] and all(p["digest_matches"] and p["width"] > 0 and p["height"] > 0 for p in output["images"]) and set(hashes) <= known and browser.get("images_loaded") == 1
        facts["offline_no_sdk"] = {c["operation"] for c in evidence["recovery_calls"] if c["phase"] == "initial"} == {"native_execute", "sdk_client_open"} and not any(c["phase"] == "recovery" for c in evidence["recovery_calls"]) and evidence["execution_counts"]["producer_instances"] == 2 and failed["calls"] == restored["calls"] == evidence["recovery_calls"] and failed["sdk"]["events"] == restored["sdk"]["events"] == evidence["sdk"]["events"] and failed["sdk"]["image_generation_events"] == restored["sdk"]["image_generation_events"]
        original_dir = fault["directory"] + "/"
        facts["original_files_preserved"] = {k:v for k,v in after["files"].items() if k.startswith(original_dir)} == {k:v for k,v in failed["state"]["files"].items() if k.startswith(original_dir)} and original_prefix == "sdk-files/" + original_dir and failed["state"]["files"][original_dir + "worker_receipt.json"] == fault["receipt_sha256"] and failed["state"]["files"][original_dir + "native_checkpoint.json"] == fault["checkpoint_sha256"]
        facts["artifact_evidence"] = output["evidence_complete"] and output["frozen_skill_files"] == {"single": oracle["source_skill_md"][oracle["single_skill_id"]]} and browser.get("visible_prompts") == [p["image_prompt"] for p in output["images"]] and browser.get("cover_sha256") == hashes[0] and browser.get("cover_loaded") is True and browser.get("failed_visible") is True and browser.get("resume_clicked") is True
        writes = evidence["network"]
        execute_path = "/api/runs/" + run["id"] + "/execute"
        executions = [w for w in writes if w["path"] == execute_path and w["method"] == "POST"]
        queues = [w for w in writes if w["path"] == "/api/series/" + target["id"] + "/queue" and w["method"] == "POST"]
        creates = [w for w in writes if w["path"] == "/api/runs" and w["method"] == "POST"]
        facts["gui_post_count"] = len(executions) == 2 and all(w.get("status") == 202 for w in executions) and len(creates) == len(queues) == 1 and creates[0].get("status") == queues[0].get("status") == 201 and len(writes) == 4 and browser.get("resume_request", {}).get("body", {}).get("expected_version") == first["content_runs"][0]["version"]
    except (KeyError, IndexError, StopIteration, ValueError, TypeError, AttributeError):
        pass
    labels = {"declared_fault": "真实完成后宿主故障恰一次，保留失败而非远端失败", "same_run_revision": "仅同Run/Revision的失败与恢复两Attempt，最终待批准", "original_attempt_preserved": "旧失败Attempt完整保留且最终阶段与DB一致", "production_terminal": "原SDK最后完整turn/receipt与恢复来源thread对应", "real_image": "原SDK新图→原Attempt→恢复Attempt→网页SHA一致", "offline_no_sdk": "恢复真实SDK入口/native execute/新生图为0", "original_files_preserved": "原失败Attempt全文件及完成回执摘要不变", "artifact_evidence": "原网页读到冻结Skill、Prompt、恢复终态及同图封面", "gui_post_count": "仅原网页一次入队/建Run/生产/显式恢复，CAS匹配"}
    for key, good in facts.items():
        check(key, labels[key], bool(good), ["recovery_stages.json", "recovery_fault.json", "recovery_calls.json", "sdk.json", "outputs.json", "network.json", "browser.json"])
    check("evidence_complete", "采集无缺失", not evidence.get("collection_errors"), ["collection_errors.json"])
    groups = {"boundary_enforced": {"bounded_database", "bounded_files", "no_agent_or_external"}, "state_consistent": {"same_run_revision", "original_attempt_preserved", "real_image", "original_files_preserved"}, "protocol_valid": {"production_terminal", "offline_no_sdk", "gui_post_count", "evidence_complete"}}
    failed = any(c["status"] == "failed" for c in checks)
    dimensions = {k: "failed" if any(c["status"] == "failed" and c["id"] in ids for c in checks) else "passed" for k,ids in groups.items()}
    dimensions["task_success"] = "failed" if failed else "needs_review"
    checks.append({"id": "independent_quality", "label": "图片及状态解释独立阅读", "status": "needs_review", "detail": "内容质量和参考参数另评，不代用户签署。", "evidence": ["outputs.json", "browser.json"]})
    return {"checks": checks, "dimensions": dimensions, "auto_status": "failed" if failed else "needs_review"}
