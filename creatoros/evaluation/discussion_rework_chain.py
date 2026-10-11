"""P04/P06 real GUI chain observations and deterministic evidence grading."""
from __future__ import annotations

import hashlib
import json
import shutil
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from creatoros.content import SocialContentPack
from creatoros.runs.artifacts import validate_artifact
from creatoros.storage import (
    ContentAttempt, ContentAttemptStatus, ContentRevision, ContentRunEvent,
    ContentRunEventType, ContentRunStatus, Topic, TopicSource, TopicStatus,
)

P05_RUN = "5bfb85f661504de0bfed17ce88234ee8"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_bundle():
    """Return verified paths and immutable provenance for the ignored P05 source."""
    from .workbench import OUTPUT_ROOT

    report_root = OUTPUT_ROOT / P05_RUN
    report = json.loads((report_root / "report.json").read_text(encoding="utf-8"))
    outputs = json.loads((report_root / "outputs.json").read_text(encoding="utf-8"))
    if report.get("case_id") != "P05" or report.get("execution_status") != "completed":
        raise ValueError("P06/P04 需要已完成的真实 P05 来源；当前来源不可用。")
    if len(outputs.get("images", [])) != 1 or not outputs.get("images")[0].get("digest_matches"):
        raise ValueError("P05 来源没有一张可校验的最终图片。")
    fixture = report_root / "fixture"
    prior_state = json.loads((report_root / "after.json").read_text(encoding="utf-8"))
    prior_revision = next((row for row in prior_state["database"]["content_revisions"]
                           if row["id"] == outputs["revision_id"]), None)
    if prior_revision is None:
        raise ValueError("P05真实Run输入快照无法从已保存数据库快照定位。")
    image = fixture / outputs["images"][0]["path"]
    attempt = image.parents[1]
    skill = attempt / "skills" / "single"
    snapshot_path = report_root / "after.json"
    if not image.is_file() or not (skill / "SKILL.md").is_file() or not snapshot_path.is_file():
        raise ValueError("P05 图片、冻结 Skill 或输入快照缺失。")
    if _sha(image) != outputs["images"][0]["sha256"]:
        raise ValueError("P05 图片摘要与来源报告不一致。")
    bundle = {
        "run_id": P05_RUN,
        "report_path": str(report_root / "report.json"),
        "image_path": str(image),
        "image_sha256": _sha(image),
        "skill_path": str(skill),
        "skill_sha256": _sha(skill / "SKILL.md"),
        "input_snapshot_path": str(snapshot_path),
        "input_snapshot": (json.loads(prior_revision["production_input_json"])
                           if isinstance(prior_revision["production_input_json"], str)
                           else prior_revision["production_input_json"]),
        "production_thread_id": outputs["thread_id"],
        "source_artifact_sha256": outputs["images"][0]["sha256"],
        "attempt_path": str(attempt),
        "production_request_path": str(attempt / "production_request.txt"),
        "checkpoint_path": str(attempt / "native_checkpoint.json"),
    }
    bundle["input_snapshot_sha256"] = hashlib.sha256(json.dumps(bundle["input_snapshot"], sort_keys=True,
        ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    return bundle


def seed_existing_delivery(world, content):
    """Seed one reused P05 artifact into this isolated world's real Run tables."""
    source = source_bundle()
    content.add_topic(topic_id=f"topic-seed-{world.case_id.lower()}", series_id=world.series_id,
                      title="bring/take/fetch/carry 四格双语辨词", source=TopicSource.MANUAL)
    run = world.runs.create(f"topic-seed-{world.case_id.lower()}", idempotency_key=f"seed-{world.case_id}")
    with world.database.session() as session:
        saved = session.get(type(run), run.id)
        snapshot = dict(saved.input_snapshot_json)
        source_input = dict(source["input_snapshot"])
        for key in ("creator_id", "series_id", "topic_id", "series_name", "topic_title"):
            source_input[key] = snapshot[key]
        source_input["skill_name"] = world.single_skill["id"]
        source_input["skill_path"] = world.single_skill["local_path"]
        from creatoros.integrations.producer_skills import _digest as skill_digest
        source_input["skill_digest"] = skill_digest(Path(world.single_skill["local_path"]))
        source_input["production_protocol"] = "native-v1"
        snapshot = source_input
        saved.input_snapshot_json = snapshot
        saved.status = ContentRunStatus.AWAITING_APPROVAL
        # The reused artifact is already validated; mirror the real validator.
        session.get(Topic, saved.topic_id).status = TopicStatus.READY
        saved.producer_thread_id = source["production_thread_id"]
        saved.version += 1
        revision = session.query(ContentRevision).filter_by(content_run_id=run.id).one()
        revision.production_input_json = snapshot
        revision_root = world.runs.output_root / world.creator_a / world.series_id / run.id / "revision-001"
        target = revision_root / "attempt-001"
        (target / "images").mkdir(parents=True)
        (target / "skills").mkdir()
        (target / "partial-images").mkdir()
        shutil.copyfile(source["image_path"], target / "images" / "01.png")
        shutil.copytree(source["skill_path"], target / "skills" / "single")
        shutil.copyfile(source["production_request_path"], target / "production_request.txt")
        checkpoint = json.loads(Path(source["checkpoint_path"]).read_text(encoding="utf-8"))
        for page in checkpoint["pages"]:
            source_page = Path(source["attempt_path"]) / "partial-images" / Path(page["image_path"]).name
            dest_page = target / "partial-images" / source_page.name
            shutil.copyfile(source_page, dest_page)
            page["image_path"] = str(dest_page.resolve())
        (target / "native_checkpoint.json").write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")
        original_pack = SocialContentPack.load(Path(source["image_path"]).parents[1])
        pack = original_pack.model_copy(update={"pack_id": run.id, "creator_id": world.creator_a,
            "series_id": world.series_id, "topic_id": saved.topic_id})
        (target / "social_content_pack.json").write_text(pack.model_dump_json(indent=2), encoding="utf-8")
        checked = validate_artifact(target, production_protocol="native-v1")
        revision.artifact_directory = str(target)
        revision.manifest_path = str(target / "social_content_pack.json")
        revision.artifact_digest = checked.artifact_digest
        revision.validation_json = checked.model_dump(mode="json")
        revision.validated_at = datetime.now(timezone.utc)
        revision_root.mkdir(parents=True, exist_ok=True)
        session.add(ContentAttempt(id=f"seed-attempt-{world.case_id.lower()}", revision_id=revision.id,
            attempt_number=1, status=ContentAttemptStatus.SUCCEEDED,
            producer_thread_id=source["production_thread_id"], output_directory=str(target),
            usage_json={"scope": "reused_prior_p05_evidence", "new_calls": 0},
            completed_at=datetime.now(timezone.utc), duration_ms=0))
        session.add(ContentRunEvent(content_run_id=run.id, revision_id=revision.id,
            event_type=ContentRunEventType.VALIDATED, from_status="producing",
            to_status=ContentRunStatus.AWAITING_APPROVAL.value,
            payload_json={"seed": "verified P05 artifact copy", "source_sha256": source["image_sha256"]}))
    world.reused_source = {**source, "copy_path": str(target / "images" / "01.png"),
                           "copy_sha256": _sha(target / "images" / "01.png"),
                           "generated_in_this_case": False, "seed_run_id": run.id}
    world.seed_run_id = run.id
    world.seed_revision_id = revision.id
    world.seed_artifact_digest = checked.artifact_digest
    return world.reused_source


class _ObservedClient:
    def __init__(self, client, calls):
        self._client, self._calls = client, calls

    def __getattr__(self, name):
        return getattr(self._client, name)

    async def thread_fork(self, *args, **kwargs):
        requested = kwargs.get("thread_id", args[0] if args else None)
        self._calls.append({"operation": "thread_fork", "phase": "started", "requested_thread_id": requested})
        thread = await self._client.thread_fork(*args, **kwargs)
        self._calls.append({"operation": "thread_fork", "phase": "returned", "requested_thread_id": requested,
                            "thread_id": thread.id})
        return _ObservedThread(thread, self._calls)

    async def thread_resume(self, *args, **kwargs):
        requested = kwargs.get("thread_id", args[0] if args else None)
        self._calls.append({"operation": "thread_resume", "phase": "started", "requested_thread_id": requested})
        thread = await self._client.thread_resume(*args, **kwargs)
        self._calls.append({"operation": "thread_resume", "phase": "returned", "requested_thread_id": requested,
                            "thread_id": thread.id})
        return _ObservedThread(thread, self._calls)

    async def thread_start(self, *args, **kwargs):
        self._calls.append({"operation": "thread_start", "phase": "started"})
        thread = await self._client.thread_start(*args, **kwargs)
        self._calls.append({"operation": "thread_start", "phase": "returned", "thread_id": thread.id})
        return _ObservedThread(thread, self._calls)


class _ObservedThread:
    """Record exact turn input kinds and image file hashes, then forward unchanged."""
    def __init__(self, thread, calls):
        self._thread, self._calls = thread, calls

    def __getattr__(self, name):
        return getattr(self._thread, name)

    async def turn(self, inputs, *args, **kwargs):
        import openai_codex
        observed = []
        for item in inputs:
            row = {"type": type(item).__name__, "module": type(item).__module__}
            if isinstance(item, openai_codex.LocalImageInput):
                path = Path(item.path)
                row.update(path=str(path), sha256=_sha(path) if path.is_file() and not path.is_symlink() else None)
            observed.append(row)
        self._calls.append({"operation": "thread_turn", "phase": "started", "thread_id": self.id,
                            "inputs": observed, "model": kwargs.get("model"), "effort": kwargs.get("effort")})
        result = await self._thread.turn(inputs, *args, **kwargs)
        self._calls.append({"operation": "thread_turn", "phase": "returned", "thread_id": self.id})
        return result


class _DiscussionProbe:
    """Observe SDK method selection and returned thread ID around the real reviewer."""
    def __init__(self, reviewer):
        self.reviewer, self.calls, self.lock = reviewer, [], RLock()

    def review(self, *args, **kwargs):
        import creatoros.integrations.content_discussion as discussion_module
        original_factory = discussion_module._production_client
        calls = self.calls

        @asynccontextmanager
        async def observed_factory(*factory_args, **factory_kwargs):
            async with original_factory(*factory_args, **factory_kwargs) as client:
                yield _ObservedClient(client, calls)

        with self.lock:
            discussion_module._production_client = observed_factory
            try:
                return self.reviewer.review(*args, **kwargs)
            finally:
                discussion_module._production_client = original_factory


def install_discussion_probe(service):
    probe = _DiscussionProbe(service.reviewer)
    service.reviewer = probe
    return probe


def discussion_rework_checks(evidence, case_id):
    """Only grade captured service/SDK/browser facts; never score model quality."""
    checks = []

    def check(identifier, label, good, files, detail=None):
        checks.append({"id": identifier, "label": label,
            "status": "passed" if bool(good) else "failed", "detail": detail or label,
            "evidence": files})

    oracle, browser = evidence.get("oracle", {}), evidence.get("browser", {})
    before, after = evidence.get("before", {}).get("database", {}), evidence.get("after", {}).get("database", {})
    db_same = all(before.get(name) == after.get(name) for name in before) and set(before) == set(after)
    if case_id == "P04":
        allowed = {"content_runs", "content_revisions", "content_attempts", "content_run_events", "topics"}
        expected_counts = oracle.get("expected_after", {}).get("row_counts", {})
        count_ok = set(expected_counts) == set(after) and all(len(after[key]) == value for key, value in expected_counts.items())
        unaffected = all(before[name] == after[name] for name in before if name not in allowed)
        row = lambda item: json.dumps(item, sort_keys=True, default=str)
        old_revisions = before.get("content_revisions", [])
        old_attempts = before.get("content_attempts", [])
        old_events = before.get("content_run_events", [])
        old_run_ids = {item["id"] for item in before.get("content_runs", [])}
        new_run_ids = {item["id"] for item in after.get("content_runs", [])}
        old_topic_ids = {item["id"] for item in before.get("topics", [])}
        new_topic_ids = {item["id"] for item in after.get("topics", [])}
        expected_topic = oracle.get("expected_after", {}).get("topic_update", {})
        seed_before = next((item for item in before.get("content_runs", [])
                            if item["id"] == oracle.get("seed_run_id")), {})
        target_topic = seed_before.get("topic_id")
        topics_by_id = {item["id"]: item for item in after.get("topics", [])}
        topics_ok = bool(target_topic) and expected_topic == {
            "id": target_topic, "status": "ready", "mutable_columns": ["status", "updated_at"]}
        for old_topic in before.get("topics", []):
            actual_topic = topics_by_id.get(old_topic["id"], {})
            expected_row = dict(old_topic)
            if old_topic["id"] == target_topic:
                expected_row["status"] = "ready"
                if "updated_at" in old_topic:
                    expected_row["updated_at"] = actual_topic.get("updated_at")
                    topics_ok &= bool(expected_row["updated_at"])
            topics_ok &= expected_row == actual_topic
        old_ids_preserved = (old_run_ids == new_run_ids and old_topic_ids == new_topic_ids
             and topics_ok
             and {row(x) for x in old_revisions} <= {row(x) for x in after.get("content_revisions", [])}
             and {row(x) for x in old_attempts} <= {row(x) for x in after.get("content_attempts", [])}
             and {row(x) for x in old_events} <= {row(x) for x in after.get("content_run_events", [])})
        seeded = next((r for r in after.get("content_runs", []) if r["id"] == oracle.get("seed_run_id")), {})
        state_ok = (seeded.get("status") == "awaiting_approval" and seeded.get("active_revision_number") == 2
                    and seeded.get("topic_id") == target_topic
                    and not seeded.get("approved_revision_id") and not after.get("manual_publications"))
        old_nonseed_runs = [item for item in before.get("content_runs", []) if item["id"] != oracle.get("seed_run_id")]
        final_runs_by_id = {item["id"]: item for item in after.get("content_runs", [])}
        nonseed_runs_unchanged = all(final_runs_by_id.get(item["id"]) == item for item in old_nonseed_runs)
        old_revision_rows, old_attempt_rows, old_event_rows = (
            {row(item) for item in old_revisions}, {row(item) for item in old_attempts}, {row(item) for item in old_events})
        new_revisions = [item for item in after.get("content_revisions", []) if row(item) not in old_revision_rows]
        new_attempts = [item for item in after.get("content_attempts", []) if row(item) not in old_attempt_rows]
        new_events = [item for item in after.get("content_run_events", []) if row(item) not in old_event_rows]
        only_expected_rows = (len(new_revisions) == 1 and new_revisions[0].get("content_run_id") == oracle.get("seed_run_id")
            and new_revisions[0].get("revision_number") == 2
            and len(new_attempts) == 1 and new_attempts[0].get("revision_id") == new_revisions[0].get("id")
            and new_attempts[0].get("attempt_number") == 1
            and len(new_events) == expected_counts.get("content_run_events", 0) - len(old_events)
            and all(item.get("content_run_id") == oracle.get("seed_run_id") for item in new_events))
        db_bounded = count_ok and unaffected and old_ids_preserved and nonseed_runs_unchanged and only_expected_rows and state_ok
    else:
        db_bounded = db_same
    check("database_scope", "无授权外表/旧行改动，返工仅新增规定行；讨论数据库全表不变", db_bounded,
          ["before.json", "after.json"])
    check("browser_chain", "原 SPA 必需步骤、刷新与零重提均有记录",
          browser.get("completed") is True and not browser.get("page_errors")
          and browser.get("posts_after_refresh") == 0,
          ["browser.json", "network.json"])
    counts = evidence.get("execution_counts", {})
    source = evidence.get("reused_source", {})
    sha = source.get("image_sha256")
    valid_hash = lambda value: isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)
    copied = (valid_hash(sha)
              and source.get("source_artifact_sha256") == sha == source.get("copy_sha256")
              and bool(source.get("image_path")) and bool(source.get("copy_path"))
              and valid_hash(source.get("skill_sha256")) and valid_hash(source.get("input_snapshot_sha256"))
              and source.get("generated_in_this_case") is False)
    check("source_copy_verified", "P05既有真实图/快照隔离复制且SHA一致，未冒充新生成",
          copied, ["reused_source.json"])

    if case_id == "P04":
        stages = evidence.get("rework_stages", [])
        before_execution = next((row for row in stages if row.get("name") == "saved_before_execute"), {})
        run_after = next((row for row in stages if row.get("name") == "completed"), {})
        checks_data = evidence.get("outputs", {})
        valid_pause = (before_execution.get("run_status") == "queued"
                       and before_execution.get("new_attempt_count") == 0
                       and before_execution.get("sdk_client_opens") == 0
                       and before_execution.get("image_generation_count") == 0)
        check("save_without_execution", "先保存返工只建queued Revision，未启动模型/生图", valid_pause,
              ["rework_stages.json", "execution_counts.json", "network.json"])
        revisions = after.get("content_revisions", [])
        attempts = after.get("content_attempts", [])
        runs = after.get("content_runs", [])
        exact = (len(runs) == len(before.get("content_runs", [])) == 1
                 and len(revisions) == len(before.get("content_revisions", [])) + 1
                 and len(attempts) == len(before.get("content_attempts", [])) + 1
                 and len(evidence.get("rework_stages", [])) == 2
                 and run_after.get("explicit_execute_clicked") is True)
        check("new_revision_attempt", "显式点击后同Run新增一个返工Revision/Attempt", exact,
              ["before.json", "after.json", "rework_stages.json"])
        old = source.get("image_sha256")
        images = checks_data.get("images", [])
        new_events = evidence.get("sdk", {}).get("image_generation_events", [])
        generated = [row for row in new_events if row.get("status") == "completed"
                     and row.get("item_type") == "imageGeneration"
                     and str(row.get("source", "")).startswith(checks_data.get("sdk_prefix", "\0"))
                     and row.get("generation_artifact", {}).get("verified") is True
                     and row.get("generation_artifact", {}).get("saved_path")
                     and row.get("thread_id") == checks_data.get("thread_id")
                     and row.get("turn_id") == checks_data.get("delivery_turn_id")]
        output_hashes = {image.get("sha256") for image in images}
        generated_hashes = {row.get("generation_artifact", {}).get("sha256") for row in generated}
        thread_id = checks_data.get("thread_id")
        sdk_prefix = checks_data.get("sdk_prefix", "")
        from .workbench import sdk_terminal
        terminal = bool(sdk_prefix) and sdk_terminal(evidence.get("sdk", {}),
            source_prefix=sdk_prefix, thread_id=thread_id, turn_id=checks_data.get("delivery_turn_id"))
        receipts = [row for row in evidence.get("sdk", {}).get("receipts", [])
                   if row.get("source") == sdk_prefix + "worker_receipt.json"]
        receipt_turn = receipts[0].get("turns", [])[-1] if len(receipts) == 1 and receipts[0].get("turns") else {}
        receipt_ok = (len(receipts) == 1 and receipts[0].get("source") == sdk_prefix + "worker_receipt.json"
                      and receipts[0].get("thread_id") == thread_id
                      and receipt_turn.get("status") == "completed"
                      and receipt_turn.get("id") == checks_data.get("delivery_turn_id")
                      and receipt_turn.get("phase") in {"production", "delivery_repair"})
        valid_image = (len(images) == 1 and images[0].get("digest_matches") is True
                       and images[0].get("sha256") != old and images[0].get("width", 0) > 0
                       and bool(output_hashes & generated_hashes) and terminal and receipt_ok
                       and thread_id != oracle.get("production_thread_id")
                       and checks_data.get("evidence_complete") is True)
        check("new_real_image", "新版由本轮真实 Codex 生图并可解码，旧图另存不变", valid_image,
              ["outputs.json", "sdk.json", "reused_source.json"])
        before_db, after_db = evidence.get("before", {}).get("database", {}), evidence.get("after", {}).get("database", {})
        authorized = {"content_runs", "content_revisions", "content_attempts", "content_run_events", "topics"}
        tables_preserved = set(before_db) == set(after_db) and all(
            before_db[name] == after_db[name] for name in before_db if name not in authorized)
        base_revisions = [r for r in before_db.get("content_revisions", []) if r["content_run_id"] == oracle.get("seed_run_id")]
        final_revisions = [r for r in after_db.get("content_revisions", []) if r["content_run_id"] == oracle.get("seed_run_id")]
        base_attempts = [r for r in before_db.get("content_attempts", []) if r["revision_id"] in {x["id"] for x in base_revisions}]
        final_attempts = [r for r in after_db.get("content_attempts", []) if r["revision_id"] in {x["id"] for x in final_revisions}]
        boundary = (tables_preserved and len(base_revisions) == 1 and base_revisions[0] in final_revisions
                    and len(base_attempts) == 1 and base_attempts[0] in final_attempts
                    and run_after.get("old_revision_unchanged") is True
                    and run_after.get("unapproved") is True
                    and runs[0].get("status") == "awaiting_approval") if runs else False
        check("old_revision_preserved", "旧Revision/文件摘要不变，新版未批准未发布", boundary,
              ["rework_stages.json", "before.json", "after.json"])
    else:
        records = evidence.get("discussion_records", [])
        sdk = evidence.get("sdk", {})
        usage = evidence.get("discussion_usage", [])
        try:
            from .workbench import agent_protocol
            protocol_facts, calls = agent_protocol(evidence)
        except (KeyError, TypeError, ValueError):
            protocol_facts, calls = {}, []
        names = [call.get("name") for call in calls]
        check("deepseek_chat_tools", "真实账号聊天先查Run，再两次调用原讨论工具；已有结果可从上下文复用",
              all(protocol_facts.get(name) is True for name in ("agent_terminal", "request_capture", "tool_protocol", "final_answer"))
              and names.count("get_content_run") >= 1 and names.count("discuss_content_run") == 2,
              ["requests.json", "transport.json", "snapshots.json", "messages.json", "trace.json"])
        first = records[0] if len(records) > 0 else {}
        second = records[1] if len(records) > 1 else {}
        check("two_real_discussions", "两条真实完成讨论准确关联同一Run/Revision/digest",
              len(records) == 2 and all(row.get("status") == "completed" and row.get("run_id") == oracle.get("seed_run_id")
                  and row.get("revision_id") == oracle.get("seed_revision_id")
                  and row.get("artifact_digest") == oracle.get("seed_artifact_digest") for row in records),
              ["discussion_records.json", "browser.json"])
        check("fork_then_continue", "首轮fork原生产thread，第二轮续聊首轮讨论thread",
              bool(first.get("source_thread_id")) and first.get("source_thread_id") == oracle.get("production_thread_id")
              and bool(first.get("thread_id")) and second.get("thread_id") == first.get("thread_id")
              and second.get("source_thread_id") == oracle.get("production_thread_id")
              and any(row.get("operation") == "thread_fork" and row.get("phase") == "returned"
                      and row.get("requested_thread_id") == oracle.get("production_thread_id")
                      and row.get("thread_id") == first.get("thread_id") for row in evidence.get("discussion_probe", []))
              and any(row.get("operation") == "thread_resume" and row.get("phase") == "returned"
                      and row.get("requested_thread_id") == first.get("thread_id")
                      and row.get("thread_id") == second.get("thread_id") for row in evidence.get("discussion_probe", [])),
              ["discussion_records.json", "discussion_probe.json", "sdk.json", "oracle.json"])
        image_hashes = [row.get("image_sha256") for row in usage]
        copied_inputs = (len(usage) == 2 and all(len(row.get("image_inputs", [])) == 1
            and row["image_inputs"][0].get("image_sha256") == oracle.get("seed_image_sha256")
            and row["image_inputs"][0].get("path", "").startswith("discussions/work/") for row in usage))
        turn_inputs = [row for row in evidence.get("discussion_probe", [])
                       if row.get("operation") == "thread_turn" and row.get("phase") == "started"]
        sdk_image_inputs = (len(turn_inputs) == 2 and all(
            len([item for item in call.get("inputs", []) if item.get("type") == "LocalImageInput"
                 and str(item.get("module", "")).split(".")[0] == "openai_codex" and valid_hash(item.get("sha256"))
                 and item.get("sha256") == oracle.get("seed_image_sha256")]) == 1
            for call in turn_inputs)
            and [call.get("thread_id") for call in turn_inputs] == [first.get("thread_id"), second.get("thread_id")])
        check("actual_image_inputs", "两轮都实际发送隔离复制的选定版本图片", len(usage) == 2
              and all(value == oracle.get("seed_image_sha256") for value in image_hashes)
              and all(row.get("image_input_count") == 1 for row in usage)
              and [row.get("mode") for row in usage] == ["forked_production", "continued_discussion"]
              and copied_inputs and sdk_image_inputs,
              ["discussion_usage.json", "discussion_probe.json", "reused_source.json"])
        no_generation = not any(row.get("item_type") == "imageGeneration" for row in sdk.get("events", []))
        check("read_only_no_generation", "讨论无生图/返工/批准/发布，Run与旧文件不变",
              no_generation and counts.get("producer_instances", 0) == 0 and db_same
              and evidence.get("source_files_unchanged") is True
              and evidence.get("seed_revision_files_unchanged") is True,
              ["sdk.json", "execution_counts.json", "before.json", "after.json"])
        check("round_continuity", "第二轮追问承接前轮，刷新复读同两条记录且无新POST",
              len(records) == 2 and first.get("reply", "").strip() and second.get("reply", "").strip()
              and browser.get("refreshed_discussion_ids") == [first.get("id"), second.get("id")]
              and browser.get("posts_after_refresh") == 0,
              ["discussion_records.json", "browser.json"])

    failed = any(row["status"] == "failed" for row in checks)
    checks.append({"id": "independent_quality", "label": "图片/讨论内容质量独立阅读",
        "status": "needs_review", "detail": "自动判分仅确认链路与证据；模型答复和视觉质量须独立阅读。",
        "evidence": ["answer.txt", "outputs.json"]})
    dimensions = {
        "task_success": "failed" if failed else "needs_review",
        "boundary_enforced": "failed" if any(row["status"] == "failed" for row in checks
            if row["id"] in {"database_scope", "read_only_no_generation", "old_revision_preserved"}) else "passed",
        "state_consistent": "failed" if any(row["status"] == "failed" for row in checks
            if row["id"] in {"database_scope", "new_revision_attempt", "two_real_discussions"}) else "passed",
        "protocol_valid": "failed" if any(row["status"] == "failed" for row in checks
            if row["id"] in {"fork_then_continue", "actual_image_inputs", "new_real_image"}) else "passed",
    }
    return {"auto_status": "failed" if failed else "needs_review", "checks": checks, "dimensions": dimensions}
