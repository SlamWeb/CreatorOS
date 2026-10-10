"""Opt-in live browser Eval host. Never submits an Agent turn itself.

Only this isolated host exposes /__live_eval__; the production app does not.
The browser drives the normal UI. The controller only collects and grades.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
from time import monotonic
from threading import RLock
from uuid import uuid4

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
from creatoros.web.chat import ACCOUNT_TOOLS, AgentChatService


class BrowserEvaluation:
    def __init__(self, case_id, output_root=None):
        dataset = json.loads((PROJECT_ROOT / "docs/agent-eval/cases.json").read_text(encoding="utf-8"))
        if case_id not in {"E01", "E02"}:
            raise ValueError("浏览器真实评测目前只接线 E01/E02。")
        self.case = next(row for row in dataset["cases"] if row["id"] == case_id)
        self.root = Path(output_root or PROJECT_ROOT / "data/agent-eval").resolve() / uuid4().hex
        self.root.mkdir(parents=True, exist_ok=False)
        self.clock = monotonic()
        self.captured = None
        self.session_file = None
        self.finished = False
        self.lock = RLock()
        self.chain_status = None
        self.chain_task_status = None
        self.view_confirmed = False
        self.collection_task = None
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
        # Even environment/setup failures leave a discoverable report.
        write_json(self.root / "report.json", self.report)
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
        self.query = self.case["steps"][0]["text"]
        for key, value in (self.fixture.foreign_ids or {}).items():
            self.query = self.query.replace("{{" + key.replace("_id", "_b_id") + "}}", value)
        self.app = self.fixture.app
        router = APIRouter()
        router.get("/__live_eval__/scenario")(self.scenario)
        router.post("/__live_eval__/finish")(self.finish_request)
        router.get("/__live_eval__/result")(self.result)
        router.post("/__live_eval__/view")(self.confirm_view)
        # create_app already installed the SPA catch-all. Test-only routes must
        # precede it, or GET scenario silently returns index.html with HTTP 200.
        self.app.router.routes[0:0] = router.routes

    def provider(self):
        files = list((self.fixture.root / "sessions").glob("*/messages.json"))
        if self.captured is not None or len(files) != 1:
            raise ValueError("本次真实评测只允许一个浏览器会话；不自动重试模型。")
        self.session_file = files[0]
        provider = AgentChatService._provider()  # Real configured DeepSeek, max_retries=0.
        self.captured = CapturedProvider(provider, self.root / "requests.json", self.session_file)
        self.report["model"] = {"name": provider.model, "provider": type(provider).__name__}
        return self.captured

    def scenario(self):
        # No oracle/private markers/checklist goes into the model's user input.
        return {"case_id": self.case["id"], "run_id": self.root.name,
                "creator_name": self.fixture.expected_creator["display_name"],
                "creator_id": self.fixture.creator_a, "query": self.query}

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
        try:
            doc = self.app.state.chat.get(browser["session_id"])
            if doc["status"] == "running":
                # An unfinished timeout is not a successful model task.
                self.app.state.chat.shutdown()
                doc = self.app.state.chat.get(browser["session_id"])
                raise ValueError("浏览器观察结束时会话仍在运行；未记为通过。")
            if doc.get("creator_id") != self.fixture.creator_a:
                raise ValueError("浏览器会话不是本题绑定账号。")
            context = RuntimeContext(project_root=self.fixture.root, studio_url=base,
                allowed_tools=ACCOUNT_TOOLS, archive_only_reads=True,
                creator_id=self.fixture.creator_a, agent_session_id=doc["id"])
            if self.case["id"] == "E01":
                result = list_creators(offset=0, limit=100, context=context)
                probe = {"name": "list_creators", "arguments": {"offset": 0, "limit": 100},
                         "content": result.content, "is_error": result.is_error}
            else:
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
            evidence = collect(self.root, self.session_file, self.captured, self.fixture,
                               doc, self.before, self.fixture.state(), probe)
        except Exception as error:
            self.report["error"] = {"kind": "browser_or_evidence", "message": str(error)}
            evidence = collect(self.root, self.session_file, self.captured, self.fixture,
                               doc, self.before, self.fixture.state(), probe)
        if self.case["id"] == "E02":
            from creatoros.evaluation.grader_e02 import grade_e02
            grade = grade_e02(evidence)
        else:
            grade = grade_e01(evidence)
        self.report.update(grade)
        answer = evidence.get("final_answer", "")
        browser_ok = (browser.get("completed") is True and browser.get("turn_posts") == 1
            and browser.get("session_posts") == 1 and browser.get("posts_after_refresh") == 0
            and isinstance(browser.get("copied_reply"), str)
            and browser["copied_reply"].replace("\r\n", "\n") == answer and bool(answer)
            and browser.get("visible_reply") == browser.get("restored_reply")
            and bool(browser.get("visible_reply")) and browser.get("trace_visible") is True
            and browser.get("query") == self.query and browser.get("restored_session_id") == doc.get("id"))
        messages = evidence.get("messages", [])
        browser_ok &= [row.get("content") for row in messages if row.get("role") == "user"] == [self.query]
        browser["controller_checks"] = {"browser_ok": browser_ok, "exactly_one_real_user_turn":
            [row.get("content") for row in messages if row.get("role") == "user"] == [self.query],
            "clipboard_comparison": "仅将 Windows CRLF 归一为 LF；Markdown/正文不作改写。"}
        write_json(self.root / "browser.json", browser)
        self.report["checks"].append({"id": "browser_e2e", "label": "浏览器发送、完整回复、刷新与 Trace",
            "status": "passed" if browser_ok else "failed", "detail": "真实页面操作；复制原文与账本逐字对照，刷新零重提。",
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
            for path in sorted(self.root.iterdir()) if path.is_file() and path.suffix in {".json", ".txt"}
            and path.name != "report.json"]
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
            browser = json.loads((self.root / "browser.json").read_text(encoding="utf-8"))
            browser["eval_view"] = payload
            write_json(self.root / "browser.json", browser)
            check = next(row for row in self.report["checks"] if row["id"] == "browser_eval_view")
            check.update(status="passed" if ok else "failed", detail="实际打开 Eval、读取完整链路及数据库结果；刷新仍为同一 Run。" if ok else "Eval 页面验收失败：" + str(payload.get("error", "未完成")))
            self.report["auto_status"] = self.chain_status if ok else "failed"
            self.report["dimensions"]["task_success"] = self.chain_task_status if ok else "failed"
            if not ok:
                self.report["execution_status"] = "failed"
                self.report["error"] = {"kind": "browser_eval_view", "message": str(payload.get("error", "末端页面未完成"))}
            self.report.update(finished_at=now(), elapsed_seconds=round(monotonic() - self.clock, 3))
            write_json(self.root / "report.json", self.report)
            self.view_confirmed = True
            return redact(self.report)[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=["E01", "E02"], required=True)
    parser.add_argument("--port", type=int, default=8878)
    args = parser.parse_args()
    host = BrowserEvaluation(args.case)
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
