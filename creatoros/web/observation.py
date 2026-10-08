"""Local projections of recorded business identity and public execution evidence.

No execution services are started here. IDs contain typed references, never paths.
Missing historical evidence remains missing; timestamps never establish a link.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from fastapi import HTTPException
from sqlalchemy import inspect, select

from creatoros.integrations.codex_public_events import sanitize_public
from creatoros.storage import (ContentAttempt, ContentRevision, ContentRun, ContentRunEvent,
                               Creator, Series, Topic, PendingOperation, ManualPublication,
                               PublicationMetric, OperationEvent)


ACTIVE = {"running", "researching", "producing", "validating"}
IDENTIFIER = re.compile(r"^[A-Za-z0-9_:.@-]{1,180}$")
MODELS = {"creator": Creator, "series": Series, "topic": Topic, "run": ContentRun,
          "revision": ContentRevision, "attempt": ContentAttempt, "operation": PendingOperation}
GROUPS = {"overview": "总览 Agent 会话", "unassigned": "未分配账号的栏目",
          "extractions": "全局 Skill 提炼与融合", "historical": "归属已变化的历史任务"}


def encode(kind, *refs):
    return base64.urlsafe_b64encode(json.dumps([kind, *map(str, refs)], separators=(",", ":")).encode()).decode().rstrip("=")


def decode(value):
    try:
        parts = json.loads(base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True))
        if (not isinstance(parts, list) or not 1 <= len(parts) <= 10
                or any(not isinstance(p, str) or not IDENTIFIER.fullmatch(p) or p in {".", ".."} for p in parts)):
            raise ValueError()
        return parts[0], parts[1:]
    except (ValueError, TypeError, UnicodeError):
        raise HTTPException(404, "观察节点不存在。") from None


def clean(value):
    """Keep complete public data; hide secrets/reasoning, never ordinary data keys."""
    def normalize(item):
        if isinstance(item, dict):
            return {str(k): "[REDACTED]" if str(k).lower() in {"confirmation_token", "lease_owner"}
                    else normalize(v) for k, v in item.items()}
        if isinstance(item, (list, tuple)):
            return [normalize(x) for x in item]
        if isinstance(item, datetime):
            return item.isoformat()
        if hasattr(item, "value"):
            return item.value
        return item
    return sanitize_public(normalize(value))[0]


def safe_path(root, path):
    root, path = Path(root).absolute(), Path(path).absolute()
    def linked(candidate):
        try:
            return candidate.is_symlink() or bool(getattr(candidate.lstat(), "st_file_attributes", 0) & 0x400)
        except FileNotFoundError:
            return False  # Missing evidence remains a readable diagnostic state.
    if not path.is_relative_to(root) or linked(root):
        raise ValueError("Unsafe evidence path")
    cursor = path
    while cursor != root:
        if linked(cursor):
            raise ValueError("Linked evidence path")
        cursor = cursor.parent
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Evidence escaped its root")
    return path


def read_json(root, path, maximum=64 * 1024 * 1024):
    path = safe_path(root, path)
    if path.stat().st_size > maximum:
        raise ValueError("Evidence exceeds read limit")
    return json.loads(path.read_text(encoding="utf-8"))


def row_data(row):
    return {col.key: getattr(row, col.key) for col in inspect(row).mapper.column_attrs}


def node(kind, refs, label, *, status=None, children=True, **extra):
    state = status.value if hasattr(status, "value") else status
    return {"id": encode(kind, *refs), "kind": kind, "label": str(label)[:180], "status": state,
            "has_children": children, "active": state in ACTIVE, **extra}


class ObservationService:
    def __init__(self, db, *, chat, research, discussions, extractions, runs):
        self.db, self.chat, self.research = db, chat, research
        self.discussions, self.extractions, self.runs = discussions, extractions, runs

    def _row(self, kind, identifier):
        with self.db.session() as session:
            row = session.get(MODELS[kind], identifier)
            if row is None:
                raise HTTPException(404, "业务记录不存在。")
            return row

    def _rows(self, model, *conditions, order=None):
        with self.db.session() as session:
            return list(session.scalars(select(model).where(*conditions).order_by(order if order is not None else model.id)))

    def _document(self, kind, identifier):
        pattern = r"[a-f0-9-]{36}" if kind == "session" else r"[a-f0-9]{64}" if kind == "extraction" else r"[a-f0-9]{32}"
        if not re.fullmatch(pattern, identifier):
            raise HTTPException(404, "本地记录 ID 无效。")
        roots = {"research": (self.research.root, "batches", ".json"),
                 "discussion": (self.discussions.root, "records", ".json"),
                 "session": (self.chat.root, "", "/view.json"),
                 "extraction": (self.extractions.root, "jobs", "/job.json")}
        root, sub, suffix = roots[kind]
        try:
            value = read_json(root, Path(root) / sub / (identifier + suffix))
            if not isinstance(value, dict) or value.get("id") != identifier:
                raise ValueError()
            return value
        except (OSError, ValueError):
            raise HTTPException(404, "本地记录缺失、损坏或不可安全读取。") from None

    def _documents(self, kind):
        roots = {"research": (self.research.root, "batches/*.json"),
                 "discussion": (self.discussions.root, "records/*.json"),
                 "session": (self.chat.root, "*/view.json"),
                 "extraction": (self.extractions.root, "jobs/*/job.json")}
        root, pattern = roots[kind]
        result = []
        for path in sorted(Path(root).glob(pattern)):
            identifier = path.stem if kind in {"research", "discussion"} else path.parent.name
            if not IDENTIFIER.fullmatch(identifier):
                continue
            try:
                result.append(self._document(kind, identifier))
            except HTTPException:
                continue
        return sorted(result, key=lambda d: (d.get("updated_at") or d.get("created_at") or "", d["id"]), reverse=True)

    def _research_ownership_changed(self, doc):
        snapshot = doc.get("snapshot") or {}
        # Missing historical ownership is unknown, not proof of a transfer.
        if "creator" not in snapshot:
            return False
        frozen = (snapshot.get("creator") or {}).get("id")
        try:
            return frozen != self._row("series", doc["series_id"]).creator_id
        except HTTPException:
            return True

    def _snapshot(self, refs):
        self._document("session", refs[0])  # Check the complete managed path before existing service reads.
        self._trace(refs)
        try:
            safe_path(self.chat.root, Path(self.chat.root) / refs[0] / "messages.request-trace" / (refs[2] + ".json"))
        except ValueError:
            raise HTTPException(404, "聊天请求正文不可安全读取。") from None
        return self.chat.request_snapshot(*refs[:3])

    def _trace(self, refs):
        self._document("session", refs[0])
        try:
            safe_path(self.chat.root, Path(self.chat.root) / refs[0] / "messages.context-trace.jsonl")
        except ValueError:
            raise HTTPException(404, "聊天请求索引不可安全读取。") from None
        return self.chat.turn_trace(*refs[:2])

    def _worker_location(self, refs):
        kind, identifier, *tail = refs
        if kind == "attempt":
            attempt = self._row(kind, identifier)
            revision = self._row("revision", attempt.revision_id)
            run = self._row("run", revision.content_run_id)
            data = revision.production_input_json
            root = Path(self.runs.output_root)
            for key in ("creator_id", "series_id"):
                if not IDENTIFIER.fullmatch(str(data.get(key, ""))):
                    raise HTTPException(404, "冻结输入归属缺失。")
            frozen = run.input_snapshot_json
            if (data.get("topic_id") != run.topic_id or any(data.get(k) != frozen.get(k) for k in ("creator_id", "series_id"))):
                raise HTTPException(404, "执行版本与任务冻结归属不一致。")
            expected = root / data["creator_id"] / data["series_id"] / run.id / f"revision-{revision.revision_number:03d}" / f"attempt-{attempt.attempt_number:03d}"
            if attempt.output_directory and Path(attempt.output_directory).resolve() != expected.resolve():
                raise HTTPException(404, "执行目录与冻结输入不一致。")
            thread = attempt.producer_thread_id
        elif kind == "research_attempt":
            doc = self._document("research", identifier)
            number = int(tail[0])
            if not 1 <= number <= int(doc.get("attempt", 0)):
                raise HTTPException(404, "调研尝试不存在。")
            root = Path(self.research.root)
            expected = root / "work" / identifier / str(number)
            attempts = doc.get("attempts", [])
            thread = attempts[number - 1].get("thread_id") if len(attempts) >= number else None
        elif kind == "discussion":
            doc = self._document(kind, identifier)
            root = Path(self.discussions.root)
            expected, thread = root / "work" / identifier, doc.get("thread_id")
        elif kind in {"extraction", "extraction_execution"}:
            doc = self._document("extraction", identifier)
            root = Path(self.extractions.root)
            job_root = root / "jobs" / identifier
            relative = doc.get("progress_directory", ".")
            thread = doc.get("thread_id")
            if kind == "extraction_execution":
                operation = tail[0]
                if operation == "initial" and len(tail) == 1:
                    relative = "."
                    thread = doc.get("thread_id") if doc.get("progress_directory", ".") == "." else None
                elif operation in {"revise", "trial"} and len(tail) == 2:
                    key = tail[1]
                    action = doc.get("actions", {}).get(key)
                    if not re.fullmatch(r"[a-f0-9]{64}", key) or not action or action.get("operation") != operation:
                        raise HTTPException(404, "提炼操作记录不存在。")
                    relative = f"revisions/{key}" if operation == "revise" else f"trials/{key}/revision-001/attempt-001"
                    trial = next((t for t in doc.get("trials", []) if t.get("id") == key), {})
                    thread = trial.get("thread_id") if operation == "trial" else doc.get("thread_id") if relative == doc.get("progress_directory") else None
                else:
                    raise HTTPException(404, "提炼执行不存在。")
            try:
                expected = safe_path(job_root, job_root / relative)
            except ValueError:
                raise HTTPException(404, "提炼执行目录越过当前任务。") from None
        else:
            raise HTTPException(404, "执行来源不存在。")
        try:
            return root, safe_path(root, expected), thread
        except ValueError:
            raise HTTPException(404, "执行目录不可安全读取。") from None

    def _worker(self, refs):
        root, directory, thread = self._worker_location(refs)
        warnings, events, receipt = [], [], {}
        try:
            receipt = read_json(root, directory / "worker_receipt.json", 1024 * 1024)
            if not isinstance(receipt, dict) or (thread and receipt.get("thread_id") != thread):
                raise ValueError()
            thread = thread or receipt.get("thread_id")
        except (OSError, ValueError):
            receipt = {}
            warnings.append("历史未记录可核验的 worker 回执。")
        source = "codex_public_events.jsonl"
        path = directory / source
        if not path.is_file():
            source, path = "codex_trace.jsonl", directory / "codex_trace.jsonl"
            warnings.append("此执行未保存完整公开 SDK item；旧摘要只能证明已记录的部分活动。")
        try:
            path = safe_path(root, path)
            if path.stat().st_size > 64 * 1024 * 1024:
                raise ValueError()
            with path.open(encoding="utf-8") as stream:
                for number, line in enumerate(stream, 1):
                    try:
                        event = json.loads(line)
                        if not isinstance(event, dict):
                            raise ValueError()
                        if thread and event.get("thread_id") and event["thread_id"] != thread:
                            warnings.append("活动 thread 与本次执行已保存身份不一致，已忽略。")
                            continue
                        if not thread and event.get("thread_id"):
                            thread = event["thread_id"]
                        method = event.get("method", event.get("type", ""))
                        item = event.get("item") or (event.get("payload") or {}).get("item") or {}
                        item_type = event.get("item_type") or item.get("type")
                        if item_type == "reasoning" or "reasoning" in str(method).lower():
                            event = {k: event.get(k) for k in ("schema_version", "at", "phase", "method", "type", "thread_id", "turn_id", "item_id", "status")}
                            event.update(item_type="reasoning", payload={"omitted": "内部思考正文不展示。"},
                                         omissions=[{"path": "$.payload", "reason": "reasoning_body_excluded"}])
                        event["_line"] = number
                        events.append(event)
                    except (ValueError, AttributeError):
                        warnings.append(f"第 {number} 行活动未完整写入或损坏。")
        except (OSError, ValueError):
            warnings.append("本地公开活动文件缺失或不可安全读取。")
        if source != "codex_public_events.jsonl" and refs[0] in {"extraction", "extraction_execution"}:
            try:
                index = read_json(root, directory / "public_events/index.json")
                for entry in index.get("items", []):
                    identifier = entry.get("id")
                    if not isinstance(identifier, int) or identifier < 1:
                        continue
                    value = read_json(root, directory / "public_events" / f"{identifier}.json")
                    events.append({"_line": identifier, "type": "legacy_public_activity", "item_id": f"legacy-{identifier}",
                                   "item_type": value.get("item_type"), "status": value.get("status"),
                                   "at": value.get("at"), "payload": value})
                source = source + "+public_events"
            except (OSError, ValueError, AttributeError):
                pass
        return {"thread_id": thread, "receipt": receipt, "events": events, "host": self._worker_host(refs),
                "source": source, "warnings": list(dict.fromkeys(warnings))}

    def _worker_host(self, refs):
        """The current host execution gates liveness; a saved SDK start cannot."""
        kind, identifier, *tail = refs
        if kind == "attempt":
            attempt = self._row(kind, identifier)
            revision = self._row("revision", attempt.revision_id)
            run = self._row("run", revision.content_run_id)
            attempts = self._rows(ContentAttempt, ContentAttempt.revision_id == revision.id)
            current = (revision.revision_number == run.active_revision_number
                       and attempt.attempt_number == max(a.attempt_number for a in attempts))
            status = attempt.status.value
            active = current and status == "running" and run.status.value in {"producing", "validating"}
            return {"status": status, "run_status": run.status.value, "current_execution": current, "active": active}
        if kind == "research_attempt":
            doc = self._document("research", identifier)
            current = int(tail[0]) == int(doc.get("attempt", 0))
            return {"status": doc.get("status"), "current_execution": current,
                    "active": current and doc.get("status") == "researching"}
        if kind == "discussion":
            doc = self._document(kind, identifier)
            return {"status": doc.get("status"), "current_execution": True,
                    "active": doc.get("status") in {"queued", "running"}}
        doc = self._document("extraction", identifier)
        current_directory = doc.get("progress_directory", ".")
        operation = tail[0] if kind == "extraction_execution" else doc.get("operation") or "initial"
        expected = current_directory
        if kind == "extraction_execution":
            expected = "." if operation == "initial" else f"revisions/{tail[1]}" if operation == "revise" else f"trials/{tail[1]}/revision-001/attempt-001"
        current = expected == current_directory
        active = current and doc.get("status") not in {"failed", "interrupted", "cancelled"} and (
            doc.get("status") == "running" if operation == "initial" else doc.get("operation") == operation)
        if operation == "trial" and kind == "extraction_execution":
            trial = next((t for t in doc.get("trials", []) if t.get("id") == tail[1]), {})
            active = active and trial.get("status") == "running"
        return {"status": doc.get("status"), "operation": doc.get("operation"),
                "current_execution": current, "active": bool(active)}

    @staticmethod
    def _sdk_status(status, host):
        return "unknown" if status in ACTIVE and not host["active"] else status

    @staticmethod
    def _stale_warning():
        return "宿主已终止或此执行已被后续尝试替代；SDK 的 running 仅为最后保存状态，当前是否运行未知，不继续自动刷新。"

    def _ancestors(self, kind, refs):
        path = []
        for _ in range(14):
            parent = None
            if kind == "creator":
                break
            if kind == "series":
                creator_id = self._row(kind, refs[0]).creator_id
                parent = ("creator", [creator_id]) if creator_id else ("group", ["unassigned"])
            elif kind in {"topics", "researches", "operations"}:
                parent = ("series", refs)
            elif kind == "topic":
                parent = ("topics", [self._row(kind, refs[0]).series_id])
            elif kind == "run":
                run = self._row(kind, refs[0])
                topic = self._row("topic", run.topic_id)
                series = self._row("series", topic.series_id)
                parent = ("topic", [topic.id]) if run.input_snapshot_json.get("creator_id") == series.creator_id else ("group", ["historical"])
            elif kind == "revision":
                parent = ("run", [self._row(kind, refs[0]).content_run_id])
            elif kind == "attempt":
                parent = ("revision", [self._row(kind, refs[0]).revision_id])
            elif kind == "discussions":
                parent = ("run", refs)
            elif kind == "discussion":
                parent = ("discussions", [self._document(kind, refs[0])["run_id"]])
            elif kind == "research":
                doc = self._document(kind, refs[0])
                parent = ("group", ["historical"]) if self._research_ownership_changed(doc) else ("researches", [doc["series_id"]])
            elif kind == "research_attempt":
                parent = ("research", refs[:1])
            elif kind == "operation":
                series_id = self._row(kind, refs[0]).scope_series_id
                parent = ("operations", [series_id]) if series_id else None
            elif kind == "extraction":
                parent = ("group", ["extractions"])
            elif kind == "extraction_execution":
                parent = ("extraction", refs[:1])
            elif kind == "sessions":
                parent = ("creator", refs)
            elif kind == "session":
                doc = self._document(kind, refs[0])
                parent = ("sessions", [doc["creator_id"]]) if doc.get("creator_id") else ("group", ["overview"])
            elif kind == "user_turn":
                parent = ("session", refs[:1])
            elif kind == "request":
                parent = ("user_turn", refs[:2])
            elif kind == "tool_call":
                parent = ("request", refs[:3])
            elif kind == "worker":
                parent = (refs[0], refs[1:])
            elif kind == "worker_turn":
                parent = ("worker", refs[:-1])
            elif kind == "worker_item":
                parent = ("worker_turn", refs[:-1])
            if parent is None:
                break
            kind, refs = parent
            label = {"sessions": "账号 Agent 会话", "topics": "选题与生产", "researches": "选题调研",
                     "discussions": "产物讨论", "operations": "选题操作与审批", "worker": "Codex 公开执行记录"}.get(kind, kind)
            if kind == "group":
                label = GROUPS[refs[0]]
            elif kind in MODELS:
                data = row_data(self._row(kind, refs[0]))
                label = data.get("display_name") or data.get("name") or data.get("title") or f"{kind} {refs[0][:8]}"
            elif kind == "session":
                label = self._document(kind, refs[0]).get("title", "对话")
            path.append({"id": encode(kind, *refs), "kind": kind, "label": label})
        return list(reversed(path))

    def _worker_turns(self, refs):
        doc = self._worker(refs)
        turns = {str(t["id"]): dict(t) for t in doc["receipt"].get("turns", []) if isinstance(t, dict) and t.get("id")}
        for event in doc["events"]:
            identifier = event.get("turn_id")
            if identifier:
                turns.setdefault(str(identifier), {"id": str(identifier), "status": "unknown"})
                if event.get("method") in {"capture/started", "turn/started", "turn/completed", "capture/finished"} and event.get("status"):
                    turns[str(identifier)]["status"] = event["status"]
        if any(not event.get("turn_id") for event in doc["events"]):
            turns["unrecorded"] = {"id": "unrecorded", "status": "partial"}
        for turn in turns.values():
            status = turn.get("status")
            turn["status"] = self._sdk_status(status, doc["host"])
            if turn["status"] != status:
                turn["last_recorded_status"] = status
                doc["warnings"].append(self._stale_warning())
        return doc, turns

    @staticmethod
    def _item_groups(events, turn_id):
        result = {}
        for event in events:
            if (event.get("turn_id") or "unrecorded") != turn_id:
                continue
            item = event.get("item") or (event.get("payload") or {}).get("item") or {}
            identifier = str(event.get("item_id") or item.get("id") or f"event-{event['_line']}")
            result.setdefault(identifier, []).append(event)
        return result

    def tree(self, parent=None, offset=0, limit=100):
        kind, refs = decode(parent) if parent else ("root", [])
        try:
            items = self._children(kind, refs)
        except (IndexError, KeyError, TypeError, ValueError):
            raise HTTPException(404, "观察节点缺失或记录不完整。") from None
        return clean({"items": items[offset:offset + limit], "next_offset": offset + min(limit, max(0, len(items) - offset)),
                      "has_more": offset + limit < len(items), "total": len(items)})

    def _children(self, kind, refs):
        if kind == "root":
            return [node("creator", [r.id], r.display_name, status="active" if r.is_active else "disabled") for r in self._rows(Creator)] + [node("group", [key], label) for key, label in GROUPS.items()]
        if kind == "creator":
            self._row(kind, refs[0])
            return [node("series", [r.id], r.name, status="active" if r.is_active else "archived") for r in self._rows(Series, Series.creator_id == refs[0])] + [node("sessions", refs, "账号 Agent 会话")]
        if kind == "group":
            if refs[0] == "overview":
                return [node("session", [d["id"]], d.get("title", "对话"), status=d.get("status")) for d in self._documents("session") if d.get("scope_kind", "overview") == "overview"]
            if refs[0] == "unassigned":
                return [node("series", [r.id], r.name) for r in self._rows(Series, Series.creator_id.is_(None))]
            if refs[0] == "extractions":
                return [node("extraction", [d["id"]], d.get("instruction") or d.get("task_kind") or "Skill 提炼", status=d.get("status")) for d in self._documents("extraction")]
            if refs[0] == "historical":
                result = []
                for run in self._rows(ContentRun):
                    topic = self._row("topic", run.topic_id)
                    series = self._row("series", topic.series_id)
                    if run.input_snapshot_json.get("creator_id") != series.creator_id:
                        result.append(node("run", [run.id], topic.title, status=run.status))
                result += [node("research", [doc["id"]], doc.get("instructions") or "历史调研", status=doc.get("status"))
                           for doc in self._documents("research") if self._research_ownership_changed(doc)]
                return result
            raise HTTPException(404, "分组不存在。")
        if kind == "series":
            self._row(kind, refs[0])
            return [node("topics", refs, "选题与生产"), node("researches", refs, "选题调研"), node("operations", refs, "选题操作与审批")]
        if kind == "topics":
            self._row("series", refs[0])
            return [node("topic", [r.id], r.title, status=r.status) for r in self._rows(Topic, Topic.series_id == refs[0], order=Topic.position)]
        if kind == "topic":
            topic = self._row(kind, refs[0])
            creator = self._row("series", topic.series_id).creator_id
            return [node("run", [r.id], "生产 " + r.id[:8], status=r.status) for r in self._rows(ContentRun, ContentRun.topic_id == topic.id) if r.input_snapshot_json.get("creator_id") == creator]
        if kind == "run":
            self._row(kind, refs[0])
            return [node("revision", [r.id], f"版本 {r.revision_number}", status="approved" if r.approved_at else "validated" if r.validated_at else "pending") for r in self._rows(ContentRevision, ContentRevision.content_run_id == refs[0], order=ContentRevision.revision_number)] + [node("discussions", refs, "产物讨论")]
        if kind == "revision":
            self._row(kind, refs[0])
            return [node("attempt", [r.id], f"执行尝试 {r.attempt_number}", status=r.status) for r in self._rows(ContentAttempt, ContentAttempt.revision_id == refs[0], order=ContentAttempt.attempt_number)]
        if kind in {"attempt", "discussion", "extraction_execution", "research_attempt"}:
            self._worker_location([kind, *refs])
            return [node("worker", [kind, *refs], "Codex 公开执行记录")]
        if kind == "extraction":
            doc = self._document(kind, refs[0])
            items = [node("extraction_execution", [refs[0], "initial"], "首次提炼或融合")]
            for key, action in doc.get("actions", {}).items():
                operation = action.get("operation")
                if operation not in {"revise", "trial"} or not re.fullmatch(r"[a-f0-9]{64}", key):
                    continue
                title = ("草稿改稿：" if operation == "revise" else "试产：") + action.get("value", "")
                items.append(node("extraction_execution", [refs[0], operation, key], title))
            return items
        if kind == "researches":
            self._row("series", refs[0])
            return [node("research", [d["id"]], d.get("instructions") or "栏目选题调研", status=d.get("status")) for d in self._documents("research") if d.get("series_id") == refs[0] and not self._research_ownership_changed(d)]
        if kind == "research":
            doc = self._document(kind, refs[0])
            return [node("research_attempt", [refs[0], str(n)], f"调研尝试 {n}") for n in range(1, int(doc.get("attempt", 0)) + 1)]
        if kind == "discussions":
            self._row("run", refs[0])
            return [node("discussion", [d["id"]], d.get("message") or "产物讨论", status=d.get("status")) for d in self._documents("discussion") if d.get("run_id") == refs[0]]
        if kind == "operations":
            self._row("series", refs[0])
            return [node("operation", [r.id], r.request_text, status=r.status, children=False) for r in self._rows(PendingOperation, PendingOperation.scope_series_id == refs[0])]
        if kind == "sessions":
            self._row("creator", refs[0])
            return [node("session", [d["id"]], d.get("title", "对话"), status=d.get("status")) for d in self._documents("session") if d.get("creator_id") == refs[0]]
        if kind == "session":
            doc = self._document(kind, refs[0])
            return [node("user_turn", [refs[0], r["id"]], r.get("text") or "用户指令", status=r.get("status", "unknown")) for r in doc.get("requests", [])]
        if kind == "user_turn":
            return [node("request", [*refs, r["request_id"]], f"模型请求 {i + 1}", status=r.get("status")) for i, r in enumerate(self._trace(refs)["requests"])]
        if kind == "request":
            snapshot = self._snapshot(refs)
            calls = (snapshot.get("response") or {}).get("tool_calls", [])
            return [node("tool_call", [*refs, str(c["id"])], c.get("name") or (c.get("function") or {}).get("name", "工具调用"), children=False) for c in calls]
        if kind == "worker":
            _doc, turns = self._worker_turns(refs)
            return [node("worker_turn", [*refs, identifier], "未记录 turn 归属的活动" if identifier == "unrecorded" else f"Turn {identifier}", status=t.get("status")) for identifier, t in turns.items()]
        if kind == "worker_turn":
            doc, turns = self._worker_turns(refs[:-1])
            if refs[-1] not in turns:
                raise HTTPException(404, "执行回合不属于此任务。")
            return [node("worker_item", [*refs, key], str(events[-1].get("item_type") or events[-1].get("method") or events[-1].get("type") or "公开活动"), status=self._sdk_status(events[-1].get("status"), doc["host"]), children=False,
                         last_recorded_status=events[-1].get("status")) for key, events in self._item_groups(doc["events"], refs[-1]).items()]
        if kind in {"tool_call", "worker_item", "operation"}:
            self.detail(encode(kind, *refs))
            return []
        raise HTTPException(404, "观察节点不存在。")

    def _business_links(self, snapshot):
        links = []
        mapping = {"run_id": "run", "revision_id": "revision", "series_id": "series", "topic_id": "topic", "creator_id": "creator"}
        def visit(value, tool_name=""):
            if isinstance(value, dict):
                for key, kind in mapping.items():
                    identifier = value.get(key)
                    if isinstance(identifier, str) and IDENTIFIER.fullmatch(identifier):
                        try:
                            self._row(kind, identifier)
                            links.append({"label": f"{kind}: {identifier}", "node_id": encode(kind, identifier)})
                        except HTTPException:
                            pass
                identifier = value.get("id")
                doc_kind = "research" if tool_name in {"research_series_topics", "get_topic_research"} else "discussion" if tool_name in {"discuss_content_run", "get_content_discussion"} else None
                if doc_kind and isinstance(identifier, str) and IDENTIFIER.fullmatch(identifier):
                    try:
                        self._document(doc_kind, identifier)
                        links.append({"label": f"{doc_kind}: {identifier}", "node_id": encode(doc_kind, identifier)})
                    except HTTPException:
                        pass
                for child in value.values():
                    if isinstance(child, (dict, list)):
                        visit(child, tool_name)
            elif isinstance(value, list):
                for child in value:
                    visit(child, tool_name)
        for result in snapshot.get("tool_results", []):
            content = result.get("content")
            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except ValueError:
                    continue
            visit(content, result.get("name", ""))
        return list({entry["node_id"]: entry for entry in links}.values())

    def _origins(self, target, creator_id=None):
        """Explicit tool-result identities only; observing a record is not creating it."""
        links = []
        for doc in self._documents("session"):
            if creator_id and doc.get("creator_id") not in {None, creator_id}:
                continue
            for request in doc.get("requests", []):
                try:
                    trace = self._trace([doc["id"], request["id"]])
                    for entry in trace["requests"]:
                        refs = [doc["id"], request["id"], entry["request_id"]]
                        snapshot = self._snapshot(refs)
                        for result in snapshot.get("tool_results", []):
                            if any(link["node_id"] == target for link in self._business_links({"tool_results": [result]})):
                                submitted = result.get("name") in {"start_content_run", "research_series_topics", "discuss_content_run", "request_content_revision"}
                                label = "聊天提交记录" if submitted else "聊天查询引用（不代表发起）"
                                links.append({"label": f"{label}：{result.get('name', '工具')}", "node_id": encode("tool_call", *refs, result["tool_call_id"])})
                except (HTTPException, KeyError, OSError, ValueError):
                    continue
        return list({link["node_id"]: link for link in links}.values())

    def _diagnostic(self, kind, refs):
        """Known error locations only. Reading errors must never change their status."""
        try:
            if kind == "research":
                self._document(kind, refs[0])
                root = Path(self.research.root)
                path = root / f"{refs[0]}-error.txt"
            elif kind in {"attempt", "discussion", "extraction", "extraction_execution", "research_attempt"}:
                root, directory, _thread = self._worker_location([kind, *refs])
                path = directory / "error.txt"
                # Trial errors are saved at its explicit trial root by the host.
                if kind == "extraction_execution" and refs[1] == "trial":
                    path = directory.parent.parent / "error.txt"
            else:
                return None
            path = safe_path(root, path)
            if path.stat().st_size > 8 * 1024 * 1024:
                return "已保存错误文件超过 8 MiB 读取上限，正文未展示。"
            return path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            return None

    def _worker_timeline(self, refs):
        doc = self._worker(refs)
        prefix = encode("worker", *refs)
        timeline = [{"id": prefix + "-identity", "kind": "worker", "label": "Codex 执行身份",
                     "content": {"thread_id": doc["thread_id"], "evidence": doc["source"]}, "node_id": prefix}]
        # This order is the persisted event order, not a guessed correspondence.
        grouped = {}
        for event in doc["events"]:
            turn_id = event.get("turn_id") or "unrecorded"
            item = event.get("item") or (event.get("payload") or {}).get("item") or {}
            item_id = str(event.get("item_id") or item.get("id") or f"event-{event['_line']}")
            grouped[(turn_id, item_id)] = event
        labels = {"agentMessage": "Codex 回复", "agent_message": "Codex 回复（旧摘要）",
                  "commandExecution": "工具：命令执行", "webSearch": "工具：联网搜索",
                  "web_search": "联网搜索（旧摘要）", "fileChange": "工具：文件修改",
                  "mcpToolCall": "工具：MCP 调用", "dynamicToolCall": "工具调用",
                  "plan": "Codex 计划", "userMessage": "Codex 实际输入", "reasoning": "内部思考条目（正文不展示）"}
        for (turn_id, item_id), event in grouped.items():
            payload = event.get("payload") or event.get("item") or {}
            item = payload.get("item") if isinstance(payload, dict) else None
            item = item if isinstance(item, dict) else payload
            item_type = event.get("item_type") or (item.get("type") if isinstance(item, dict) else None)
            method = event.get("method") or event.get("type", "公开活动")
            is_start = method in {"capture/started", "turn/started"}
            status = None if is_start else self._sdk_status(event.get("status"), doc["host"])
            if not is_start and status != event.get("status"):
                doc["warnings"].append(self._stale_warning())
            timeline.append({"id": encode("worker_item", *refs, turn_id, item_id), "kind": "codex_item", "label": labels.get(item_type, str(method)),
                "at": event.get("at"), "status": status, "last_recorded_status": event.get("status"), "content": item,
                "node_id": encode("worker_item", *refs, turn_id, item_id)})
        if not doc["events"]:
            timeline.append({"id": prefix + "-missing", "kind": "missing", "label": "公开执行正文未记录",
                             "content": "历史只有业务状态或回执；没有可展示的完整 SDK item。", "node_id": prefix})
        diagnostic = self._diagnostic(refs[0], refs[1:])
        if diagnostic:
            timeline.append({"id": prefix + "-diagnostic", "kind": "error", "label": "已保存错误诊断",
                             "content": diagnostic, "status": "failed", "node_id": prefix})
        return timeline, doc["warnings"]

    def _timeline(self, kind, refs):
        items, warnings = [], []
        def add(label, content, *, status=None, at=None, node_id=None, event_kind="host"):
            items.append({"id": f"{encode(kind, *refs)}-{len(items)}", "label": label, "kind": event_kind,
                          "content": content, "status": status, "at": at, "node_id": node_id})
        def worker(source):
            try:
                timeline, notes = self._worker_timeline(source)
                items.extend(timeline)
                warnings.extend(notes)
            except HTTPException:
                add("执行证据不可用", "受管目录缺失或归属校验未通过。", event_kind="missing")
        if kind in {"run", "revision", "attempt"}:
            row = self._row(kind, refs[0])
            revision = self._row("revision", row.revision_id) if kind == "attempt" else row if kind == "revision" else None
            run = self._row("run", revision.content_run_id) if revision else row
            frozen = run.input_snapshot_json
            add("CreatorOS 接收生产任务", {k: frozen.get(k) for k in ("topic_title", "topic_brief", "audience", "creator_id", "series_id", "topic_id") if k in frozen}, at=run.created_at)
            revisions = [revision] if revision else self._rows(ContentRevision, ContentRevision.content_run_id == run.id, order=ContentRevision.revision_number)
            for rev in revisions:
                add(f"版本 {rev.revision_number}：冻结输入", {"instruction": rev.instruction, "topic": rev.production_input_json.get("topic_title"), "note": "完整冻结输入在版本详情。"}, at=rev.created_at, node_id=encode("revision", rev.id))
                attempts = [row] if kind == "attempt" else self._rows(ContentAttempt, ContentAttempt.revision_id == rev.id, order=ContentAttempt.attempt_number)
                for attempt in attempts:
                    add(f"尝试 {attempt.attempt_number}：执行开始", {"attempt_id": attempt.id, "thread_id": attempt.producer_thread_id}, at=attempt.started_at, node_id=encode("attempt", attempt.id))
                    worker(["attempt", attempt.id])
                    label = "宿主记录结果" if attempt.completed_at else "宿主当前执行状态（未记录结束时间）"
                    add(f"尝试 {attempt.attempt_number}：{label}", {"error_type": attempt.error_type, "error_message": attempt.error_message, "usage": attempt.usage_json}, status=attempt.status, at=attempt.completed_at or attempt.heartbeat_at, node_id=encode("attempt", attempt.id))
                add(f"版本 {rev.revision_number}：产物验收", {"artifact_digest": rev.artifact_digest, "validation": rev.validation_json,
                    "validated_at": rev.validated_at, "approved_at": rev.approved_at}, status="approved" if rev.approved_at else "validated" if rev.validated_at else "unavailable", at=rev.validated_at)
            for event in self._rows(ContentRunEvent, ContentRunEvent.content_run_id == run.id, order=ContentRunEvent.id):
                add(f"业务状态：{event.event_type.value}", {"from": event.from_status, "to": event.to_status, "payload": event.payload_json}, status=event.to_status, at=event.created_at, event_kind="business_state")
            add("当前业务状态", {"run_id": run.id, "status": run.status, "version": run.version,
                "meaning": "SDK 完成和业务验收/批准分别保留；不依据文字回复推断成功。"}, status=run.status, at=run.updated_at)
        elif kind in {"research", "discussion", "extraction"}:
            doc = self._document(kind, refs[0])
            add("宿主提交要求", {k: doc.get(k) for k in ("message", "instructions", "instruction", "count") if k in doc}, at=doc.get("created_at"))
            if kind == "research":
                for number in range(1, int(doc.get("attempt", 0)) + 1):
                    add(f"调研尝试 {number}", {"batch_id": refs[0], "attempt": number}, node_id=encode("research_attempt", refs[0], number))
                    worker(["research_attempt", refs[0], str(number)])
            elif kind == "extraction":
                for execution in self._children(kind, refs):
                    source_kind, source_refs = decode(execution["id"])
                    add(execution["label"], "按保存的操作 ID 关联此次执行。", node_id=execution["id"])
                    worker([source_kind, *source_refs])
            else:
                worker([kind, *refs])
            for event in doc.get("events", (doc.get("progress") or {}).get("events", [])):
                add(str(event.get("kind", "公开进度")), event.get("text"), status=event.get("status"), at=event.get("at"), event_kind="progress")
            add("宿主保存的结果", {k: doc.get(k) for k in ("status", "candidates", "reply", "note", "error", "error_type", "saved_skills") if k in doc}, status=doc.get("status"), at=doc.get("updated_at"))
            diagnostic = self._diagnostic(kind, refs)
            if diagnostic:
                add("已保存错误诊断", diagnostic, status="failed", event_kind="error")
            elif doc.get("status") in {"failed", "interrupted"}:
                warnings.append("历史未保存可读取的原始错误诊断，只展示已记录的宿主错误字段。")
        elif kind in {"worker", "worker_turn", "worker_item"}:
            tail = 0 if kind == "worker" else 1 if kind == "worker_turn" else 2
            source = refs[:-tail] if tail else refs
            items, warnings = self._worker_timeline(source)
            if tail:
                target = encode("worker_turn", *refs[:-1]) if kind == "worker_item" else encode(kind, *refs)
                items = [item for item in items if item.get("node_id") and (item["node_id"] == encode(kind, *refs)
                    if kind == "worker_item" else decode(item["node_id"])[0] == "worker_item" and encode("worker_turn", *decode(item["node_id"])[1][:-1]) == target)]
        elif kind == "session":
            doc = self._document(kind, refs[0])
            for entry in doc.get("requests", []):
                add("用户指令", entry.get("text"), status=entry.get("status"), node_id=encode("user_turn", refs[0], entry["id"]), event_kind="user")
        elif kind in {"research_attempt", "extraction_execution"}:
            items, warnings = self._worker_timeline([kind, *refs])
        elif kind in {"user_turn", "request", "tool_call"}:
            doc = self._document("session", refs[0])
            turn = next(r for r in doc.get("requests", []) if r["id"] == refs[1])
            add("用户指令", turn.get("text"), status=turn.get("status"), node_id=encode("user_turn", *refs[:2]), event_kind="user")
            trace = self._trace(refs)
            for entry in trace["requests"]:
                if kind in {"request", "tool_call"} and entry["request_id"] != refs[2]:
                    continue
                identity = [*refs[:2], entry["request_id"]]
                add("模型请求", {k: entry.get(k) for k in ("model", "request_kind", "usage", "error_type")}, status=entry.get("status"), at=entry.get("started_at"), node_id=encode("request", *identity), event_kind="model_request")
                try:
                    snapshot = self._snapshot(identity)
                    response = snapshot.get("response") or {}
                    if response.get("content"):
                        add("模型公开回复", response["content"], node_id=encode("request", *identity), event_kind="assistant")
                    for call in (snapshot.get("response") or {}).get("tool_calls", []):
                        if kind == "tool_call" and call["id"] != refs[3]:
                            continue
                        results = [result for result in snapshot.get("tool_results", []) if result["tool_call_id"] == call["id"]]
                        add(call.get("name") or (call.get("function") or {}).get("name", "工具调用"), {"arguments": call.get("arguments", (call.get("function") or {}).get("arguments")), "results": results},
                            status="failed" if any(r.get("is_error") for r in results) else "completed" if results else "unknown", node_id=encode("tool_call", *identity, call["id"]), event_kind="tool")
                except HTTPException:
                    warnings.append("部分模型请求正文未保存或不可用。")
        # Sort only timestamped slots; undated evidence retains its source order.
        # This is display ordering, never an identity or origin inference.
        dated = []
        for index, item in enumerate(items):
            at = item.get("at")
            try:
                parsed = at if isinstance(at, datetime) else datetime.fromisoformat(at.replace("Z", "+00:00"))
                stamp = (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()
                dated.append((index, stamp, item))
            except (TypeError, ValueError, AttributeError):
                continue
        for target, ordered in zip(dated, sorted(dated, key=lambda entry: entry[1])):
            items[target[0]] = ordered[2]
        if dated and len(dated) < len(items):
            warnings.append("有时间戳的条目按已记录时间排序；无时间戳条目保留来源顺序，不能据此推断发生时刻。")
        return items, list(dict.fromkeys(warnings))

    def detail(self, node_id):
        kind, refs = decode(node_id)
        result = {"id": node_id, "kind": kind, "label": kind, "status": None,
                  "sections": [], "links": [], "warnings": [], "active": False, "ancestors": [], "timeline": []}
        host_active = None
        def section(title, content):
            result["sections"].append({"title": title, "content": content})
        try:
            if kind in MODELS:
                row = self._row(kind, refs[0])
                data = row_data(row)
                result.update(label=data.get("title") or data.get("display_name") or data.get("name") or f"{kind} {refs[0]}", status=data.get("status"))
                section("业务记录", data)
                if kind == "attempt":
                    host_active = self._worker_host([kind, *refs])["active"]
                    if result["status"] in ACTIVE and not host_active:
                        result["last_recorded_status"] = result["status"]
                        result["status"] = "unknown"
                        result["warnings"].append(self._stale_warning())
                parent = {"series": ("creator", data.get("creator_id")), "topic": ("series", data.get("series_id")), "run": ("topic", data.get("topic_id")), "revision": ("run", data.get("content_run_id")), "attempt": ("revision", data.get("revision_id"))}.get(kind)
                if parent and parent[1]:
                    result["links"].append({"label": "所属业务记录", "node_id": encode(*parent)})
                if kind == "run":
                    events = self._rows(ContentRunEvent, ContentRunEvent.content_run_id == row.id, order=ContentRunEvent.id)
                    section("业务状态时间线", [row_data(e) for e in events])
                    publications = self._rows(ManualPublication, ManualPublication.content_run_id == row.id)
                    if publications:
                        publication = publications[0]
                        section("人工发布记录", row_data(publication))
                        section("效果反馈", [row_data(r) for r in self._rows(PublicationMetric, PublicationMetric.publication_id == publication.id)])
                    result["links"].append({"label": "打开作品", "href": f"/runs/{row.id}"})
                    result["links"] += self._origins(node_id, data.get("input_snapshot_json", {}).get("creator_id"))
                    if not any(l["label"].startswith("聊天") for l in result["links"]):
                        result["warnings"].append("未保存可确认的关联聊天工具记录；不能判断由页面还是聊天发起。")
                if kind == "operation":
                    section("操作状态时间线", [row_data(e) for e in self._rows(OperationEvent, OperationEvent.pending_operation_id == row.id)])
            elif kind in {"research", "discussion", "extraction", "session"}:
                doc = self._document(kind, refs[0])
                result.update(label=doc.get("title") or doc.get("message") or doc.get("instructions") or kind, status=doc.get("status"))
                section("已保存记录", doc)
                if kind in {"research", "discussion"}:
                    result["links"] += self._origins(node_id, doc.get("creator_id") or (doc.get("snapshot", {}).get("creator") or {}).get("id"))
                    if not result["links"]:
                        result["warnings"].append("未记录关联聊天工具；不推断发起来源。")
                if kind == "session" and not doc.get("requests"):
                    result["warnings"].append("历史未保存用户回合索引。")
                if kind == "research":
                    section("调研公开活动时间线", (doc.get("progress") or {}).get("events", []))
                if kind == "discussion":
                    section("讨论公开活动时间线", doc.get("events", []))
            elif kind in {"research_attempt", "extraction_execution"}:
                self._worker_location([kind, *refs])
                host_active = self._worker_host([kind, *refs])["active"]
                result["active"] = host_active
                section("执行记录", {"source": kind, "record_id": refs[0], "operation": refs[1:]})
            elif kind == "user_turn":
                doc = self._document("session", refs[0])
                turn = next((r for r in doc.get("requests", []) if r["id"] == refs[1]), None)
                if turn is None:
                    raise HTTPException(404, "聊天回合不存在。")
                trace = self._trace(refs)
                result.update(label=turn.get("text") or "用户指令", status=trace["status"])
                section("用户指令", turn)
                section("模型请求索引", trace)
                if not trace["available"]:
                    result["warnings"].append("此历史回合未记录模型请求正文；不补造上下文。")
            elif kind in {"request", "tool_call"}:
                snapshot = self._snapshot(refs)
                if kind == "request":
                    trace_entry = next(r for r in self._trace(refs)["requests"] if r["request_id"] == refs[2])
                    result["status"] = trace_entry.get("status")
                    section("请求元数据", trace_entry)
                    section("实际模型输入", snapshot.get("context"))
                    section("模型公开输出", snapshot.get("response"))
                    section("工具结果", snapshot.get("tool_results"))
                    result["links"] += self._business_links(snapshot)
                else:
                    call = next((c for c in (snapshot.get("response") or {}).get("tool_calls", []) if c["id"] == refs[3]), None)
                    if call is None:
                        raise HTTPException(404, "工具调用不属于此模型请求。")
                    returned = [r for r in snapshot.get("tool_results", []) if r["tool_call_id"] == refs[3]]
                    result["label"] = call.get("name") or (call.get("function") or {}).get("name", "工具调用")
                    result["status"] = "failed" if any(r.get("is_error") for r in returned) else "completed" if returned else "unknown"
                    section("调用参数", call)
                    section("工具返回", returned)
                    result["links"] += self._business_links({"tool_results": returned})
                    if not returned:
                        result["warnings"].append("未保存工具结果；执行是否生效未知。")
            elif kind in {"worker", "worker_turn", "worker_item"}:
                tail = 0 if kind == "worker" else 1 if kind == "worker_turn" else 2
                source_refs = refs[:-tail] if tail else refs
                doc, turns = self._worker_turns(source_refs)
                host_active = doc["host"]["active"]
                result["label"] = f"Codex {doc['thread_id'] or '未记录 thread'}"
                result["warnings"] += doc["warnings"]
                section("执行身份", {"thread_id": doc["thread_id"], "receipt": doc["receipt"], "source": doc["source"], "host": doc["host"]})
                if kind == "worker":
                    root, directory, _thread = self._worker_location(source_refs)
                    for filename, title in (("worker_task.json", "宿主任务"), ("production_progress.json", "宿主公开进度")):
                        try:
                            section(title, read_json(root, directory / filename))
                        except (OSError, ValueError):
                            pass
                    for filename in ("production_request.txt", "production_instructions.txt", "composition_request.txt", "delivery_repair_request.txt", "research_request.txt", "discussion_request.txt", "instructions.txt"):
                        try:
                            path = safe_path(root, directory / filename)
                            if path.stat().st_size <= 8 * 1024 * 1024:
                                section(f"已保存的实际输入：{filename}", path.read_text(encoding="utf-8"))
                            else:
                                result["warnings"].append(f"{filename} 超过 8 MiB 读取上限，未展示正文。")
                        except (OSError, ValueError):
                            pass
                    result["active"] = host_active
                else:
                    turn_id = refs[-tail]
                    if turn_id not in turns:
                        raise HTTPException(404, "执行回合不属于此任务。")
                    result["status"] = turns[turn_id].get("status")
                    if kind == "worker_turn":
                        section("执行回合", turns[turn_id])
                        section("公开事件时间线", [{k: e.get(k) for k in ("at", "phase", "method", "type", "item_id", "item_type", "status")} for e in doc["events"] if (e.get("turn_id") or "unrecorded") == turn_id])
                    else:
                        events = self._item_groups(doc["events"], turn_id).get(refs[-1])
                        if events is None:
                            raise HTTPException(404, "公开条目不属于此执行回合。")
                        recorded_status = events[-1].get("status") or result["status"]
                        result["status"] = self._sdk_status(recorded_status, doc["host"])
                        result["last_recorded_status"] = recorded_status
                        if result["status"] != recorded_status:
                            result["warnings"].append(self._stale_warning())
                        section("完整已记录公开事件", events)
                    if turn_id == "unrecorded":
                        result["warnings"].append("这些旧事件没有 turn ID，不能按时间或位置猜测关联。")
            elif kind in {"group", "sessions", "topics", "researches", "operations", "discussions"}:
                self._children(kind, refs)
                result["label"] = GROUPS.get(refs[0], kind)
                section("观察范围", "按已保存标识关联。只读本地记录；不启动模型、工具、恢复或生产。")
            else:
                raise HTTPException(404, "观察节点不存在。")
        except (IndexError, KeyError, TypeError, ValueError, StopIteration):
            raise HTTPException(404, "观察记录缺失或结构不完整。") from None
        try:
            result["ancestors"] = self._ancestors(kind, refs)
        except (HTTPException, KeyError, ValueError, IndexError):
            result["warnings"].append("部分上级历史记录已不可用。")
        try:
            result["timeline"], notes = self._timeline(kind, refs)
            result["warnings"] += notes
        except (HTTPException, KeyError, ValueError, IndexError, StopIteration):
            result["warnings"].append("部分时间线记录缺失或不可安全读取。")
        result["warnings"] = list(dict.fromkeys(result["warnings"]))
        diagnostic = self._diagnostic(kind, refs)
        if diagnostic:
            section("已保存错误诊断", diagnostic)
        result["active"] = (result["active"] or result["status"] in ACTIVE) and host_active is not False
        return clean(result)
