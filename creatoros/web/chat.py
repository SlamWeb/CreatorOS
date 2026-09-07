"""Web host for the existing Agent Loop; production remains owned by Studio."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import Event, RLock, Thread
from time import monotonic
from uuid import UUID, uuid4

from fastapi import HTTPException

from creatoros.agent.loop import run_agent
from creatoros.ai import DeepSeekProvider
from creatoros.ai.types import TextDelta
from creatoros.config import PROJECT_ROOT
from creatoros.context import RuntimeContext
from creatoros.session.snapshot import load_messages, new_messages, save_messages
from creatoros.terminal import Console

STUDIO_TOOLS = frozenset({"list_creators", "list_creator_series", "list_series_topics",
                          "start_content_run", "get_content_run"})
WEB_INSTRUCTIONS = (
    "你在 CreatorOS Studio 网页中帮助用户运营自有账号。只使用提供的工具，先查真实目录，不猜 ID。"
    "同名对象或多个候选不明确时先询问。只有用户明确要求生产才提交；提交不是完成，不轮询等待生图。"
    "本入口支持查询账号/栏目/选题、提交已有选题生产及查询 Run；"
    "新增或调整选题请引导使用页面的运营指令 Preview/人工确认入口。"
    "只根据 allowed_actions 建议后续操作，cancelled/approved 为只读终态，不可恢复或返工。"
    "批准/返工请打开 Run 页面，不声称已发布。不支持的能力如实说明。"
)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _uuid(value):
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(404, "对话不存在。") from None


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


class ChatStopped(Exception):
    pass


class WebConsole(Console):
    """One HTTP message in, then return; stream/events are rendered by the browser."""
    def __init__(self, text):
        prompts = iter([text, "/menu"])
        super().__init__(input_fn=lambda _: next(prompts))

    def write(self, *args, **kwargs):
        pass

    def render_event(self, event):
        pass


class AgentChatService:
    def __init__(self, root: Path, provider_factory=None):
        self.root = Path(root)
        self.provider_factory = provider_factory or self._provider
        self.lock = RLock()
        self.stopping = Event()
        self.thread = None
        self.active = None
        self.provider = None
        self.last_saved = 0.0

    @staticmethod
    def _provider():
        key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not key:
            raise HTTPException(503, "未配置 DeepSeek，目录与表单仍可使用。")
        return DeepSeekProvider(api_key=key, timeout_seconds=60, max_retries=0)

    def _path(self, session_id):
        return self.root / _uuid(session_id) / "view.json"

    def _save(self, doc):
        doc["updated_at"] = _now()
        _write(self._path(doc["id"]), doc)
        self.last_saved = monotonic()

    def start(self):
        # Called only after Studio acquired its execution ownership lock.
        with self.lock:
            self.stopping.clear()
            for path in self.root.glob("*/view.json"):
                doc = json.loads(path.read_text(encoding="utf-8"))
                if doc["status"] == "running":
                    doc["status"] = "interrupted"
                    doc["version"] += 1
                    doc["error"] = "服务重启，上一条指令未完整结束。请先查询已有任务，不会自动重跑。"
                    for entry in doc["entries"]:
                        if entry.get("kind") == "tool" and entry.get("status") == "running":
                            entry["status"] = "unknown"
                    self._repair(doc["id"])
                    self._save(doc)

    def _repair(self, session_id):
        path = self._path(session_id).with_name("messages.json")
        messages = load_messages(path)
        pending = {}
        for message in messages:
            for call in message.get("tool_calls", []):
                pending[call["id"]] = call
            if message.get("role") == "tool":
                pending.pop(message.get("tool_call_id"), None)
        for call_id in pending:
            messages.append({"role": "tool", "tool_call_id": call_id,
                             "content": "指令中断，工具结果未知，可能已生效。先查询已有状态，不要自动重试写入。"})
        if pending:
            save_messages(messages, path)

    def create(self):
        with self.lock:
            doc = {"id": str(uuid4()), "title": "新对话", "version": 0, "status": "idle",
                   "entries": [], "requests": [], "error": None, "updated_at": _now()}
            messages = new_messages()
            messages[0]["content"] += "\n\n" + WEB_INSTRUCTIONS
            save_messages(messages, self._path(doc["id"]).with_name("messages.json"))
            self._save(doc)
            return self._view(doc)

    def _read(self, session_id):
        path = self._path(session_id)
        if self.active is not None and self.active["id"] == session_id:
            return self.active
        if not path.is_file():
            raise HTTPException(404, "对话不存在。")
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _view(doc):
        # Full messages/tool results stay local; bounded presentation projection only.
        return deepcopy({k: v for k, v in doc.items() if k != "requests"} | {
            "entries": doc["entries"][-200:], "has_older": len(doc["entries"]) > 200})

    def get(self, session_id):
        with self.lock:
            return self._view(self._read(session_id))

    def list(self):
        with self.lock:
            docs = [self._read(p.parent.name) for p in self.root.glob("*/view.json")]
            return [{k: d[k] for k in ("id", "title", "status", "updated_at")}
                    for d in sorted(docs, key=lambda d: d["updated_at"], reverse=True)[:30]]

    def submit(self, session_id, request_id, text, version, studio_url):
        with self.lock:
            doc = self._read(session_id)
            for item in doc["requests"]:
                if item["id"] == request_id:
                    if item["text"] != text:
                        raise HTTPException(409, "同一请求 ID 不能用于不同指令。")
                    return self._view(doc)
            if self.stopping.is_set():
                raise HTTPException(503, "服务正在关闭。")
            if doc["version"] != version:
                raise HTTPException(409, "对话已变化，请查看最新消息后再发送。")
            if self.thread is not None and self.thread.is_alive():
                raise HTTPException(409, "Agent 正在处理一条指令，请完成后再发送。生产任务可继续后台运行。")
            provider = self.provider_factory()
            doc["requests"].append({"id": request_id, "text": text})
            doc["entries"].append({"kind": "user", "text": text})
            doc.update(status="running", error=None, version=doc["version"] + 1)
            if doc["title"] == "新对话":
                doc["title"] = text[:36]
            self.active, self.provider = doc, provider
            self._save(doc)
            self.thread = Thread(target=self._run, args=(doc, text, studio_url, provider), daemon=True)
            self.thread.start()
            return self._view(doc)

    def _emit(self, doc, event):
        with self.lock:
            if self.stopping.is_set():
                raise ChatStopped()
            entries = doc["entries"]
            if isinstance(event, TextDelta):
                if not entries or entries[-1]["kind"] != "assistant":
                    entries.append({"kind": "assistant", "text": ""})
                entries[-1]["text"] += event.content
                if monotonic() - self.last_saved > 0.5:
                    self._save(doc)
                return
            kind, data = event.kind, event.data
            if kind == "turn_start":
                entries.append({"kind": "assistant", "text": ""})
            elif kind == "tool_call":
                entries.append({"kind": "tool", "name": data["name"], "status": "running"})
            elif kind == "tool_result":
                entry = entries[-1]
                entry["status"] = "failed" if data.get("is_error") else "done"
                if data["name"] in {"start_content_run", "get_content_run"}:
                    try:
                        result = json.loads(data["content"])
                        entry["run_id"] = str(UUID(result.get("run_id", "")))
                        entry["run_status"] = result.get("status")
                    except (ValueError, TypeError, AttributeError):
                        pass
            elif kind == "model_usage":
                entries.append({"kind": "usage", **data})
            elif kind in {"context_compacted", "context_warning", "guard_stop"}:
                entries.append({"kind": kind, **data})
            self._save(doc)

    def _run(self, doc, text, studio_url, provider):
        status, error = "idle", None
        try:
            session_file = self._path(doc["id"]).with_name("messages.json")
            messages = load_messages(session_file)
            instructions = new_messages()[0]["content"] + "\n\n" + WEB_INSTRUCTIONS
            if messages[0].get("content") != instructions:
                # Host instructions are current configuration, not frozen conversation history.
                # Existing checkpoints fail their digest check and safely fall back to full history.
                messages[0]["content"] = instructions
                save_messages(messages, session_file)
            run_agent(provider, console=WebConsole(text),
                      session_file=session_file,
                      runtime_context=RuntimeContext(project_root=PROJECT_ROOT, studio_url=studio_url,
                                                     allowed_tools=STUDIO_TOOLS),
                      on_stream_event=lambda e: self._emit(doc, e) if isinstance(e, TextDelta) else None,
                      on_agent_event=lambda e: self._emit(doc, e))
        except ChatStopped:
            status, error = "interrupted", "服务关闭，指令已中断；请先查看已有任务。"
        except Exception:
            status, error = "failed", "Agent 请求未完整结束。请先查看已有任务，再决定下一步；未自动重试。"
        finally:
            try:
                provider.client.close()
            finally:
                with self.lock:
                    if self.stopping.is_set():
                        status, error = "interrupted", "服务关闭，指令已中断；请先查看已有任务。"
                    self._repair(doc["id"])
                    doc.update(status=status, error=error, version=doc["version"] + 1)
                    for entry in doc["entries"]:
                        if entry.get("kind") == "tool" and entry.get("status") == "running":
                            entry["status"] = "unknown"
                    self._save(doc)
                    self.active, self.provider = None, None

    def shutdown(self):
        self.stopping.set()
        with self.lock:
            if self.active is not None:
                self.active.update(status="interrupted", error="服务关闭，指令中断；不会自动重试。")
                self._save(self.active)
            provider, thread = self.provider, self.thread
        if provider is not None:
            provider.client.close()
        if thread is not None:
            thread.join(timeout=5)
