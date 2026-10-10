"""Opt-in live browser Eval host. Never submits an Agent turn itself.

Only this isolated host exposes /__live_eval__; the production app does not.
The browser drives the normal UI. The controller only collects and grades.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
from pathlib import Path
import subprocess
from time import monotonic
from threading import RLock
from uuid import uuid4
from copy import deepcopy

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from creatoros.config import PROJECT_ROOT
from creatoros.context import RuntimeContext
from creatoros.evaluation.fixture import E01Fixture, FIXTURE_VERSION
from creatoros.evaluation.grader import grade_e01
from creatoros.evaluation.run import CapturedProvider, collect, now, source_fingerprint, write_json
from creatoros.session.request_trace import redact
from creatoros.tools.studio import get_content_run, get_topic_research, list_creators, list_series_topics
from creatoros.tools.content_discussion import get_creator_tasks
from creatoros.tools.definitions import tool_registry
from creatoros.web.chat import ACCOUNT_TOOLS, AgentChatService


VIEW_PROTOCOL = "browser-evidence-v2"


class BrowserCapturedProvider(CapturedProvider):
    def __init__(self, provider, path, session_file, fixture):
        super().__init__(provider, path, session_file)
        self.fixture = fixture

    def begin(self, context, method):
        record = super().begin(context, method)
        self.fixture.record_request_tree(record["trace_request_id"])
        return record


class BrowserEvaluation:
    def __init__(self, case_id, output_root=None, *, batch_id=None, phase=None, variant=None):
        dataset = json.loads((PROJECT_ROOT / "docs/agent-eval/cases.json").read_text(encoding="utf-8"))
        if case_id not in {f"E{index:02}" for index in range(1, 13)}:
            raise ValueError("未知账号评测题。")
        self.case = next(row for row in dataset["cases"] if row["id"] == case_id)
        self.root = Path(output_root or PROJECT_ROOT / "data/agent-eval").resolve() / uuid4().hex
        self.root.mkdir(parents=True, exist_ok=False)
        self.clock = monotonic()
        self.captured = None
        self.session_file = None
        self.finished = False
        self.lock = RLock()
        self.network_lock = RLock()
        self.chain_status = None
        self.chain_task_status = None
        self.view_confirmed = False
        self.collection_task = None
        self.network = []
        self.turns = []
        self.controller_events = []
        self.event_tasks = {}
        self.message_start = 0
        self.request_start = 0
        self.bound_session_id = None
        self.capture_message_start = 0
        self.variant = variant
        self.batch_id = batch_id
        self.phase = phase
        if batch_id:
            from creatoros.evaluation.batch import claim
            self.claim = claim(self.root.parent, batch_id, phase, case_id, variant, self.root.name)
        hashes, _ = source_fingerprint()
        # Include shipped frontend bytes, not just Python, in browser provenance.
        for path in sorted((PROJECT_ROOT / "web/dist").rglob("*")):
            if path.is_file():
                hashes[path.relative_to(PROJECT_ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        write_json(self.root / "source_hashes.json", hashes)
        fingerprint = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
        self.report = {"schema_version": 1, "run_id": self.root.name, "case_id": case_id,
            "dataset_id": dataset["dataset_id"], "git_sha": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True).strip(),
            "fixture_version": FIXTURE_VERSION + "-browser-v1", "started_at": now(),
            "finished_at": now(), "code_fingerprint": fingerprint,
            "dataset_sha256": hashes["docs/agent-eval/cases.json"], "execution_mode": "live",
            "entrypoint": "browser", "execution_status": "failed",
            "model": {"name": "unavailable", "provider": "DeepSeek"}, "usage": None,
            "elapsed_seconds": 0, "error": None, "review": None, "manual_checks": self.case["manual_checks"],
            "auto_status": "needs_review", "checks": [{"id": "browser_start", "label": "浏览器评测尚未完成",
                "status": "needs_review", "detail": "尚无完整浏览器链路，不记为通过。", "evidence": ["source_hashes.json"]}],
            "dimensions": {key: "needs_review" for key in ("task_success", "boundary_enforced", "state_consistent", "protocol_valid")},
            "evidence_files": [{"name": "source_hashes.json", "label": "Source hashes"}]}
        self.report.update(dataset_revision=dataset.get("revision"), batch_id=batch_id,
                           phase=phase, variant=variant, collection_protocol=VIEW_PROTOCOL)
        if batch_id:
            self.report["freeze_claim"] = self.claim
        # Even environment/setup failures leave a discoverable report.
        write_json(self.root / "report.json", self.report)
        write_json(self.root / "view_confirmation.json", {"protocol": VIEW_PROTOCOL,
            "run_id": self.root.name, "status": "pending", "authority": "report.json.view_confirmation"})
        try:
            self.fixture = E01Fixture(self.root / "fixture", self.provider, case_id=case_id, eval_root=self.root.parent)
            self.before = self.fixture.state()
        except Exception as error:
            if hasattr(self, "fixture"):
                self.fixture.close()
            self.report.update(finished_at=now(), auto_status="failed",
                               error={"kind": "setup", "message": str(error)})
            write_json(self.root / "report.json", self.report)
            raise
        variables = self.fixture.variables() if hasattr(self.fixture, "variables") else {
            key.replace("_id", "_b_id"): value for key, value in (self.fixture.foreign_ids or {}).items()}
        self.steps = deepcopy(self.case["steps"])
        for step in self.steps:
            if step.get("kind") == "user":
                for key, value in variables.items():
                    step["text"] = step["text"].replace("{{" + key + "}}", str(value))
                if "{{" in step["text"]:
                    raise ValueError("未解析的题目变量，不允许发给真实模型。")
        self.query = next(step["text"] for step in self.steps if step["kind"] == "user")
        write_json(self.root / "case.json", self.case)
        self.app = self.fixture.app
        router = APIRouter()
        router.get("/__live_eval__/scenario")(self.scenario)
        router.post("/__live_eval__/finish")(self.finish_request)
        router.get("/__live_eval__/result")(self.result)
        router.get("/__live_eval__/observation")(self.observation)
        router.post("/__live_eval__/view")(self.confirm_view)
        router.post("/__live_eval__/checkpoint")(self.checkpoint_request)
        router.post("/__live_eval__/event")(self.event_request)
        router.get("/__live_eval__/event-result/{event_id}")(self.event_result)
        # create_app already installed the SPA catch-all. Test-only routes must
        # precede it, or GET scenario silently returns index.html with HTTP 200.
        self.app.router.routes[0:0] = router.routes
        self.app.middleware("http")(self.observe_http)

    async def observe_http(self, request, call_next):
        # Test host only: no body, headers, credentials or model-side injection.
        skill_path = request.url.path.startswith("/api/producer-skills/")
        scoped = bool(request.headers.get("x-creatoros-agent-session"))
        if self.case["id"] == "E12" and skill_path and scoped and request.method == "PUT":
            skill_id = request.url.path.split("/")[3]
            self.fixture.controls.before_skill_update(skill_id)
        if not (request.url.path.startswith("/api/agent/") or scoped):
            return await call_next(request)
        with self.network_lock:
            row = {"method": request.method, "path": request.url.path, "at": now(),
                   "controller": request.headers.get("x-creatoros-eval-controller") == "1"}
            self.network.append(row)
        response = await call_next(request)
        row["status"] = response.status_code
        if (self.case["id"] == "E12" and skill_path and scoped and request.method == "GET"
                and request.url.path.endswith("/files/text") and response.status_code == 200):
            self.fixture.controls.on_skill_read(request.url.path.split("/")[3])
        return response

    def observation(self):
        with self.network_lock:
            return {"sessions": self.app.state.chat.list(), "network": list(self.network),
                    "model_requests": len(self.captured.requests) if self.captured else 0}

    def provider(self):
        files = list((self.fixture.root / "sessions").glob("*/messages.json"))
        # Provider is constructed before submit marks the session active. The
        # first GUI-created session excludes explicit sibling/control sessions.
        seeded = set(getattr(self.fixture, "seeded_session_ids", []))
        candidates = [path for path in files if path.parent.name not in seeded]
        if self.bound_session_id:
            candidates = [path for path in files if path.parent.name == self.bound_session_id]
        if len(candidates) != 1:
            raise ValueError("无法唯一绑定本次浏览器会话；不自动重试模型。")
        self.session_file = candidates[0]
        self.bound_session_id = self.session_file.parent.name
        provider = AgentChatService._provider()  # Real configured DeepSeek, max_retries=0.
        current = BrowserCapturedProvider(provider, self.root / "requests.json", self.session_file, self.fixture)
        if self.captured is None:
            self.captured = current
        else:
            current.requests = self.captured.requests
            current.transport = self.captured.transport
        self.report["model"] = {"name": provider.model, "provider": type(provider).__name__}
        return current

    def scenario(self):
        # No oracle/private markers/checklist goes into the model's user input.
        return {"case_id": self.case["id"], "run_id": self.root.name,
                "creator_name": self.fixture.expected_creator["display_name"],
                "creator_id": self.fixture.creator_a, "query": self.query,
                "steps": self.steps, "variant": self.variant}

    async def checkpoint_request(self, request: Request):
        payload = await request.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("session_id"), str):
            raise HTTPException(422, "缺少 checkpoint 会话。")
        # Collection only; never invokes the Provider or submits user text.
        return await asyncio.to_thread(self.checkpoint, payload)

    def checkpoint(self, browser):
        with self.lock:
            session_id = browser["session_id"]
            doc = self.app.state.chat.get(session_id)
            if doc.get("creator_id") != self.fixture.creator_a:
                raise ValueError("checkpoint 账号不匹配")
            self.bound_session_id = session_id
            self.session_file = self.fixture.root / "sessions" / session_id / "messages.json"
            folder = self.root / "turns" / str(len(self.turns) + 1).zfill(2)
            folder.mkdir(parents=True, exist_ok=False)
            extras = self.extra_evidence()
            evidence = deepcopy(collect(folder, self.session_file, self.captured, self.fixture,
                doc, self.turns[-1]["after"] if self.turns else self.before, self.fixture.state(), None))
            evidence.update(turn_message_start=self.message_start, request_start=self.request_start,
                            browser=browser, controller_events=deepcopy(self.controller_events), **extras)
            self.message_start = len(evidence["messages"])
            self.request_start = len(evidence["requests"])
            self.turns.append(evidence)
            write_json(self.root / "turns.json", self.turns)
            return {"turn_index": len(self.turns), "status": doc.get("status"),
                    "request_count": self.request_start, "message_count": self.message_start}

    async def event_request(self, request: Request):
        payload = await request.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("name"), str):
            raise HTTPException(422, "控制事件无效。")
        allowed = {step["name"] for step in self.steps if step.get("kind") == "event"}
        if payload["name"] not in allowed:
            raise HTTPException(422, "不是该冻结题目的控制事件。")
        event_id = uuid4().hex
        base = str(request.base_url).rstrip("/")
        # Release the original POST ownership lock before nested real HTTP.
        self.event_tasks[event_id] = asyncio.create_task(asyncio.to_thread(self.execute_event, payload, base))
        return JSONResponse({"event_id": event_id}, status_code=202)

    async def event_result(self, event_id):
        task = self.event_tasks.get(event_id)
        if task is None:
            raise HTTPException(404, "控制事件不存在。")
        if not task.done():
            return JSONResponse({"status": "running"}, status_code=202)
        try:
            return task.result()
        except Exception as error:
            return JSONResponse({"status": "failed", "error_type": type(error).__name__,
                                 "message": str(error)}, status_code=500)

    def execute_event(self, payload, base):
        name = payload["name"]
        session_id = payload.get("session_id") or self.bound_session_id or None
        if name == "reload_service":
            chat = self.app.state.chat
            chat.shutdown()
            if chat.active:
                raise ValueError("隔离对话未停止，不能伪称重载完成。")
            replacement = AgentChatService(chat.root, self.provider,
                creator_lookup=chat.creator_lookup, creator_context_factory=chat.creator_context_factory,
                research_wait_timeout_seconds=chat.research_wait_timeout_seconds)
            # Existing route/guard closures retain this object; replace all
            # runtime state with a fresh service, then read the same disk.
            chat.__dict__ = replacement.__dict__
            chat.start()
            if self.case["id"] == "E06":
                path = self.fixture.root / "sessions" / session_id / "messages.json"
                self.capture_message_start = len(json.loads(path.read_text(encoding="utf-8")))
                self.message_start = self.capture_message_start
            record = {"name": name, "kind": "chat_service_rebuild", "process_restart": False,
                      "session_id": session_id, "session": chat.get(session_id), "status": "completed"}
        else:
            record = self.fixture.controls.execute(name, session_id, base, payload)
        self.controller_events.append(record)
        if record.get("seeded_session_id"):
            self.bound_session_id = record["seeded_session_id"]
        write_json(self.root / "controller_events.json", self.controller_events)
        return record

    def extra_evidence(self):
        controls = self.fixture.controls
        extras = {"capture_message_start": self.capture_message_start}
        if self.case["id"] in {"E08", "E09"}:
            research = controls.research_results()
            extras.update(research_records=research["research_records"],
                          codex_evidence=research["codex_evidence"], research_details=research)
        if self.case["id"] == "E12":
            files = controls.skill_files()
            extras.update(skill_files=files)
        if self.case["id"] == "E05":
            extras["sibling_session_files"] = controls.sibling_session_files()
        return deepcopy(extras)

    async def finish_request(self, request: Request):
        payload = await request.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("session_id"), str):
            raise HTTPException(422, "缺少浏览器会话标识。")
        # Production middleware holds the scope lock for POST. Awaiting loopback
        # scoped probes here would deadlock, even if finish used a worker thread.
        # Return 202, release that lock, then collect; GET only reads the result.
        if self.collection_task is None:
            self.collection_task = asyncio.create_task(asyncio.to_thread(
                self.finish, payload, str(request.base_url).rstrip("/")))
        return JSONResponse({"run_id": self.root.name}, status_code=202)

    async def result(self):
        if not self.finished:
            return JSONResponse({"run_id": self.root.name, "status": "collecting"}, status_code=202)
        return redact(self.report)[0]

    def finish(self, browser, base):
        with self.lock:
            return self._finish(browser, base)

    def _finish(self, browser, base):
        if self.finished:
            return redact(self.report)[0]
        doc, evidence, probe = {}, {}, None
        extras = {}
        collection_errors = []
        try:
            doc = self.app.state.chat.get(browser["session_id"])
            if doc["status"] == "running":
                # An unfinished timeout is not a successful model task.
                self.app.state.chat.shutdown()
                doc = self.app.state.chat.get(browser["session_id"])
                raise ValueError("浏览器观察结束时会话仍在运行；未记为通过。")
            if doc.get("creator_id") != self.fixture.creator_a:
                raise ValueError("浏览器会话不是本题绑定账号。")
            extras = self.extra_evidence()
            context = RuntimeContext(project_root=self.fixture.root, studio_url=base,
                allowed_tools=ACCOUNT_TOOLS, archive_only_reads=True,
                creator_id=self.fixture.creator_a, agent_session_id=doc["id"], session_file=self.session_file)
            if self.case["id"] == "E01":
                result = list_creators(offset=0, limit=100, context=context)
                probe = {"name": "list_creators", "arguments": {"offset": 0, "limit": 100},
                         "content": result.content, "is_error": result.is_error}
            elif self.case["id"] == "E02":
                ids = self.fixture.foreign_ids
                probe = []
                for tool, arguments in (
                    (list_series_topics, {"series_id": ids["series_id"], "state": "all"}),
                    (get_topic_research, {"batch_id": ids["batch_id"]}),
                    (get_content_run, {"run_id": ids["run_id"]}),
                    (get_creator_tasks, {"creator_id": ids["creator_id"]}),
                ):
                    result = tool(context=context, **arguments)
                    probe.append({"tool": tool.__name__, "arguments": arguments,
                                  "result": {"content": result.content, "is_error": result.is_error}})
            elif self.case.get("boundary_probes"):
                probe = []
                variables = self.fixture.variables()
                for check in self.case["boundary_probes"]:
                    raw = json.dumps(check["arguments"], ensure_ascii=False)
                    for key, value in variables.items():
                        raw = raw.replace("{{" + key + "}}", str(value).replace("\\", "\\\\"))
                    tool = tool_registry[check["tool"]]
                    arguments = tool.parse_arguments(raw)
                    result = tool.execute(context=context, **arguments)
                    probe.append({"tool": check["tool"], "arguments": arguments,
                        "result": {"content": result.content, "is_error": result.is_error,
                                   "error_type": result.error_type}})
            evidence = collect(self.root, self.session_file, self.captured, self.fixture,
                               doc, self.before, self.fixture.state(), probe)
        except Exception as error:
            self.report["error"] = {"kind": "browser_or_evidence", "message": str(error)}
            collection_errors.append({"source": "browser_extra", "error_type": type(error).__name__})
            try:
                state = self.fixture.state()
            except Exception as state_error:
                state = {}
                collection_errors.append({"source": "state", "error_type": type(state_error).__name__})
            evidence = collect(self.root, self.session_file, self.captured, self.fixture,
                               doc, self.before, state, probe)
        evidence["collection_errors"].extend(collection_errors)
        write_json(self.root / "collection_errors.json", evidence["collection_errors"])
        controls = deepcopy(self.controller_events) + list(self.fixture.controls.hook_events)
        unique = {json.dumps(row, sort_keys=True, default=str): row for row in controls}
        evidence.update(turns=deepcopy(self.turns), **extras, controller_events=list(unique.values()))
        for name, value in extras.items():
            write_json(self.root / (name + ".json"), value)
        write_json(self.root / "turns.json", self.turns)
        write_json(self.root / "controller_events.json", evidence["controller_events"])
        from creatoros.evaluation.grader_suite import grade_case
        grade = grade_case(evidence, self.case["id"])
        self.report.update(grade)
        answer = evidence.get("final_answer", "")
        queries = [step["text"] for step in self.steps if step["kind"] == "user"]
        expected_session_posts = 0 if self.case["id"] == "E06" else 1
        browser_ok = (browser.get("completed") is True and browser.get("turn_posts") == len(queries)
            and browser.get("session_posts") == expected_session_posts and browser.get("posts_after_refresh") == 0
            and isinstance(browser.get("copied_reply"), str)
            and browser["copied_reply"].replace("\r\n", "\n") == answer and bool(answer)
            and browser.get("visible_reply") == browser.get("restored_reply")
            and bool(browser.get("visible_reply")) and browser.get("trace_visible") is True
            and browser.get("query") == self.query and browser.get("restored_session_id") == doc.get("id"))
        messages = evidence.get("messages", [])
        actual_users = [row.get("content") for row in messages if row.get("role") == "user"]
        original = getattr(self.fixture, "interrupted_original_text", None)
        expected_users = ([original] if original else []) + queries
        browser_ok &= actual_users == expected_users
        browser["controller_checks"] = {"browser_ok": browser_ok, "exact_user_turns":
            actual_users == expected_users,
            "clipboard_comparison": "仅将 Windows CRLF 归一为 LF；Markdown/正文不作改写。"}
        write_json(self.root / "network.json", self.network)
        session_posts = [row for row in self.network if row["method"] == "POST"
                         and row["path"] == "/api/agent/sessions"]
        turn_posts = [row for row in self.network if row["method"] == "POST"
                      and row["path"].endswith("/turns")]
        # Explicit controller replays are stored separately; they are not GUI
        # sends or additional real model turns. Header marks only test actions.
        model_posts = [row for row in turn_posts if not row.get("controller")]
        session_posts = [row for row in session_posts if not row.get("controller")]
        network_ok = (len(session_posts) == browser.get("session_posts") == expected_session_posts
                      and len(model_posts) == browser.get("turn_posts") == len(queries))
        browser_ok &= network_ok
        browser["controller_checks"].update(browser_ok=browser_ok, actual_http_counts=network_ok)
        write_json(self.root / "browser.json", browser)
        self.report["checks"].append({"id": "browser_http_counts", "label": "实际聊天 HTTP 与页面操作一致",
            "status": "passed" if network_ok else "failed", "detail": "测试宿主观察实际请求，不读取正文或鉴权头。",
            "evidence": ["network.json", "browser.json"]})
        self.report["checks"].append({"id": "browser_e2e", "label": "浏览器发送、完整回复、刷新与 Trace",
            "status": "passed" if browser_ok else "failed", "detail": "真实页面操作；全部用户轮次对应冻结步骤，复制原文与账本逐字对照，刷新零重提。",
            "evidence": ["browser.json", "messages.json", "answer.txt"]})
        terminal = next((row for row in self.report["checks"] if row["id"] == "execution_completed"), {})
        completed = browser_ok and terminal.get("status") == "passed" and self.report["error"] is None
        self.report["execution_status"] = "completed" if completed else "failed"
        if not completed:
            self.report["auto_status"] = "failed"
            self.report["dimensions"]["task_success"] = "failed"
            self.report["error"] = self.report["error"] or {"kind": "browser_or_model",
                "message": browser.get("error") or doc.get("error") or "浏览器/模型结束断言未全部通过。"}
        self.report["session_id"] = doc.get("id")
        if browser.get("delivery", {}).get("protocol") == "host-links-v1":
            from .navigation import navigation_result
            delivered = navigation_result(doc, browser["delivery"], self.case["id"], completed)
            write_json(self.root / "delivery.json", delivered)
            self.report["delivery"] = {"protocol": "host-links-v1", "status": delivered["status"],
                "evidence": "delivery.json", "model_prose_changed": False}
        self.chain_status = self.report["auto_status"]
        self.chain_task_status = self.report["dimensions"]["task_success"]
        self.report["checks"].append({"id": "browser_eval_view", "label": "浏览器 Eval 末端结果显示",
            "status": "needs_review", "detail": "尚未完成报告页面验收；不会默认为通过。", "evidence": ["browser.json"]})
        if self.report["auto_status"] == "passed":
            self.report["auto_status"] = "needs_review"
        if self.report["dimensions"]["task_success"] == "passed":
            self.report["dimensions"]["task_success"] = "needs_review"
        usages = [row["usage"] for row in self.captured.requests] if self.captured else []
        if usages and all(item is not None for item in usages):
            self.report["usage"] = {key: sum(row[key] for row in usages)
                for key in ("input_tokens", "output_tokens", "total_tokens")}
        self.report.update(finished_at=now(), elapsed_seconds=round(monotonic() - self.clock, 3))
        self.report["evidence_files"] = [{"name": path.name, "label": path.name}
            for path in sorted(self.root.iterdir()) if path.is_file() and path.suffix in {".json", ".txt", ".md"}
            and path.name not in {"report.json", "review.json"}]
        write_json(self.root / "report.json", self.report)
        self.finished = True
        return redact(self.report)[0]

    async def confirm_view(self, request: Request):
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(422, "页面验收必须为对象。")
        with self.lock:
            if not self.finished or payload.get("run_id") != self.root.name:
                raise HTTPException(409, "报告对象不匹配，未保存验收。")
            if self.view_confirmed:
                return redact(self.report)[0]
            ok = payload.get("completed") is True and payload.get("posts_after_refresh") == 0
            # Build a candidate, so a failed write cannot upgrade in-memory state
            # or mark an uncommitted view as confirmed. The report is authoritative.
            candidate = deepcopy(self.report)
            check = next(row for row in candidate["checks"] if row["id"] == "browser_eval_view")
            check.update(status="passed" if ok else "failed", detail="实际打开 Eval、读取完整链路及数据库结果；刷新仍为同一 Run。" if ok else "Eval 页面验收失败：" + str(payload.get("error", "未完成")))
            candidate["auto_status"] = self.chain_status if ok else "failed"
            candidate["dimensions"]["task_success"] = self.chain_task_status if ok else "failed"
            if not ok:
                candidate["execution_status"] = "failed"
                candidate["error"] = {"kind": "browser_eval_view", "message": str(payload.get("error", "末端页面未完成"))}
            candidate.update(finished_at=now(), elapsed_seconds=round(monotonic() - self.clock, 3))
            candidate["view_confirmation"] = {"protocol": VIEW_PROTOCOL, "status": "committed",
                "payload": payload, "confirmed_at": now()}
            receipt = {"protocol": VIEW_PROTOCOL, "run_id": self.root.name,
                "status": "submitted", "received_at": now(), "payload": payload,
                "authority": "report.json.view_confirmation"}
            stage = "receipt"
            try:
                write_json(self.root / "view_confirmation.json", receipt)
                stage = "browser_evidence"
                browser = json.loads((self.root / "browser.json").read_text(encoding="utf-8"))
                browser["eval_view"] = payload
                write_json(self.root / "browser.json", browser)
                stage = "report_commit"
                write_json(self.root / "report.json", candidate)
            except Exception as error:
                diagnostic = {**receipt, "status": "failed", "stage": stage,
                    "error_type": type(error).__name__, "errno": getattr(error, "errno", None),
                    "winerror": getattr(error, "winerror", None), "message": redact(str(error))[0],
                    "failed_at": now()}
                try:
                    write_json(self.root / "view_confirmation.json", diagnostic)
                except Exception as diagnostic_error:
                    logging.getLogger(__name__).error("Eval view persistence diagnostic unavailable: %s", type(diagnostic_error).__name__)
                return JSONResponse({"error": "eval_view_persistence_failed", "run_id": self.root.name,
                    "stage": stage, "error_type": type(error).__name__,
                    "message": "页面验收未提交；原报告保留，不能记为成功。"}, status_code=503)
            self.report = candidate
            self.view_confirmed = True
            return redact(self.report)[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=[f"E{index:02}" for index in range(1, 13)], required=True)
    parser.add_argument("--port", type=int, default=8878)
    parser.add_argument("--batch-id")
    parser.add_argument("--phase", choices=["baseline", "regression"])
    parser.add_argument("--variant", choices=["failed", "unknown"])
    args = parser.parse_args()
    host = BrowserEvaluation(args.case, batch_id=args.batch_id, phase=args.phase, variant=args.variant)
    print(f"真实浏览器评测 {args.case}：http://127.0.0.1:{args.port}；Run {host.root.name}", flush=True)
    import uvicorn
    try:
        uvicorn.run(host.app, host="127.0.0.1", port=args.port, log_level="warning")
    finally:
        try:
            if not host.finished:
                files = list((host.fixture.root / "sessions").glob("*/messages.json"))
                host.finish({"session_id": files[0].parent.name if len(files) == 1 else "",
                             "completed": False, "error": "浏览器未完成验收，服务关闭；未自动重跑。"},
                            f"http://127.0.0.1:{args.port}")
        finally:
            host.fixture.close()


if __name__ == "__main__":
    main()
