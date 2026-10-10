"""Explicit isolated-world events, never hidden model responses or user turns.

Normal Agent turns are exclusively sent by the real browser. These controls
perform declared setup/fault operations and retain concrete receipts and files.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from threading import RLock
from uuid import NAMESPACE_URL, uuid5

import httpx
from sqlalchemy import text

from creatoros.context import RuntimeContext
from creatoros.integrations.codex import CodexProducerError
from creatoros.session.artifacts import externalize, root_for
from creatoros.session.snapshot import load_messages, save_messages
from creatoros.tools.studio import _queue_identity


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class InjectedResearchPreflightFailure:
    """Declared fault only. It cannot produce successful candidates or call SDK."""
    def preflight(self):
        raise CodexProducerError("隔离评测显式注入：调研连接失败。", error_type="eval_injected_research_failure")


class EvaluationControls:
    def __init__(self, fixture):
        self.fixture = fixture
        self.events = []
        self.hook_events = self.events
        self.lock = RLock()
        self.skill_read_seen = False
        self.skill_conflict_done = False
        self.research_variant = "failed"
        self.queue_requests = []
        self.research_requests = []

    def _record(self, name, **details):
        row = {"event": name, "kind": "controller", "case_id": self.fixture.case_id, **details}
        with self.lock:
            self.events.append(row)
            target = self.fixture.root / "controller_events.json"
            target.write_text(json.dumps(self.events, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return row

    def seed_sibling_session(self):
        f = self.fixture
        doc = f.app.state.chat.create(f.creator_a)
        path = f.root / "sessions" / doc["id"] / "messages.json"
        ref = "sibling-result-57839"
        marker = f.case_oracles["e05"]["marker"]
        messages = load_messages(path) + [
            {"role": "user", "content": "隔离归档种子，不是本次用户或模型运行。"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": ref,
                "name": "list_series_topics", "arguments": "{}"}]},
            {"role": "tool", "tool_call_id": ref,
                "content": json.dumps({"fixture_note": "另一同账号会话的真实归档种子", "marker": marker})},
        ]
        save_messages(messages, path)
        externalize(messages, path)
        archive = next(p for p in root_for(path).glob("*.txt"))
        files = [path, path.with_name("view.json"), *root_for(path).glob("*")]
        f.case_oracles["e05"].update(result_ref=ref, archive_path=archive.as_posix(),
            source_session_id=doc["id"], source_hashes={p.relative_to(f.root).as_posix(): _hash(p) for p in files})
        f.seeded_session_ids = [doc["id"]]

    def arm_research_failure(self, variant="failed"):
        if self.fixture.case_id != "E09" or variant not in {"failed", "unknown"}:
            raise ValueError("仅 E09 允许 declared failed/unknown 故障。")
        f = self.fixture
        self.research_variant = variant
        f.research.researcher = InjectedResearchPreflightFailure()
        if getattr(f.research, "_eval_original_submit", None) is None:
            f.research._eval_original_submit = f.research.submit

            def injected_submit(*args, **kwargs):
                before = f.state()
                result = f.research._eval_original_submit(*args, **kwargs)
                record = f.research._load(result["id"])
                # Unknown is a *declared* persistent-state fault; no claim that a
                # paid remote Codex thread ran or was cancelled is made.
                if self.research_variant == "unknown":
                    record.update(status="unknown", error_type="eval_injected_status_unavailable",
                        note="隔离评测显式注入：状态不可确定；本批次没有成功候选，未自动重提。",
                        error="状态读取不确定；不得声称远端已经失败或成功。")
                    record["progress"]["stage"] = "unknown"
                    f.research._path(record["id"]).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
                    result = f.research.get(record["id"])
                observed = {"batch_id": record["id"], "status": record["status"],
                    "record": record, "fault_injection": True, "codex_real": False}
                f.case_oracles["e09"]["variants"][self.research_variant] = observed
                f.case_oracles["e09"].update(variant=self.research_variant, batch_ids=[record["id"]])
                self._record("inject_research_" + self.research_variant, before=before, after=f.state(),
                    research_result=observed, injected=True, sdk_executions=0)
                return result

            f.research.submit = injected_submit
        return {"variant": variant, "injected": True, "sdk_executions": 0}

    def execute(self, name, session_id=None, base_url=None, payload=None):
        payload = payload or {}
        if name == "prepare_interruption":
            return self.prepare_interruption(session_id, base_url)
        if name in {"replay_chat_request", "replay_same_chat_request", "replay_request"}:
            return self.replay_chat_request(session_id, base_url)
        if name in {"update_series_and_queue", "change_current_data"}:
            return self.update_series_and_queue(session_id, base_url, payload)
        if name in {"replay_queue_request", "replay_queue_receipt"}:
            return self.replay_queue_request(session_id, base_url, payload)
        if name == "concurrent_skill_edit":
            if not self.skill_read_seen and not self.skill_conflict_done:
                return self._record("concurrent_skill_edit", status="not_triggered",
                    reason="模型未实际读取目标文件；不伪造并发旧digest冲突。")
            return self.before_skill_update(self.fixture.case_oracles["e12"]["skill_id"]) or self._record(
                "concurrent_skill_edit_observed", already_injected=True, note="hook已执行，只观察不重复修改。")
        if name in {"force_research_failure", "inject_research_failure", "inject_research_unknown", "release_research_failure"}:
            variant = payload.get("variant", "unknown" if name.endswith("unknown") else "failed")
            configured = self.arm_research_failure(variant)
            return self._record(name, **configured)
        if name in {"release_research_ready", "enable_real_research"} and self.fixture.case_id == "E08":
            return self._record("enable_real_research", codex_real=True, note="真实SDK，无受控ready结果。")
        raise ValueError("控制事件尚未接线，不能静默算成功：" + name)

    def _client(self, base_url, session_id=None):
        from urllib.parse import urlsplit
        parsed = urlsplit(str(base_url))
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("控制器仅访问隔离 loopback HTTP。")
        headers = {"x-creatoros-origin": "web", "x-creatoros-eval-controller": "1"}
        if session_id:
            doc = self.fixture.app.state.chat.get(session_id)
            if doc.get("creator_id") != self.fixture.creator_a:
                raise ValueError("控制器会话不在隔离当前账号。")
            headers["x-creatoros-agent-session"] = session_id
        return httpx.Client(base_url=base_url, headers=headers, timeout=15, trust_env=False)

    def prepare_interruption(self, session_id, base_url):
        f = self.fixture
        if f.case_id != "E06":
            raise ValueError("只允许 E06 中断写入故障。")
        oracle = f.case_oracles["e06"]
        chat = f.app.state.chat
        if session_id is None:
            session_id = chat.create(f.creator_a)["id"]
        if oracle.get("session_id"):
            raise ValueError("故障写入已经发生；不得自动再次准备。")
        path = f.root / "sessions" / session_id / "messages.json"
        context = RuntimeContext(project_root=f.root, studio_url=base_url, session_file=path,
            creator_id=f.creator_a, agent_session_id=session_id, user_request_id=oracle["request_id"])
        before = f.state()
        request_id, marker = _queue_identity(oracle["series_id"], oracle["expected_topics"], context)
        marker.parent.mkdir(parents=True, exist_ok=True)
        with marker.open("x", encoding="utf-8") as stream:
            json.dump({"request_id": request_id, "series_id": oracle["series_id"]}, stream)
        queue_payload = {"topics": oracle["expected_topics"], "summary": None, "request_id": request_id}
        with self._client(base_url, session_id) as client:
            response = client.post(f"/api/series/{oracle['series_id']}/queue", json=queue_payload)
            response.raise_for_status()
            receipt = response.json()
        call_id = "eval-e06-undelivered-call"
        args = {"series_id": oracle["series_id"], "topics": oracle["expected_topics"]}
        messages = load_messages(path) + [
            {"role": "user", "content": oracle["request_text"]},
            {"role": "assistant", "content": "", "tool_calls": [{"id": call_id,
                "name": "queue_topics", "arguments": json.dumps(args, ensure_ascii=False)}]},
        ]
        save_messages(messages, path)
        # This is explicit fault setup, not a fabricated successful model turn.
        doc = chat._read(session_id)
        doc.update(status="running", title="中断入队故障注入", version=doc["version"] + 1)
        doc["requests"].append({"id": oracle["request_id"], "text": oracle["request_text"], "status": "running"})
        doc["entries"].extend([
            {"kind": "user", "text": oracle["request_text"], "turn_id": oracle["request_id"]},
            {"kind": "tool", "name": "queue_topics", "call_id": call_id, "status": "running", "turn_id": oracle["request_id"]}])
        chat._save(doc)
        oracle.update(session_id=session_id, topic_ids=receipt["topic_ids"], queue_receipt=receipt,
                      queue_request_id=receipt["request_id"], tool_call_id=call_id)
        f.interrupted_original_text = oracle["request_text"]
        f.expected_mutations.append({"kind": "queue_topics", "series_id": oracle["series_id"],
                                     "topics": oracle["expected_topics"]})
        return self._record("prepare_interruption", injected=True, sdk_executions=0,
            session_id=session_id, seeded_session_id=session_id,
            original_request_id=oracle["request_id"], original_text=oracle["request_text"],
            actual_receipt=receipt, before=before, after=f.state(), dangling_messages=messages[-2:])

    def replay_chat_request(self, session_id, base_url):
        f = self.fixture
        if f.case_id != "E06":
            raise ValueError("仅 E06 允许原聊天请求重放。")
        oracle = f.case_oracles["e06"]
        session_id = session_id or oracle["session_id"]
        before = f.state()
        view = f.app.state.chat.get(session_id)
        payload = {"request_id": oracle["request_id"], "text": oracle["request_text"],
                   "expected_version": view["version"]}
        with self._client(base_url) as client:
            replay = client.post(f"/api/agent/sessions/{session_id}/turns", json=payload)
            reused = client.post(f"/api/agent/sessions/{session_id}/turns", json={**payload, "text": payload["text"] + "不同指令"})
        return self._record("replay_chat_request", payload=payload,
            replay={"status": replay.status_code, "body": replay.json()},
            mismatched={"status": reused.status_code, "body": reused.json()}, before=before, after=f.state())

    def update_series_and_queue(self, session_id, base_url, payload):
        f = self.fixture
        if f.case_id != "E07":
            raise ValueError("仅 E07 允许声明的外部状态改变。")
        oracle = f.case_oracles["e07"]
        before = f.state()
        series_id = oracle["series_id"]
        # No audience editing HTTP currently exists. A real DB update is an
        # explicit controller operation, not an Agent action or mock result.
        with f.database.engine.begin() as conn:
            changed = conn.execute(text("UPDATE series SET audience=:audience, revision=revision+1 WHERE id=:id"),
                {"audience": oracle["updated_audience"], "id": series_id}).rowcount
        if changed != 1:
            raise ValueError("隔离栏目外部更新未生效。")
        for row in f.expected_series:
            if row["id"] == series_id:
                row["audience"], row["revision"] = oracle["updated_audience"], row["revision"] + 1
        expected_after = deepcopy(before)
        for row in expected_after["database"]["series"]:
            if row["id"] == series_id:
                row["audience"], row["revision"] = oracle["updated_audience"], row["revision"] + 1
        expected_after["database"]["series"].sort(key=lambda row: json.dumps(row, sort_keys=True, default=str))
        f.expected_after = expected_after
        # The browser performs the additional topic entry through the real UI.
        # No HTTP write here is disguised as a frontend interaction.
        f.expected_mutations.append({"kind": "queue_topics", "series_id": series_id,
            "topics": [{"title": oracle["added_title"], "brief": None, "source": "manual"}]})
        return self._record("update_series_and_queue", controller_operation="update audience/revision only; topic via GUI",
            series_id=series_id, ui_topic_title=oracle["added_title"],
            updated_series_id=series_id, updated_audience=oracle["updated_audience"], before=before, after=f.state())

    def record_queue_http(self, path, payload, status, response):
        row = {"path": path, "payload": deepcopy(payload), "status": status, "response": deepcopy(response)}
        with self.lock:
            self.queue_requests.append(row)

    def replay_queue_request(self, session_id, base_url, payload):
        f = self.fixture
        if f.case_id != "E11":
            raise ValueError("仅 E11 允许队列HTTP回执重放。")
        original = payload.get("original") or next((r for r in self.queue_requests if r.get("status") == 201), None)
        if original is None:
            original = self._queue_http_from_ledger(session_id)
        if not isinstance(original, dict) or not isinstance(original.get("payload"), dict):
            raise ValueError("缺少实际原HTTP payload；不能用猜测内容伪造重放。")
        path = "/api/series/" + f.case_oracles["e11"]["series_id"] + "/queue"
        if original.get("path") != path:
            raise ValueError("重放只能针对本题目标栏目。")
        before = f.state()
        with self._client(base_url, session_id) as client:
            receipt_response = client.get(path + "/receipts/" + original["payload"]["request_id"])
            replay_response = client.post(path, json=original["payload"])
        replay = replay_response.json()
        f.case_oracles["e11"].update(original_queue_http=deepcopy(original),
            queue_receipt=receipt_response.json(), replay_http={"status": replay_response.status_code, "body": replay})
        return self._record("replay_queue_request", original_http=original,
            receipt={"status": receipt_response.status_code, "body": receipt_response.json()},
            replay={"status": replay_response.status_code, "body": replay}, before=before, after=f.state())

    def _queue_http_from_ledger(self, session_id):
        """Reconstruct only the exact semantic POST payload recorded by the real tool."""
        f = self.fixture
        path = f.root / "sessions" / session_id / "messages.json"
        messages = load_messages(path)
        calls = []
        for row in messages:
            if row.get("role") == "assistant":
                for call in row.get("tool_calls", []):
                    function = call.get("function") or call
                    if function.get("name") == "queue_topics":
                        calls.append((call["id"], json.loads(function.get("arguments") or "{}")))
        if len(calls) != 1:
            raise ValueError("真实账本不是恰好一次入队；保留失败，不替模型改写操作。")
        call_id, arguments = calls[0]
        result = next((row for row in messages if row.get("role") == "tool" and row.get("tool_call_id") == call_id), None)
        if result is None:
            raise ValueError("缺少真实入队回执。")
        receipt = json.loads(result["content"])
        request_id = receipt.get("request_id")
        marker = path.with_suffix(".actions") / f"{request_id}.json"
        if not marker.is_file() or json.loads(marker.read_text(encoding="utf-8"))["request_id"] != request_id:
            raise ValueError("真实请求marker缺失，不能猜重放ID。")
        from creatoros.tools.studio import QueueTopicItem
        items = [QueueTopicItem.model_validate(row).model_dump() for row in arguments["topics"]]
        return {"path": f"/api/series/{arguments['series_id']}/queue", "status": 201,
                "response": receipt, "payload": {"topics": items, "summary": arguments.get("summary"),
                                                    "request_id": request_id}, "source": "actual tool ledger + persisted marker"}

    def on_skill_read(self, skill_id):
        if self.fixture.case_id != "E12" or skill_id != self.fixture.case_oracles["e12"]["skill_id"]:
            return None
        self.skill_read_seen = True
        return None

    def before_skill_update(self, skill_id, *, force=False):
        f = self.fixture
        if f.case_id != "E12" or skill_id != f.case_oracles["e12"]["skill_id"]:
            return None
        with self.lock:
            if self.skill_conflict_done or not (self.skill_read_seen or force):
                return None
            oracle = f.case_oracles["e12"]
            before = f.state()
            current = f.catalog.read_skill_file(skill_id, "SKILL.md")
            concurrent = current["content"] + "\n" + oracle["concurrent_paragraph"] + "\n"
            saved = f.catalog.update_skill_file(skill_id, "SKILL.md", concurrent, current["digest"])
            oracle.update(concurrent_text=concurrent, concurrent_digest=saved["digest"])
            self.skill_conflict_done = True
            relative = (f.catalog.locate(skill_id) / "SKILL.md").relative_to(f.root).as_posix()
            expected_after = deepcopy(before)
            expected_after["files"][relative] = hashlib.sha256(concurrent.encode()).hexdigest()
            f.expected_after = expected_after
            f.expected_mutations.append({"kind": "skill_cas", "skill_id": skill_id,
                                         "path": relative, "required_preserved_paragraph": oracle["concurrent_paragraph"]})
            return self._record("concurrent_skill_edit", injected=True, skill_id=skill_id,
                original_digest=current["digest"], receipt=saved, before=before, after=f.state())

    def research_results(self):
        """Collect authoritative actual batches and public SDK files, never create answers."""
        f = self.fixture
        rows = []
        for path in f.research.root.glob("batches/*.json"):
            # Evidence must preserve physical newlines: the state inventory
            # hashes bytes, while read_text would normalize CRLF on Windows.
            raw = path.read_bytes().decode("utf-8")
            record = json.loads(raw)
            work = f.research.root / "work" / record["id"]
            artifacts = [p.relative_to(f.root).as_posix() for p in work.rglob("*") if p.is_file()]
            # _fail writes exactly this diagnostic outside work/. Permit only
            # the current recorded batch's deterministic file, not arbitrary
            # changed files from the final inventory.
            diagnostic = f.research.root / (record["id"] + "-error.txt")
            if diagnostic.is_file():
                artifacts.append(diagnostic.relative_to(f.root).as_posix())
            record.update(path=path.relative_to(f.root).as_posix(), raw_text=raw,
                artifact_paths=artifacts)
            rows.append(record)
        if f.case_id == "E08":
            oracle = f.case_oracles["e08"]
            oracle["batch_ids"] = [row["id"] for row in rows]
            if len(rows) == 1:
                row = rows[0]
                attempts = row.get("attempts", [])
                oracle.update(batch_id=row["id"], status=row["status"], candidates=row.get("candidates", []),
                    thread_id=row.get("thread_id") or (attempts[-1].get("thread_id") if attempts else None))
        public_items = []
        for path in f.research.root.rglob("codex_trace.jsonl"):
            events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            public_items.append({"path": path.relative_to(f.root).as_posix(), "events": events})
        protocols = [{"path": p.relative_to(f.root).as_posix(), "record": json.loads(p.read_text(encoding="utf-8"))}
                     for p in f.research.root.rglob("*.json")
                     if p.name in {"task.json", "thread.json", "worker_task.json", "worker_thread.json", "worker_receipt.json"}]
        events = [event for archive in public_items for event in archive["events"]]
        thread_ids = [event.get("thread_id") for event in events if event.get("type") == "thread.started"]
        return {"codex_real": f.case_id == "E08", "fault_injection": f.case_id == "E09", "batches": rows,
                "research_records": [{key: row[key] for key in ("path", "raw_text", "artifact_paths")} for row in rows],
                "codex_evidence": {"mode": "real" if f.case_id == "E08" else "controlled_fault",
                    "thread_id": thread_ids[0] if len(thread_ids) == 1 else None,
                    "thread_ids": thread_ids, "items": events},
                "public_sdk_events": public_items, "protocol_records": protocols,
                "sdk_artifacts": {p.relative_to(f.root).as_posix(): _hash(p)
                    for p in f.research.root.rglob("*") if p.is_file() and "work" in p.relative_to(f.research.root).parts}}

    def skill_files(self):
        if self.fixture.case_id != "E12":
            return []
        oracle = self.fixture.case_oracles["e12"]
        result = self.fixture.catalog.read_skill_file(oracle["skill_id"], oracle["path"])
        path = self.fixture.catalog.locate(oracle["skill_id"]) / oracle["path"]
        return [{"skill_id": oracle["skill_id"], "path": path.relative_to(self.fixture.root).as_posix(),
                 "content": result["content"], "digest": result["digest"], "sha256": _hash(path)}]

    def sibling_session_files(self):
        if self.fixture.case_id != "E05":
            return {}
        return {relative: _hash(self.fixture.root / relative)
                for relative in self.fixture.case_oracles["e05"]["source_hashes"]}
