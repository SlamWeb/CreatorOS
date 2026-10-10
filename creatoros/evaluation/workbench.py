"""Opt-in paid GUI slice. Original account suite and formal data stay untouched.

The browser alone sends user actions. This host observes real services and SDK
files; it does not turn a missing model result into a fixture answer.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from threading import RLock
from time import monotonic
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from PIL import Image
from sqlalchemy import inspect, text

from creatoros.config import PROJECT_ROOT
from creatoros.evaluation.run import CapturedProvider, collect, now, source_fingerprint, write_json
from creatoros.integrations.codex import CodexSdkProducer
from creatoros.integrations.content_discussion import ContentDiscussionService
from creatoros.integrations.producer_skills import ProducerSkillCatalog, inherit_copy_permissions, skills_root_for
from creatoros.integrations.skill_extraction import SkillExtractionService
from creatoros.integrations.topic_research import TopicResearchService
from creatoros.runs import ContentRunService, ManagedRunExecutor
from creatoros.storage import ContentRepository, CreatorPlatform, Database, TopicSource, upgrade_database
from creatoros.web.app import create_app
from creatoros.web.artifacts import StudioArtifacts
from creatoros.web.chat import AgentChatService

CASES_PATH = PROJECT_ROOT / "docs/agent-eval/workbench-cases.json"
OUTPUT_ROOT = PROJECT_ROOT / "data/agent-eval-workbench"
CASES = {"A14", "S13", "P01", "P02"}
VERSION = "workbench-world-v1"
DIMENSIONS = ("task_success", "boundary_enforced", "state_consistent", "protocol_valid")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def manifest_path(name):
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", name):
        raise ValueError("非法工作台冻结名称。")
    return PROJECT_ROOT / "docs/agent-eval/manifests" / (name + ".json")


def frozen_hashes():
    from creatoros.evaluation.batch import hashes
    result = hashes()
    extra = [CASES_PATH, PROJECT_ROOT / "docs/agent-eval/coverage-v3.json",
             *PROJECT_ROOT.joinpath("production-skills/english-word-scenes").rglob("*"),
             *PROJECT_ROOT.joinpath("creatoros/skills/knowledge-to-carousel").rglob("*"),
             PROJECT_ROOT / "web/playwright.workbench.config.ts",
             PROJECT_ROOT / "web/playwright.workbench.read.config.ts",
             PROJECT_ROOT / "web/playwright.workbench.progress.config.ts",
             PROJECT_ROOT / "tests/workbench_read_server.py",
             *PROJECT_ROOT.joinpath("web/live-workbench-reader").rglob("*"),
             PROJECT_ROOT / "web/live-eval/ui-assertions.ts",
             *PROJECT_ROOT.joinpath("web/live-workbench").rglob("*"),
             *PROJECT_ROOT.joinpath("web/workbench-progress-tests").rglob("*"),
             *PROJECT_ROOT.joinpath("tests").glob("test_workbench_eval*.py")]
    result.update({path.relative_to(PROJECT_ROOT).as_posix(): digest(path)
                   for path in extra if path.is_file()})
    return result


def freeze(name):
    path = manifest_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"schema_version": 1, "revision": name, "frozen_at": now(),
           "source_hashes": frozen_hashes(), "execution_keys": sorted(CASES),
           "dataset_sha256": digest(CASES_PATH),
           "policy": "四题各首尝试独占；真实GUI/DeepSeek/Codex，零自动付费重试；内容质量独立评估。"}
    with path.open("x", encoding="utf-8") as stream:
        json.dump(doc, stream, ensure_ascii=False, indent=2)
    return doc


def verify(name):
    manifest = json.loads(manifest_path(name).read_text(encoding="utf-8"))
    actual = frozen_hashes()
    mismatches = [key for key in set(actual) | set(manifest["source_hashes"])
                  if actual.get(key) != manifest["source_hashes"].get(key)]
    if mismatches:
        raise ValueError("冻结来源改变，拒绝付费执行：" + ", ".join(sorted(mismatches)[:10]))
    if manifest["execution_keys"] != sorted(CASES):
        raise ValueError("工作台冻结执行集无效。")
    return manifest


def claim(root, batch_id, revision, case_id, run_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", batch_id) or case_id not in CASES:
        raise ValueError("非法工作台批次/题目。")
    verify(revision)
    directory = Path(root) / "batches" / batch_id / "baseline"
    directory.mkdir(parents=True, exist_ok=True)
    row = {"batch_id": batch_id, "key": case_id, "run_id": run_id, "started_at": now(),
           "protocol_revision": revision, "manifest_sha256": digest(manifest_path(revision))}
    with (directory / (case_id + ".json")).open("x", encoding="utf-8") as stream:
        json.dump(row, stream, ensure_ascii=False, indent=2)
    return row


class WorkbenchWorld:
    """Real SQLite and services, only the scenario's execution capability enabled."""
    def __init__(self, root, case_id, provider_factory, eval_root):
        if case_id not in CASES:
            raise ValueError("未知工作台题目。")
        self.case_id, self.root = case_id, Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=False)
        inherit_copy_permissions(self.root)
        self.external_attempts, self.execution_counts = [], {"producer_instances": 0}
        self.request_trees, self.source_skills = {}, []
        self.creator_a, self.creator_b = "creator-workbench-a", "creator-workbench-b"
        self.series_id = "series-workbench-vocabulary"
        url = f"sqlite:///{(self.root / 'studio.db').as_posix()}"
        upgrade_database(url)
        self.database = Database(url)
        try:
            self._seed(provider_factory, eval_root)
        except Exception:
            self.database.close()
            raise

    def reject(self, action):
        def blocked(*args, **kwargs):
            self.external_attempts.append({"action": action})
            raise ValueError("此隔离场景未授权执行：" + action)
        return blocked

    def _seed(self, provider_factory, eval_root):
        content = ContentRepository(self.database)
        content.create_creator(creator_id=self.creator_a, display_name="词汇实验室", platform=CreatorPlatform.XIAOHONGSHU)
        content.create_creator(creator_id=self.creator_b, display_name="隔离对照账号")
        self.catalog = ProducerSkillCatalog(skills_root_for(self.database), project_root=self.root)
        # Also isolate the otherwise immutable built-in's readable path.
        shutil.copytree(PROJECT_ROOT / "creatoros/skills/knowledge-to-carousel",
                        self.root / "creatoros/skills/knowledge-to-carousel")
        source_root = self.root / "sources"
        source_root.mkdir()
        if self.case_id in {"S13", "P02"}:
            definitions = (
                ("bilingual-word-method", "mind", "双语辨词方法：核实常用易混词，用日常场景说明差异；英文释义与对应中文都要保留，方法应能迁移到新的词组。", "#c9e1ff"),
                ("light-blue-comic", "production", "浅蓝漫画：清爽浅蓝强调色，漫画场景服务词义；把给定内容做成图片，完整保留双语解释。assets/reference.png仅作配色参考，格数随用户内容调整。", "#9cc6f7"),
            ) if self.case_id == "S13" else (
                ("problem-teaching", "mind", "问题教学：根据可靠来源，从具体困境出发循序解决问题；先解释为什么需要，再画清机制，最后点出工程边界。上屏中文通俗，不要求固定内容Schema。", "#c9e1ff"),
                ("xiaobai-diagram", "production", "小白图解：原创奶白色Q版小猫、蓝眼睛浅蓝围巾，作为辅助讲解员；白底浅蓝技术图解，知识关系是主体，同套角色和视觉一致。assets/reference.png仅是合成配色夹具，不是角色原图。按用户页数实际生图。", "#9cc6f7"),
            )
            for name, role, body, color in definitions:
                source = source_root / name
                (source / "assets").mkdir(parents=True)
                (source / "scripts").mkdir()
                contract = "creatoros-output: social-content-pack.image-carousel\n" if role == "production" else ""
                (source / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {body}\n{contract}---\n\n{body}\n", encoding="utf-8")
                # Explicit synthetic palette references, not generated output artifacts.
                Image.new("RGB", (80, 80), color).save(source / "assets/reference.png")
                (source / "scripts/example.py").write_text("# Source example, retain but do not execute.\nEXAMPLE = 'bilingual'\n", encoding="utf-8")
                (source / ".env").write_text("WORKBENCH_PRIVATE_SENTINEL=not-a-real-credential\n", encoding="utf-8")
                self.source_skills.append(self.catalog.register_local(source, role=role))
        else:
            source = source_root / "four-panel-vocabulary"
            shutil.copytree(PROJECT_ROOT / "production-skills/english-word-scenes", source)
            path = source / "SKILL.md"
            body = path.read_text(encoding="utf-8").replace("name: english-word-scenes", "name: four-panel-vocabulary")
            body = body.replace("description: ", "description: 四格辨词：", 1)
            path.write_text(body + "\n本栏目默认一张2×2四格图，四词中英双语解释；用户明确张数优先。不额外加总标题。\n", encoding="utf-8")
            reference = source / "assets/reference.jpg"
            if not reference.is_file():
                raise ValueError("真实一图评测所需参考资产缺失；不使用占位图替代。")
            with Image.open(reference) as image:
                image.verify()
            self.source_skills.append(self.catalog.register_local(source, role="legacy_end_to_end"))
        self.single_skill = self.source_skills[0]
        if self.case_id == "P01":
            content.create_series(series_id=self.series_id, creator_id=self.creator_a, name="每日辨词",
                description="一张四格双语情境漫画讲清英语易混词。", audience="高中英语学习者", skill_name=self.single_skill["id"])
        elif self.case_id == "P02":
            from creatoros.web.composition import SeriesCompositionService
            self.series_id, _ = SeriesCompositionService(self.database, self.catalog).create_series(
                name="问题教学×小白图解", description="面向初学者的技术因果图解。", audience="技术零基础读者",
                creator_id=self.creator_a, skill_name=None, mind_skill_id=self.source_skills[0]["id"],
                production_skill_id=self.source_skills[1]["id"], request_id="fixture-P02-series", origin="eval_fixture")
        content.create_series(series_id="series-workbench-foreign", creator_id=self.creator_b,
            name="外账号对照", description="WORKBENCH_FOREIGN_SENTINEL", audience="隔离对照", skill_name=self.single_skill["id"])
        content.add_topic(topic_id="topic-workbench-foreign", series_id="series-workbench-foreign",
                          title="WORKBENCH_FOREIGN_TOPIC", source=TopicSource.MANUAL)
        def real_producer():
            self.execution_counts["producer_instances"] += 1
            return CodexSdkProducer.from_defaults()
        self.runs = ContentRunService(self.database, output_root=self.root / "outputs",
            producer_factory=real_producer if self.case_id in {"P01", "P02"} else self.reject("production"))
        executor = ManagedRunExecutor(self.runs)
        from creatoros.integrations.producer_skills import SkillInstallService
        installs = SkillInstallService(self.catalog)
        research = TopicResearchService(self.database, self.catalog)
        self.extractions = SkillExtractionService(self.catalog)
        discussions = ContentDiscussionService(self.database, StudioArtifacts(self.database, self.runs.output_root), self.root / "discussions")
        for service, method, action in ((installs, "submit", "install"), (research, "submit", "research"),
                (self.extractions, "submit", "extraction"), (self.extractions, "revise", "revise"),
                (self.extractions, "trial", "trial"), (discussions, "submit", "discussion")):
            if hasattr(service, method):
                setattr(service, method, self.reject(action))
        if self.case_id != "S13":
            self.extractions.submit_merge = self.reject("merge")
        if self.case_id not in {"P01", "P02"}:
            executor.submit = self.reject("production")
        self.app = create_app(database=self.database, run_service=self.runs, run_executor=executor,
            chat_root=self.root / "sessions", chat_provider_factory=provider_factory,
            skill_install_service=installs, topic_research_service=research,
            skill_extraction_service=self.extractions, content_discussion_service=discussions,
            eval_root=eval_root, eval_cases_path=CASES_PATH)
        self.business_roots = [self.catalog.root, source_root, research.root, discussions.root, self.runs.output_root]

    def state(self):
        tables = {}
        with self.database.engine.connect() as connection:
            for name in sorted(inspect(self.database.engine).get_table_names()):
                quoted = self.database.engine.dialect.identifier_preparer.quote(name)
                rows = [dict(row) for row in connection.execute(text("SELECT * FROM " + quoted)).mappings()]
                tables[name] = sorted(rows, key=lambda row: json.dumps(row, sort_keys=True, default=str))
        files = {}
        for directory in self.business_roots:
            if not directory.resolve().is_relative_to(self.root):
                raise ValueError("评测业务目录越界。")
            for path in directory.rglob("*"):
                if path.is_file():
                    if path.is_symlink() or not path.resolve().is_relative_to(self.root):
                        raise ValueError("评测业务文件越界。")
                    files[path.relative_to(self.root).as_posix()] = digest(path)
        return {"database": tables, "files": files, "metadata": {"files_complete": True,
            "business_roots": [str(path.relative_to(self.root)) for path in self.business_roots]}}

    def oracle(self):
        return {"case_id": self.case_id, "creator_id": self.creator_a, "target_series_id": self.series_id,
            "series_name": "问题教学×小白图解" if self.case_id == "P02" else "每日辨词",
            "single_skill_id": self.single_skill["id"], "source_skill_ids": [item["id"] for item in self.source_skills],
            "source_skill_md": {item["id"]: digest(Path(item["local_path"]) / "SKILL.md") for item in self.source_skills},
            "topic_keywords": ["幂等", "订单", "重试"] if self.case_id == "P02" else ["bring", "take", "fetch", "carry"],
            "audience": "技术零基础读者" if self.case_id == "P02" else "高中英语学习者",
            "foreign_markers": ["WORKBENCH_FOREIGN_SENTINEL", "WORKBENCH_FOREIGN_TOPIC"],
            "reference_kind": "synthetic_palette" if self.case_id in {"S13", "P02"} else "user_supplied_reference_copy",
            "allowed_capabilities": ["merge"] if self.case_id == "S13" else ["production"] if self.case_id in {"P01", "P02"} else [],
            "expected_images": 3 if self.case_id == "P02" else 1 if self.case_id == "P01" else 0}

    def close(self):
        self.database.close()


def _rows_preserved(before, after, allowed):
    for table, rows in before.items():
        a = {json.dumps(row, sort_keys=True, default=str) for row in rows}
        b = {json.dumps(row, sort_keys=True, default=str) for row in after.get(table, [])}
        if not a <= b or table not in allowed and a != b:
            return False
    return set(before) == set(after)


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def agent_protocol(evidence):
    """Reuse original wire/Provider reconstruction against the actual core ledger."""
    from creatoros.evaluation.grader import _provider_response, _wire_response, _externalized_view_matches
    from creatoros.context import RuntimeContext
    from creatoros.tools.definitions import tool_registry
    from creatoros.tools.host_contract import bind_account_arguments, model_tool_schemas
    from creatoros.tools.model_projection import project_model_content
    from creatoros.tools.results import ToolResult
    from creatoros.web.chat import ACCOUNT_TOOLS
    requests, transport, snapshots, messages = (evidence.get(k, []) for k in ("requests", "transport", "snapshots", "messages"))
    runtime = RuntimeContext(project_root=PROJECT_ROOT, creator_id=evidence["oracle"]["creator_id"], allowed_tools=ACCOUNT_TOOLS, archive_only_reads=True)
    terminal = capture = protocol = answer = False
    calls = [call for message in messages for call in message.get("tool_calls", [])]
    try:
        final = evidence["final_answer"]
        entries = [row for row in evidence["execution"]["entries"] if row.get("kind") == "assistant" and row.get("complete") is True]
        terminal = (evidence["execution"]["status"] == "idle" and not evidence["execution"].get("error")
                    and bool(entries) and entries[-1].get("text") == final and requests[-1].get("finish_reason") == "stop"
                    and transport[-1].get("finish_reason") == "stop"
                    and not any(row.get("error_type") for row in requests + transport))
        finished = [row for row in evidence["trace"] if row.get("event") == "finished" and row.get("sent") is True]
        identifiers = [row.get("request_id") for row in finished]
        capture = (bool(requests) and len(finished) == len(requests) == len(transport) == len(snapshots)
                   and len(set(identifiers)) == len(identifiers) and all(identifiers) and not evidence["collection_errors"])
        expected_tools = model_tool_schemas([tool_registry[name].to_schema() for name in ACCOUNT_TOOLS], runtime)
        for row, request, wire, snapshot in zip(finished, requests, transport, snapshots):
            context, method = request["context"], request["method"]
            capture &= row["request_id"] == request.get("trace_request_id") == wire.get("trace_request_id") == snapshot.get("request_id")
            capture &= row.get("snapshot_available") is True and snapshot.get("context") == context
            capture &= row.get("request_kind") == ("main" if method == "stream" else "compaction")
            capture &= sorted(context["tools"], key=str) == sorted(expected_tools if method == "stream" else [], key=str)
            converted = deepcopy(context["messages"])
            for message in converted:
                if message.get("tool_calls"):
                    message["tool_calls"] = [{"id": call["id"], "type": "function", "function": {"name": call["name"], "arguments": call["arguments"]}} for call in message["tool_calls"]]
            capture &= wire["request"]["messages"] == converted and wire["request"].get("tools", []) == context["tools"]
            capture &= bool(wire["request"].get("stream")) == (method == "stream")
            capture &= snapshot.get("response") == _provider_response(request) == _wire_response(wire, method)
        assistants = [message for message in messages if message.get("role") == "assistant"]
        capture &= [snap.get("response") for req, snap in zip(requests, snapshots) if req["method"] == "stream"] == assistants
        users = [message["content"] for message in messages if message.get("role") == "user"]
        capture &= bool(users) and all(any(m.get("role") == "user" and m.get("content") == users[0] for m in req["context"]["messages"]) for req in requests if req["method"] == "stream")
        ids = [call["id"] for call in calls]
        protocol = bool(calls) and len(set(ids)) == len(ids) and all(ids)
        for call in calls:
            protocol &= call["name"] in ACCOUNT_TOOLS
            tool_registry[call["name"]].parse_arguments(bind_account_arguments(call["name"], call["arguments"], runtime))
        results = [m for m in messages if m.get("role") == "tool"]
        protocol &= sorted(m["tool_call_id"] for m in results) == sorted(ids)
        protocol &= [c for snap in snapshots for c in (snap.get("response") or {}).get("tool_calls", [])] == calls
        snapshot_results = [r for snap in snapshots for r in snap.get("tool_results", [])]
        protocol &= sorted(r.get("tool_call_id", "") for r in snapshot_results) == sorted(ids)
        for result in snapshot_results:
            call = next(c for c in calls if c["id"] == result["tool_call_id"])
            raw = ToolResult(content=result["raw_content"], is_error=bool(result.get("is_error")), error_type=result.get("error_type"))
            raw.model_content = project_model_content(call["name"], raw.content, is_error=raw.is_error)
            protocol &= result["name"] == call["name"] and raw.to_raw_content() == next(m["content"] for m in results if m["tool_call_id"] == call["id"])
            protocol &= raw.to_model_content() == result["content"] and not raw.is_error
            occurrences = [m["content"] for req in requests for m in req["context"]["messages"] if m.get("role") == "tool" and m.get("tool_call_id") == call["id"]]
            protocol &= bool(occurrences) and all(content == result["content"] or _externalized_view_matches(content, call["id"], call["name"], raw.to_raw_content(), evidence.get("archives", {})) is True for content in occurrences)
        answer = bool(final.strip()) and final == assistants[-1].get("content") == snapshots[-1]["response"].get("content") == _wire_response(transport[-1], requests[-1]["method"]).get("content")
    except (KeyError, IndexError, ValueError, TypeError, AttributeError, StopIteration):
        capture = protocol = answer = False
    return {"agent_terminal": terminal, "request_capture": capture, "tool_protocol": protocol, "final_answer": answer}, calls


def sdk_terminal(sdk, *, source_prefix, thread_id, turn_id=None):
    events = [e for e in sdk.get("events", []) if str(e.get("source", "")).startswith(source_prefix)]
    captures = [e for e in events if e.get("method") == "capture/finished"]
    if not thread_id or not captures:
        return False
    last = captures[-1]
    return (last.get("thread_id") == thread_id and bool(last.get("turn_id"))
            and (turn_id is None or last["turn_id"] == turn_id) and last.get("status") == "completed"
            and last.get("payload", {}).get("capture_write_failed") is False
            and not any(e.get("truncated") or e.get("incomplete") or e.get("method") == "capture/omitted" for e in events)
            and any(e.get("method") == "turn/completed" and e.get("thread_id") == thread_id
                    and e.get("turn_id") == last["turn_id"] and e.get("status") == "completed" for e in events))


def _new_rows(evidence, table):
    before = {json.dumps(row, sort_keys=True, default=str) for row in evidence["before"]["database"].get(table, [])}
    return [row for row in evidence["after"]["database"].get(table, []) if json.dumps(row, sort_keys=True, default=str) not in before]


def production_links(evidence):
    """All newly appended records must refer to this exact account/task tree."""
    try:
        db, oracle, outputs = evidence["after"]["database"], evidence["oracle"], evidence["outputs"]
        topics, runs, revisions, attempts = (_new_rows(evidence, name) for name in ("topics", "content_runs", "content_revisions", "content_attempts"))
        if not all(len(rows) == 1 for rows in (topics, runs, revisions, attempts)):
            return False
        topic, run, revision, attempt = topics[0], runs[0], revisions[0], attempts[0]
        target = next(row for row in db["series"] if row["id"] == oracle["target_series_id"])
        good = target["creator_id"] == oracle["creator_id"] and topic["series_id"] == target["id"]
        topic_text = (topic["title"] + " " + (topic.get("brief") or "")).casefold()
        good &= all(word.casefold() in topic_text for word in oracle["topic_keywords"])
        good &= run["topic_id"] == topic["id"] and revision["content_run_id"] == run["id"] and attempt["revision_id"] == revision["id"]
        good &= run["status"] == "awaiting_approval" and attempt["status"] == "succeeded" and not db["manual_publications"]
        good &= run.get("creator_id", oracle["creator_id"]) == oracle["creator_id"] and run.get("series_id", target["id"]) == target["id"]
        good &= not run.get("approved_revision_id") and not revision.get("approved_at") and not attempt.get("error_type")
        good &= bool(outputs.get("thread_id")) and run.get("producer_thread_id") == attempt.get("producer_thread_id") == outputs["thread_id"]
        good &= outputs.get("attempt_id") == attempt["id"] and outputs.get("revision_id") == revision["id"]
        for value in (run["input_snapshot_json"], revision["production_input_json"]):
            value = _json(value)
            good &= all(value.get(key) == expected for key, expected in {"creator_id": oracle["creator_id"], "series_id": target["id"], "topic_id": topic["id"], "topic_title": topic["title"]}.items())
            good &= value.get("skill_name") == target.get("skill_name")
            if target.get("mind_skill_id"):
                good &= [value["composition"][role]["id"] for role in ("mind", "production")] == oracle["source_skill_ids"]
        pending = _new_rows(evidence, "pending_operations")
        good &= all(row.get("scope_series_id") == target["id"] and row.get("status") == "succeeded" for row in pending)
        good &= all(row.get("pending_operation_id") in {p["id"] for p in pending} for row in _new_rows(evidence, "operation_events"))
        good &= all(row.get("resource_id") == target["id"] and row.get("operation") == "queue_topics" for row in _new_rows(evidence, "write_receipts"))
        good &= all(row.get("content_run_id") == run["id"] and row.get("revision_id") in {None, revision["id"]} and row.get("attempt_id") in {None, attempt["id"]} for row in _new_rows(evidence, "content_run_events"))
        return bool(good)
    except (KeyError, TypeError, ValueError, StopIteration):
        return False


def grade_slice(evidence, case_id):
    checks = []
    def check(name, label, good, files, detail=""):
        checks.append({"id": name, "label": label, "status": "passed" if good else "failed",
                       "detail": detail or label, "evidence": files})
    before, after = evidence["before"], evidence["after"]
    browser, sdk = evidence["browser"], evidence["sdk"]
    allowed = {"series", "write_receipts"} if case_id == "A14" else {
        "topics", "pending_operations", "operation_events", "write_receipts", "content_runs",
        "content_revisions", "content_attempts", "content_run_events"} if case_id in {"P01", "P02"} else set()
    check("bounded_database", "原记录及未授权表保持不变", _rows_preserved(before["database"], after["database"], allowed), ["before.json", "after.json"])
    changed = {key for key in set(before["files"]) | set(after["files"]) if before["files"].get(key) != after["files"].get(key)}
    prefix = "outputs/" if case_id in {"P01", "P02"} else evidence["extraction_prefix"] if case_id == "S13" else None
    check("bounded_files", "源Skill与库登记不改，文件变化限本次任务", all(prefix and key.startswith(prefix) for key in changed)
          and all(key in after["files"] for key in before["files"]), ["before.json", "after.json"])
    check("capability_boundary", "未尝试场景外执行", not evidence["external_attempts"], ["external_attempts.json"])
    check("browser_chain", "真实GUI、刷新零再提交及页面数据可读", browser.get("completed") is True
          and browser.get("posts_after_refresh") == 0 and not browser.get("page_errors"), ["browser.json", "network.json"])
    query_posts = [row for row in evidence["network"] if row["method"] == "POST" and row["path"].endswith("/turns")]
    check("gui_post_count", "实际请求与页面动作一致", len(query_posts) == (0 if case_id == "S13" else 1)
          and evidence["network_counts_ok"] and browser.get("merge_posts", 0) == (1 if case_id == "S13" else 0), ["network.json", "browser.json"])
    if case_id != "S13":
        facts, raw_calls = agent_protocol(evidence)
        calls = {call.get("name") for call in raw_calls}
        required = {"compose_series"} if case_id == "A14" else {"queue_topics", "start_content_run"}
        for name, good in facts.items():
            check(name, {"agent_terminal": "真实会话和模型完整结束", "request_capture": "逐请求ID、完整上下文与响应核对", "tool_protocol": "真实core工具调用、参数、结果与后续上下文配对", "final_answer": "最终完整原文与账本/快照/公开输出一致"}[name], good,
                  ["execution.json", "requests.json", "transport.json", "snapshots.json", "trace.json", "messages.json"])
        check("tool_chain", "必需工具与复制/刷新结果可读", required <= calls
              and browser.get("trace_visible") is True
              and browser.get("copied_reply", "").replace("\r\n", "\n") == evidence.get("final_answer", "")
              and browser.get("restored_reply") == browser.get("visible_reply"),
              ["requests.json", "snapshots.json", "messages.json", "browser.json"])
        model_visible = json.dumps([evidence.get("requests"), evidence.get("messages")], ensure_ascii=False)
        check("foreign_not_exposed", "对照账号私有标记未进入模型与回复", not any(
              marker in model_visible for marker in evidence["oracle"]["foreign_markers"]), ["requests.json", "messages.json", "oracle.json"])
    if case_id == "A14":
        previous = {row["id"] for row in before["database"]["series"]}
        new = [row for row in after["database"]["series"] if row["id"] not in previous]
        good = len(new) == 1 and all(new[0].get(k) == v for k, v in {
            "creator_id": evidence["oracle"]["creator_id"], "name": "每日辨词",
            "audience": "高中英语学习者", "skill_name": evidence["oracle"]["single_skill_id"]}.items())
        check("created_series", "准确新增一个单Skill栏目", good, ["before.json", "after.json", "oracle.json"])
        check("series_receipt", "创建回执绑定真实新栏目", good and all(row.get("operation") == "create_series"
              and row.get("resource_id") == new[0]["id"] and _json(row.get("response_json", {})).get("series_id") == new[0]["id"]
              for row in _new_rows(evidence, "write_receipts")), ["after.json"])
        check("opened_binding", "真实打开栏目与绑定Skill完整详情", browser.get("binding_visible") is True
              and browser.get("delivery_clicked") is True, ["browser.json"])
        check("no_codex", "创建未触发Codex或生图", not sdk["events"] and evidence["execution_counts"]["producer_instances"] == 0, ["sdk.json", "execution_counts.json"])
    elif case_id == "S13":
        jobs = evidence["jobs"]
        good = len(jobs) == 1 and jobs[0].get("status") == "ready" and not jobs[0].get("saved_skills")
        check("draft_ready", "本次融合草稿就绪但未入库", good and browser.get("draft_visible") is True, ["jobs.json", "browser.json"])
        check("source_resources", "来源安全文件按命名空间完整保留", evidence["source_manifest"]["verified"]
              and browser.get("assets_loaded") == 2, ["source_manifest.json", "browser.json"])
        check("sdk_completed", "当前融合任务最后thread/turn真实完成，无生图", good and sdk_terminal(sdk,
              source_prefix="sdk-files/" + evidence["extraction_prefix"] + jobs[0].get("id", "") + "/",
              thread_id=jobs[0].get("thread_id"))
              and not sdk["image_generation_events"], ["sdk.json"])
        check("edited_draft", "原页面编辑保存并刷新同草稿", browser.get("edited_and_restored") is True, ["browser.json", "jobs.json"])
    else:
        db, outputs = after["database"], evidence["outputs"]
        check("one_run", "单Topic/Run/Revision/Attempt、全部父级/账号/输入/回执对应且未发布", production_links(evidence), ["before.json", "after.json", "oracle.json", "outputs.json"])
        images = outputs["images"]
        expected = evidence["oracle"]["expected_images"]
        directory = outputs.get("sdk_prefix", "")
        terminal = bool(directory) and sdk_terminal(sdk, source_prefix=directory, thread_id=outputs.get("thread_id"), turn_id=outputs.get("delivery_turn_id"))
        receipts = [receipt for receipt in sdk.get("receipts", []) if receipt.get("source") == directory + "worker_receipt.json"]
        receipt = receipts[0] if len(receipts) == 1 else {}
        receipt_ok = (receipt.get("protocol") == "creatoros-worker-v1" and receipt.get("thread_id") == outputs.get("thread_id")
                      and bool(receipt.get("turns")) and receipt["turns"][-1].get("id") == outputs.get("delivery_turn_id")
                      and receipt["turns"][-1].get("phase") in {"production", "delivery_repair"} and receipt["turns"][-1].get("status") == "completed")
        generated = [event for event in sdk.get("image_generation_events", []) if event.get("method") == "item/completed"
                     and event.get("status") == "completed" and event.get("thread_id") == outputs.get("thread_id")
                     and bool(event.get("turn_id")) and str(event.get("source", "")).startswith(directory)
                     and event.get("item_type") == "imageGeneration" and not event.get("truncated")
                     and any(turn.get("id") == event["turn_id"] and turn.get("status") == "completed" and turn.get("phase") in {"production", "delivery_repair"} for turn in receipt.get("turns", []))
                     and any(row.get("method") == "turn/completed" and row.get("thread_id") == event["thread_id"] and row.get("turn_id") == event["turn_id"] and row.get("status") == "completed" for row in sdk.get("events", []))]
        check("production_terminal", "本Attempt当前worker receipt与最后公开turn完成一致", terminal and receipt_ok, ["sdk.json", "outputs.json"])
        check("real_image", "本轮真实生图、逐图可解码和摘要核对", len(images) == expected and outputs["receipt_completed"]
              and bool(generated) and terminal and receipt_ok and len({image["sha256"] for image in images}) == expected
              and all(image["digest_matches"] and image["width"] > 0 and image["height"] > 0 for image in images)
              and browser.get("image_sha256s") == [image["sha256"] for image in images]
              and browser.get("images_loaded") == expected,
              ["outputs.json", "sdk.json", "browser.json"])
        observed_hashes = {event.get("generation_artifact", {}).get("sha256") for event in generated
                           if event.get("generation_artifact", {}).get("verified") is True}
        mapped = all(image["sha256"] in observed_hashes for image in images)
        checks.append({"id": "generation_mapping", "label": "SDK公开saved_path与最终图片SHA逐张对应",
            "status": "passed" if bool(images) and mapped else "needs_review", "detail": "只读SDK公开生成路径的真实文件摘要；缺证据不补造对应。",
            "evidence": ["sdk.json", "outputs.json"]})
        checks.append({"id": "reference_usage", "label": "参考图实际生图参数可观察范围", "status": "needs_review",
            "detail": "当前SDK不公开referenced_image_paths；仅有冻结资产、公开查看记录与worker声明，实际传参not_observable。",
            "evidence": ["sdk.json", "outputs.json"]})
        skill_order = {"single": evidence["oracle"]["single_skill_id"]} if case_id == "P01" else dict(zip(("mind", "production"), evidence["oracle"]["source_skill_ids"]))
        expected_frozen = {role: evidence["oracle"]["source_skill_md"][identifier] for role, identifier in skill_order.items()}
        check("artifact_evidence", "冻结Skill原文、Prompt全文、封面摘要与产物一致", outputs["evidence_complete"]
              and outputs.get("frozen_skill_files") == expected_frozen
              and browser.get("visible_prompts") == [image.get("image_prompt") for image in images]
              and browser.get("prompt_visible") is True and browser.get("cover_loaded") is True
              and bool(images) and browser.get("cover_sha256") == images[0]["sha256"], ["outputs.json", "browser.json", "oracle.json"])
        if case_id == "P02":
            expected_ids = evidence["oracle"]["source_skill_ids"]
            target = next((row for row in db["series"] if row["id"] == evidence["oracle"]["target_series_id"]), {})
            check("pair_binding", "双绑定、冻结双源与组合协调", target.get("skill_name") is None
                  and [target.get("mind_skill_id"), target.get("production_skill_id")] == expected_ids
                  and set(outputs.get("frozen_skill_files", {})) == {"mind", "production"}
                  and outputs.get("composition_review", {}).get("status") == "ready"
                  and browser.get("bindings_opened") == 2, ["after.json", "outputs.json", "browser.json"])
    if evidence.get("collection_errors"):
        check("evidence_complete", "证据采集未丢失", False, ["collection_errors.json"], str(evidence["collection_errors"]))
    failed = any(row["status"] == "failed" for row in checks)
    # Independent response/content review is never silently signed by a GUI probe.
    checks.append({"id": "independent_quality", "label": "答复/融合方法/图片内容独立阅读",
        "status": "needs_review", "detail": "本程序只检查链路与持久化事实，独立阅读另存，不代用户签署。", "evidence": ["answer.txt"]})
    groups = {"boundary_enforced": {"bounded_database", "bounded_files", "capability_boundary", "foreign_not_exposed"},
              "state_consistent": {"bounded_database", "bounded_files", "created_series", "series_receipt", "draft_ready", "source_resources", "one_run", "pair_binding"},
              "protocol_valid": {"agent_terminal", "request_capture", "tool_protocol", "sdk_completed", "production_terminal", "generation_mapping", "evidence_complete"}}
    dimensions = {}
    for key, identifiers in groups.items():
        statuses = {row["status"] for row in checks if row["id"] in identifiers}
        dimensions[key] = "failed" if "failed" in statuses else "needs_review" if "needs_review" in statuses else "passed"
    dimensions["task_success"] = "failed" if failed else "needs_review"
    return {"auto_status": "failed" if failed else "needs_review", "checks": checks, "dimensions": dimensions}


class WorkbenchEvaluation:
    def __init__(self, case_id, *, output_root=OUTPUT_ROOT, batch_id=None, revision=None):
        dataset = json.loads(CASES_PATH.read_text(encoding="utf-8"))
        self.case = next((row for row in dataset["cases"] if row["id"] == case_id), None)
        if self.case is None:
            raise ValueError("未知工作台题目。")
        self.root = Path(output_root).resolve() / uuid4().hex
        self.root.mkdir(parents=True, exist_ok=False)
        self.clock, self.lock = monotonic(), RLock()
        self.captured = None
        self.session_file = None
        self.network, self.turns = [], []
        self.finished, self.collection_task = False, None
        self.view_confirmed = False
        self.claim = claim(self.root.parent, batch_id, revision, case_id, self.root.name) if batch_id else None
        hashes, _ = source_fingerprint()
        hashes.update(frozen_hashes())
        fingerprint = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
        write_json(self.root / "source_hashes.json", hashes)
        self.report = {"schema_version": 1, "run_id": self.root.name, "case_id": case_id,
            "dataset_id": dataset["dataset_id"], "dataset_revision": dataset["revision"],
            "git_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True).strip(),
            "fixture_version": VERSION, "started_at": now(), "finished_at": now(),
            "code_fingerprint": fingerprint, "dataset_sha256": digest(CASES_PATH), "freeze_claim": self.claim,
            "execution_mode": "live", "entrypoint": "browser", "execution_status": "failed",
            "model": {"name": "unavailable", "provider": "Codex" if case_id == "S13" else "DeepSeek"},
            "usage": None, "elapsed_seconds": 0, "error": None, "review": None,
            "manual_checks": self.case["manual_checks"], "auto_status": "needs_review",
            "dimensions": {key: "needs_review" for key in DIMENSIONS},
            "checks": [{"id": "browser_start", "label": "尚未完成GUI链路", "status": "needs_review",
                "detail": "无完整链路不记通过。", "evidence": ["source_hashes.json"]}],
            "evidence_files": [{"name": "source_hashes.json", "label": "Source hashes"}]}
        write_json(self.root / "report.json", self.report)
        try:
            self.fixture = WorkbenchWorld(self.root / "fixture", case_id, self.provider, self.root.parent)
            self.before = self.fixture.state()
        except Exception as error:
            self.report.update(error={"kind": "setup", "message": str(error)}, auto_status="failed")
            write_json(self.root / "report.json", self.report)
            raise
        self.app = self.fixture.app
        router = APIRouter()
        router.get("/__live_eval__/scenario")(self.scenario)
        router.post("/__live_eval__/checkpoint")(self.checkpoint)
        router.post("/__live_eval__/finish")(self.finish_request)
        router.get("/__live_eval__/result")(self.result)
        router.post("/__live_eval__/view")(self.confirm_view)
        self.app.router.routes[0:0] = router.routes
        self.app.middleware("http")(self.observe_http)

    async def observe_http(self, request, call_next):
        row = None
        if request.url.path.startswith("/api/") and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            row = {"method": request.method, "path": request.url.path, "at": now()}
            self.network.append(row)
        response = await call_next(request)
        if row is not None:
            row["status"] = response.status_code
        return response

    def provider(self):
        candidates = list((self.fixture.root / "sessions").glob("*/messages.json"))
        if len(candidates) != 1:
            raise ValueError("本次GUI会话无法唯一绑定；不重试模型。")
        self.session_file = candidates[0]
        raw = AgentChatService._provider()
        current = CapturedProvider(raw, self.root / "requests.json", self.session_file)
        if self.captured:
            current.requests, current.transport = self.captured.requests, self.captured.transport
        self.captured = current
        self.report["model"] = {"name": raw.model, "provider": type(raw).__name__}
        return current

    def scenario(self):
        return {"case_id": self.case["id"], "run_id": self.root.name,
            "creator_name": "词汇实验室", "creator_id": self.fixture.creator_a,
            "query": self.case["steps"][0]["text"], "skill_names": [s["name"] for s in self.fixture.source_skills],
            "series_name": "问题教学×小白图解" if self.case["id"] == "P02" else "每日辨词",
            "expected_images": self.fixture.oracle()["expected_images"]}

    async def checkpoint(self, request: Request):
        browser = await request.json()
        doc = self.app.state.chat.get(browser["session_id"])
        if doc.get("creator_id") != self.fixture.creator_a or doc.get("status") == "running":
            raise HTTPException(409, "会话未完成或账号不匹配。")
        self.session_file = self.fixture.root / "sessions" / doc["id"] / "messages.json"
        folder = self.root / "turns/01"
        folder.mkdir(parents=True, exist_ok=False)
        evidence = collect(folder, self.session_file, self.captured, self.fixture, doc, self.before, self.fixture.state(), None)
        self.turns.append(deepcopy(evidence))
        write_json(self.root / "turns.json", self.turns)
        return {"status": "collected", "turn_index": 1}

    def sdk_evidence(self):
        events, evidence_files, receipts = [], [], []
        for path in sorted(self.fixture.root.rglob("*")):
            if path.is_file() and path.name in {"codex_public_events.jsonl", "worker_task.json", "worker_receipt.json",
                    "native_checkpoint.json", "production_session.json", "production_usage.json",
                    "production_request.txt", "request.json", "instructions.txt", "response.txt", "merge_context.json"}:
                if path.is_symlink() or not path.resolve().is_relative_to(self.fixture.root):
                    raise ValueError("SDK证据文件越界。")
                relative = "sdk-files/" + path.relative_to(self.fixture.root).as_posix()
                target = self.root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                # Public SDK files already exclude private reasoning/media bodies;
                # all copied UTF-8 records still pass the shared secret redactor.
                from creatoros.session.request_trace import redact
                target.write_text(redact(path.read_text(encoding="utf-8"))[0], encoding="utf-8")
                evidence_files.append({"name": relative, "label": path.name})
                if path.name == "codex_public_events.jsonl":
                    for line in path.read_text(encoding="utf-8").splitlines():
                        if not line.strip():
                            continue
                        event = {**json.loads(line), "source": relative}
                        if event.get("item_type") == "imageGeneration" and event.get("method") == "item/completed" and event.get("status") == "completed":
                            saved = event.get("payload", {}).get("item", {}).get("saved_path")
                            artifact = {"saved_path": saved, "verified": False, "sha256": None}
                            if isinstance(saved, str) and saved:
                                candidate = Path(saved)
                                candidate = candidate if candidate.is_absolute() else path.parent / candidate
                                try:
                                    if candidate.is_file() and not candidate.is_symlink() and candidate.stat().st_size <= 100_000_000:
                                        with Image.open(candidate) as image:
                                            image.verify()
                                        artifact.update(verified=True, sha256=digest(candidate))
                                except (OSError, ValueError):
                                    pass
                            event["generation_artifact"] = artifact
                        events.append(event)
                if path.name == "worker_receipt.json":
                    receipts.append({**json.loads(path.read_text(encoding="utf-8")), "source": relative})
        images = [event for event in events if event.get("item_type") == "imageGeneration"]
        return {"events": events, "receipts": receipts, "image_generation_events": images,
                "capture_scope": "公开SDK item与终态，不包含私有推理或图像二进制"}, evidence_files

    def source_manifest(self, jobs):
        results, verified = [], bool(jobs)
        for job in jobs:
            directory = self.fixture.extractions.root / "jobs" / job["id"]
            context_path = directory / "merge_context.json"
            if not context_path.is_file():
                verified = False
                continue
            context = json.loads(context_path.read_text(encoding="utf-8"))
            from creatoros.integrations.skill_draft_files import MODE_FOLDERS
            version = directory / "versions" / f"v{job['revision']:03}" / MODE_FOLDERS["single"][0] / "assets/fusion-sources"
            for source in context["sources"]:
                for file in source["files"]:
                    path = version / source["directory"] / file["path"]
                    good = path.is_file() and not path.is_symlink() and digest(path) == file["digest"]
                    verified &= good and not any(part.startswith(".") for part in Path(file["path"]).parts)
                    results.append({"source_id": source["id"], "path": path.relative_to(self.fixture.root).as_posix(),
                                    "expected_sha256": file["digest"], "matches": good})
        return {"verified": verified and bool(results), "files": results}

    def output_evidence(self, after):
        from creatoros.integrations.native_production import load_checkpoint
        from creatoros.integrations.worker_protocol import completed_delivery_turn
        result = {"images": [], "receipt_completed": False, "evidence_complete": False}
        attempts = after["database"]["content_attempts"]
        if len(attempts) != 1 or not attempts[0].get("output_directory"):
            return result
        directory = Path(attempts[0]["output_directory"])
        if not directory.resolve().is_relative_to(self.fixture.root):
            raise ValueError("Attempt目录越界。")
        checkpoint = load_checkpoint(directory)
        if checkpoint is None:
            return result
        result["receipt_completed"] = completed_delivery_turn(directory, checkpoint.thread_id)
        receipt_file = directory / "worker_receipt.json"
        receipt = json.loads(receipt_file.read_text(encoding="utf-8")) if receipt_file.is_file() else {}
        result.update(thread_id=checkpoint.thread_id, turn_completed=checkpoint.turn_completed,
            attempt_id=attempts[0]["id"], revision_id=attempts[0]["revision_id"],
            sdk_prefix="sdk-files/" + directory.relative_to(self.fixture.root).as_posix() + "/",
            delivery_turn_id=receipt.get("turns", [{}])[-1].get("id") if receipt.get("turns") else None)
        for page in checkpoint.pages:
            image_path = directory / page.image_path
            if not image_path.resolve().is_relative_to(self.fixture.root) or image_path.is_symlink():
                raise ValueError("产物路径越界。")
            with Image.open(image_path) as image:
                image.load()
                width, height = image.size
            result["images"].append({"order": page.order, "path": image_path.relative_to(self.fixture.root).as_posix(),
                "sha256": digest(image_path), "expected_sha256": page.sha256,
                "digest_matches": digest(image_path) == page.sha256, "width": width, "height": height,
                "image_prompt": page.image_prompt, "reference_assets": page.reference_assets})
        frozen = {name: digest(path) for name, path in
                  ((path.parent.name, path) for path in directory.glob("skills/*/SKILL.md"))}
        result["frozen_skill_files"] = frozen
        result["skill_digests"] = checkpoint.skill_digests
        result["composition_review"] = checkpoint.composition_review.model_dump() if checkpoint.composition_review else {}
        result["prompt_provenance"] = checkpoint.prompt_provenance
        result["reference_usage"] = "not_observable"
        result["evidence_complete"] = (bool(checkpoint.pages) and checkpoint.turn_completed
            and checkpoint.delivery.complete and bool(frozen) and bool(checkpoint.skill_digests)
            and all(page.image_prompt.strip() and page.reference_assets for page in checkpoint.pages))
        return result

    async def finish_request(self, request: Request):
        browser = await request.json()
        if self.collection_task is None:
            self.collection_task = asyncio.create_task(asyncio.to_thread(self.safe_finish, browser))
        return JSONResponse({"run_id": self.root.name}, status_code=202)

    async def result(self):
        return JSONResponse(self.report, status_code=200 if self.finished else 202)

    def safe_finish(self, browser):
        try:
            return self.finish(browser)
        except Exception as error:
            # A collection fault must leave a terminal, schema-valid failed
            # report, not an endless 202 or a fabricated empty DB pass.
            with self.lock:
                record = {"source": "finish", "error_type": type(error).__name__, "message": str(error)}
                write_json(self.root / "collection_failure.json", record)
                write_json(self.root / "browser.json", browser)
                self.report.update(finished_at=now(), elapsed_seconds=round(monotonic() - self.clock, 3),
                    execution_status="failed", auto_status="failed", error={"kind": "collection", "message": str(error)},
                    dimensions={key: "failed" for key in DIMENSIONS},
                    checks=[{"id": "collection_failed", "label": "真实证据采集失败", "status": "failed",
                        "detail": "没有可靠快照，不判为通过。", "evidence": ["collection_failure.json"]},
                        {"id": "eval_view", "label": "完整页面证据读取", "status": "needs_review",
                         "detail": "采集未完成，完整读取不能成立。", "evidence": ["browser.json"]}],
                    evidence_files=[{"name": name, "label": name} for name in
                                    ("source_hashes.json", "browser.json", "collection_failure.json")])
                write_json(self.root / "report.json", self.report)
                self.finished = True
                return self.report

    def finish(self, browser):
        with self.lock:
            if self.finished:
                return self.report
            doc, extras, errors = {}, {}, []
            if browser.get("session_id"):
                doc = self.app.state.chat.get(browser["session_id"])
                self.session_file = self.fixture.root / "sessions" / doc["id"] / "messages.json"
            after = self.fixture.state()
            evidence = collect(self.root, self.session_file, self.captured, self.fixture, doc, self.before, after, None)
            try:
                sdk, sdk_files = self.sdk_evidence()
                jobs = self.fixture.extractions.list()
                extras = {"sdk": sdk, "jobs": jobs, "source_manifest": self.source_manifest(jobs) if self.case["id"] == "S13" else {"verified": False, "files": []},
                    "outputs": self.output_evidence(after) if self.case["id"] in {"P01", "P02"} else {},
                    "execution_counts": self.fixture.execution_counts,
                    "extraction_prefix": self.fixture.extractions.root.relative_to(self.fixture.root).as_posix() + "/jobs/"}
            except Exception as error:
                errors.append({"source": "sdk_or_artifact", "error_type": type(error).__name__, "message": str(error)})
                sdk_files = []
                extras = {"sdk": {"events": [], "receipts": [], "image_generation_events": []}, "jobs": [],
                    "source_manifest": {"verified": False, "files": []}, "outputs": {"images": [], "receipt_completed": False, "evidence_complete": False},
                    "execution_counts": self.fixture.execution_counts, "extraction_prefix": ""}
            if self.case["id"] == "S13" and extras["jobs"]:
                evidence["final_answer"] = extras["jobs"][0].get("note", "")
                (self.root / "answer.txt").write_text(evidence["final_answer"], encoding="utf-8")
                self.report["model"] = {"name": "gpt-6-sol", "provider": "Codex Python SDK"}
            evidence["collection_errors"].extend(errors)
            counts = {"session_posts": sum(row["path"] == "/api/agent/sessions" for row in self.network),
                      "turn_posts": sum(row["path"].endswith("/turns") for row in self.network),
                      "merge_posts": sum(row["path"] == "/api/skill-extractions/merge" for row in self.network)}
            evidence.update(browser=browser, network=self.network, turns=self.turns, **extras,
                network_counts_ok=all(browser.get(key, 0) == value for key, value in counts.items()))
            for name, value in {**extras, "browser": browser, "network": self.network, "turns": self.turns,
                                 "collection_errors": evidence["collection_errors"]}.items():
                write_json(self.root / (name + ".json"), value)
            self.report.update(grade_slice(evidence, self.case["id"]))
            self.chain_grade = deepcopy(self.report)
            terminal_check = "sdk_completed" if self.case["id"] == "S13" else "agent_terminal"
            ended = next((row["status"] == "passed" for row in self.report["checks"] if row["id"] == terminal_check), False)
            if self.case["id"] in {"P01", "P02"}:
                ended &= next((row["status"] == "passed" for row in self.report["checks"] if row["id"] == "production_terminal"), False)
            self.report.update(execution_status="completed" if browser.get("completed") and ended and not errors else "failed",
                finished_at=now(), elapsed_seconds=round(monotonic() - self.clock, 3), session_id=doc.get("id"),
                error={"kind": "collection", "message": str(errors)} if errors else
                      {"kind": "browser", "message": str(browser.get("error"))} if browser.get("error") else None)
            if self.captured:
                usages = [r.get("usage") for r in self.captured.requests]
                if usages and all(row is not None for row in usages):
                    self.report["usage"] = {k: sum(row[k] for row in usages) for k in ("input_tokens", "output_tokens", "total_tokens")}
            self.report["codex_usage_evidence"] = "sdk.json"
            self.report["evidence_files"] = [{"name": p.name, "label": p.name} for p in sorted(self.root.iterdir())
                if p.is_file() and p.suffix in {".json", ".txt", ".md"} and p.name not in {"report.json", "review.json"}] + sdk_files
            self.report["checks"].append({"id": "eval_view", "label": "原Eval页真实完整证据读取", "status": "needs_review",
                "detail": "等待真实页面读取，不凭报告接口宣称页面通过。", "evidence": ["browser.json"]})
            write_json(self.root / "report.json", self.report)
            self.finished = True
            return self.report

    async def confirm_view(self, request: Request):
        payload = await request.json()
        with self.lock:
            if not self.finished or payload.get("run_id") != self.root.name:
                raise HTTPException(409, "报告未完成或对象不匹配。")
            if self.view_confirmed:
                return self.report
            candidate = deepcopy(self.report)
            view = next(row for row in candidate["checks"] if row["id"] == "eval_view")
            ok = payload.get("completed") is True and payload.get("evidence_loaded") is True
            view.update(status="passed" if ok else "failed", detail="真实Eval链路、数据库、原文证据已展开并读完。" if ok else str(payload.get("error", "未读完证据")))
            candidate["view_confirmation"] = {"status": "committed", "confirmed_at": now(), "payload": payload}
            if not ok:
                candidate.update(auto_status="failed", execution_status="failed", error={"kind": "eval_view", "message": view["detail"]})
                candidate["dimensions"]["task_success"] = "failed"
            write_json(self.root / "view_confirmation.json", payload)
            browser = json.loads((self.root / "browser.json").read_text(encoding="utf-8"))
            browser["eval_view"] = payload
            write_json(self.root / "browser.json", browser)
            if not any(item["name"] == "view_confirmation.json" for item in candidate["evidence_files"]):
                candidate["evidence_files"].append({"name": "view_confirmation.json", "label": "view_confirmation.json"})
            write_json(self.root / "report.json", candidate)
            self.report = candidate
            self.view_confirmed = True
            return candidate


def readonly_view(output_root, run_id):
    """Existing reports only: no World, DB connection, migration, or recovery."""
    from fastapi import FastAPI
    from creatoros.evaluation.store import EvalStore
    from creatoros.web.eval_routes import eval_routes
    from creatoros.web.static import mount_studio
    root = Path(output_root).resolve()
    if not root.is_dir() or not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("只读查看必须指定已有目录和合法Run。")
    store = EvalStore(root, cases_path=CASES_PATH)
    report = store.detail(run_id)
    if report.get("dataset_id") != "creatoros-workbench-v1":
        raise ValueError("该Run不属于工作台数据集。")
    app = FastAPI(title="CreatorOS Eval read-only viewer")
    app.state.view_case_id = report["case_id"]
    app.include_router(eval_routes(store))
    @app.middleware("http")
    async def get_only(request, call_next):
        if request.method != "GET":
            return JSONResponse({"error": "只读查看禁止执行、审核或修改。"}, status_code=405)
        return await call_next(request)
    @app.get("/api/health")
    def health():
        return {"status": "ok", "mode": "read_only", "run_id": run_id}
    mount_studio(app, PROJECT_ROOT / "web/dist")
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["serve", "freeze", "verify", "view"])
    parser.add_argument("--case", choices=sorted(CASES))
    parser.add_argument("--port", type=int, default=8892)
    parser.add_argument("--batch-id")
    parser.add_argument("--revision")
    parser.add_argument("--run-id")
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    args = parser.parse_args()
    if args.action == "view":
        if not args.run_id:
            parser.error("只读view必须指定已有run-id。")
        app = readonly_view(args.output_root, args.run_id)
        print(f"只读完整报告：http://127.0.0.1:{args.port}/eval?case={app.state.view_case_id}&run={args.run_id}", flush=True)
        import uvicorn
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
        return
    if args.action != "serve":
        value = freeze(args.revision) if args.action == "freeze" else verify(args.revision)
        print(value["revision"])
        return
    if not args.case or not args.batch_id or not args.revision:
        parser.error("真实付费GUI必须指定case、batch-id、revision（冻结且首尝试独占）。")
    host = WorkbenchEvaluation(args.case, batch_id=args.batch_id, revision=args.revision)
    print(f"隔离工作台 {args.case}: http://127.0.0.1:{args.port}；Run {host.root.name}", flush=True)
    import uvicorn
    try:
        uvicorn.run(host.app, host="127.0.0.1", port=args.port, log_level="warning")
    finally:
        try:
            if not host.finished:
                host.safe_finish({"completed": False, "error": "浏览器未完成；未自动重跑。", "page_errors": [], "posts_after_refresh": 0})
        finally:
            host.fixture.close()


if __name__ == "__main__":
    main()
