"""Durable per-session guard for uncertain topic edits and production writes.

Only the host calls this module, with canonical validated ToolCall/ToolResult
objects. It neither dispatches nor retries business operations. A reservation
is persisted before dispatch so a crashed or interrupted call fails closed.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from filelock import FileLock

from creatoros.tools.results import ToolResult


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value):
    return sha256(_encoded(value).encode("utf-8")).hexdigest()


def _args(call):
    value = json.loads(call.arguments or "{}")
    if not isinstance(value, dict):
        raise ValueError("arguments must be an object")
    return value


def _target(call):
    """Return guarded identities; do not infer identities from user prose."""
    if call.name not in {"manage_topics", "start_content_run"}:
        return None
    args = _args(call)
    action = args.get("action")
    if call.name == "manage_topics" and action == "update":
        topic = args.get("topic_id")
        if not isinstance(topic, str) or not topic:
            raise ValueError("missing topic_id")
        return "topic_update", ["topic:" + topic], args
    if call.name != "start_content_run":
        return None
    if action == "tasks":
        topic = args.get("topic_id")
        if not isinstance(topic, str) or not topic:
            raise ValueError("missing topic_id")
        return "production_tasks", ["production-topic:" + topic], args
    if action == "start":
        topics = args.get("topic_ids")
        if not isinstance(topics, list) or not topics or any(not isinstance(x, str) or not x for x in topics):
            raise ValueError("missing topic_ids")
        # Topic order does not change the identity of a batch submission.
        args["topic_ids"] = sorted(topics)
        return "production_start", ["production-topic:" + x for x in sorted(set(topics))], args
    if action == "fresh":
        run = args.get("source_run_id")
        if not isinstance(run, str) or not run:
            raise ValueError("missing source_run_id")
        return "production_fresh", ["production-run:" + run], args
    if action == "reference":
        series, title = args.get("series_id"), args.get("title")
        if not isinstance(series, str) or not series or not isinstance(title, str) or not title:
            raise ValueError("missing series or title")
        return "production_reference", ["reference:" + _hash([series, title])], args
    return None


def _safe_pre_dispatch(result):
    # The same error_type can also be returned by an HTTP business endpoint
    # after another request has happened; only host stage evidence is decisive.
    return result.details.get("pre_dispatch") is True or result.error_type == "tool_not_exposed"


def _certain_business_rejection(record, result, data):
    # Topic edit checks an existing receipt first, then compares digest before
    # assigning fields inside the transaction. Generic 409 is not sufficient.
    return (record.get("kind") == "topic_update" and result.error_type == "conflict"
            and result.details.get("http_status") == 409 and isinstance(data, dict)
            and data.get("message") == "选题已发生变化，请重新读取后再修改。")


def _known_request_id(kind, args, context):
    if kind == "topic_update":
        from creatoros.tools.topics import TopicChanges, _write_identity
        patch = TopicChanges.model_validate(args["changes"]).model_dump(exclude_none=True)
        return _write_identity("update", args["topic_id"], {**patch, "expected_digest": args["expected_digest"]}, context)[0]
    if kind == "production_tasks":
        from creatoros.tools.production_arguments import StartRunArgs, production_tasks_payload
        from creatoros.integrations.production_identity import task_request_id
        request = StartRunArgs.model_validate(args)
        session = context.agent_session_id or str(context.session_file)
        return task_request_id(session, context.user_request_id, context.creator_id,
                               request.topic_id, production_tasks_payload(request))
    if kind == "production_fresh":
        from creatoros.integrations.production_identity import fresh_request_id
        session = context.agent_session_id or str(context.session_file)
        return fresh_request_id(session, context.user_request_id, args["source_run_id"])
    return None


_RUN_STATES = frozenset({"queued", "producing", "validating", "awaiting_approval", "approved",
                         "interrupted", "failed", "cancelled"})
_TASK_STATES = _RUN_STATES | {"waiting", "dispatched"}
_DISPATCH_STATES = frozenset({"waiting", "dispatched", "cancelled"})
_QUEUE_STATES = frozenset({"pending", "running", "succeeded", "failed", "cancelled"})


def _is_confirmed_success(record, data):
    if not isinstance(data, dict) or data.get("status") in ("unknown", "needs_attention") or data.get("outcome_unknown"):
        return False
    kind = record["kind"]
    if kind == "topic_update":
        return (data.get("ok") is True and isinstance(data.get("topic"), dict)
                and "topic:" + str(data["topic"].get("id")) in record["targets"]
                and isinstance(record.get("request_id"), str) and data.get("request_id") == record["request_id"]
                and isinstance(record.get("creator_id"), str) and data.get("creator_id") == record["creator_id"])
    if kind == "production_tasks":
        rows = data.get("items")
        return ("production-topic:" + str(data.get("topic_id")) in record["targets"]
                and data.get("creator_id") == record.get("creator_id") and bool(record.get("creator_id"))
                and isinstance(rows, list) and len(rows) == record.get("expected_count")
                and all(isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"]
                        and row.get("topic_id") == data["topic_id"]
                        and isinstance(row.get("status"), str) and row["status"] in _TASK_STATES
                        and isinstance(row.get("dispatch_state"), str) and row["dispatch_state"] in _DISPATCH_STATES
                        and (row.get("error_code") is None or isinstance(row.get("error_code"), str))
                        and row.get("error_code") not in ("studio_outcome_unknown", "queue_needs_attention")
                        for row in rows)
                and len({row["id"] for row in rows}) == len(rows))
    if kind == "production_start":
        rows = data.get("items")
        topics = record.get("expected_topics")
        return (isinstance(topics, list) and isinstance(rows, list) and len(rows) == len(topics)
                and all(isinstance(row, dict) and isinstance(row.get("topic_id"), str)
                        and isinstance(row.get("status"), str) and row["status"] in _RUN_STATES | {"rejected"}
                        and isinstance(row.get("accepted"), bool)
                        and ((row["status"] == "rejected" and not row["accepted"]
                              and isinstance(row.get("error_code"), str) and bool(row["error_code"]))
                             or (row["status"] != "rejected" and isinstance(row.get("run_id"), str)
                                 and bool(row["run_id"])))
                        and (row.get("error_code") is None or isinstance(row.get("error_code"), str))
                        and row.get("error_code") not in ("studio_outcome_unknown", "queue_needs_attention", "execution_ownership")
                        for row in rows)
                and Counter(row["topic_id"] for row in rows) == Counter(topics)
                and type(data.get("accepted_count")) is int
                and data["accepted_count"] == sum(row["accepted"] for row in rows))
    if kind in {"production_fresh", "production_reference"}:
        queue = data.get("production_queue")
        return (isinstance(data.get("run_id"), str) and bool(data["run_id"])
                and isinstance(data.get("status"), str) and data["status"] in _RUN_STATES
                and isinstance(data.get("accepted"), bool)
                and data.get("creator_id") == record.get("creator_id") and bool(record.get("creator_id"))
                and isinstance(data.get("topic_id"), str) and bool(data["topic_id"])
                and (kind != "production_reference" or data.get("series_id") == record.get("series_id"))
                and "production_queue" in data
                and (queue is None or (isinstance(queue, dict) and isinstance(queue.get("status"), str)
                                       and queue["status"] in _QUEUE_STATES)))
    return False


class UnknownWriteGuard:
    """Persistent unknown write reservations for a single bound account/session.

    Call prepare, mark_dispatched immediately before the canonical executor,
    then finish with its real ToolResult. Any persistence exception must abort
    dispatch/turn; prepared or dispatched records remain blocked after restart.
    Only trusted receipt reads may call observe_read.
    """

    def __init__(self, context):
        if not context.creator_id or context.session_file is None:
            raise ValueError("guard requires a bound account and session")
        self.context = context
        path = Path(context.session_file).resolve()
        self.path = path.with_suffix(".unknown-writes.json")
        self.lock = FileLock(str(path.with_suffix(".unknown-writes.lock")), timeout=10)
        self.scope = _hash({"creator_id": context.creator_id, "session": str(path)})

    def _load(self):
        if not self.path.exists():
            return {"version": 1, "scope": self.scope, "records": []}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if (not isinstance(value, dict) or value.get("version") != 1 or value.get("scope") != self.scope
                    or not isinstance(value.get("records"), list)):
                raise ValueError("scope or structure mismatch")
            for record in value["records"]:
                if (not isinstance(record, dict) or record.get("phase") not in {"prepared", "dispatched", "unknown", "confirmed"}
                        or not isinstance(record.get("id"), str) or not isinstance(record.get("targets"), list)
                        or any(not isinstance(x, str) for x in record["targets"])):
                    raise ValueError("invalid guard record")
            return value
        except (ValueError, OSError, TypeError) as exc:
            raise RuntimeError("未知写入保护记录无效，已阻止执行。") from exc

    def _save(self, value):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = None
        try:
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                    prefix=self.path.name + ".", suffix=".tmp", delete=False) as f:
                tmp = Path(f.name)
                f.write(_encoded(value))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        finally:
            if tmp is not None and tmp.exists():
                tmp.unlink()

    def has_pending_production(self):
        """Read the existing protection record without changing its state."""
        with self.lock:
            state = self._load()
            return any(record.get("creator_id") == self.context.creator_id
                       and isinstance(record.get("kind"), str)
                       and record["kind"].startswith("production_")
                       and record["phase"] in {"prepared", "dispatched", "unknown"}
                       for record in state["records"])

    def prepare(self, call):
        """Return (reservation_id, blocked_result), or (None, None) for a read."""
        operation = _target(call)
        if operation is None:
            return None, None
        if not self.context.user_request_id:
            return None, ToolResult("当前请求缺少用户轮身份，未执行写操作。", True, "guard_scope_missing",
                                    details={"pre_dispatch": True})
        kind, targets, args = operation
        fingerprint = _hash([call.name, args])
        with self.lock:
            state = self._load()
            for record in state["records"]:
                previous_kind = record.get("kind", "")
                overlap = bool(set(targets).intersection(record["targets"]))
                # A source Run or reference title cannot prove which Topic it
                # will affect. Until the write is reconciled, prevent changing
                # entry points to bypass the reservation in this session.
                uncertain_production_identity = (
                    record["phase"] != "confirmed"
                    and kind.startswith("production_") and previous_kind.startswith("production_")
                    and ({kind, previous_kind} & {"production_fresh", "production_reference"})
                )
                if not overlap and not uncertain_production_identity:
                    continue
                if record["phase"] != "confirmed" or (record.get("turn") == self.context.user_request_id
                                                      and record.get("fingerprint") == fingerprint):
                    notice = ("原请求已确认受理；同一用户轮未再次执行。" if record["phase"] == "confirmed" else
                              "先前写入结果尚需核对，生产目标可能关联，本次未执行；请查询原请求回执，不要重复提交。"
                              if uncertain_production_identity and not overlap else
                              "同一目标的先前写入结果尚需核对，本次未执行；请查询原请求回执，不要重复提交。")
                    return None, ToolResult(notice,
                                            True, "write_outcome_pending", details={"pre_dispatch": True})
            token = uuid4().hex
            request_id = _known_request_id(kind, args, self.context)
            state["records"].append({"id": token, "phase": "prepared", "kind": kind, "targets": targets,
                                     "turn": self.context.user_request_id, "fingerprint": fingerprint,
                                     "request_id": request_id, "creator_id": self.context.creator_id,
                                     "series_id": args.get("series_id") if kind == "production_reference" else None,
                                     # RunBatchStartRequest strips and deduplicates IDs before iterating.
                                     "expected_topics": sorted({value.strip() for value in args["topic_ids"]})
                                     if kind == "production_start" else None,
                                     "expected_count": len(args.get("tasks", [])) if kind == "production_tasks" else None})
            self._save(state)
        return token, None

    def mark_dispatched(self, token):
        if token is None:
            return
        with self.lock:
            state = self._load()
            record = next((r for r in state["records"] if r["id"] == token), None)
            if record is None or record["phase"] != "prepared":
                raise RuntimeError("写入保护预留无效，已阻止执行。")
            record["phase"] = "dispatched"
            self._save(state)

    def finish(self, token, result):
        if token is None:
            return
        if not isinstance(result, ToolResult):
            raise ValueError("trusted ToolResult required")
        try:
            data = json.loads(result.content)
        except (TypeError, ValueError):
            data = None
        with self.lock:
            state = self._load()
            record = next((r for r in state["records"] if r["id"] == token), None)
            if record is None or record["phase"] != "dispatched":
                raise RuntimeError("写入保护记录不匹配，结果需核对。")
            certain_failure = result.is_error and (_safe_pre_dispatch(result)
                                                    or _certain_business_rejection(record, result, data))
            successful = not result.is_error and _is_confirmed_success(record, data)
            if certain_failure:
                state["records"] = [r for r in state["records"] if r["id"] != token]
            elif successful:
                record["phase"] = "confirmed"
            else:
                record["phase"] = "unknown"
                if isinstance(data, dict):
                    request_id = data.get("request_id")
                    if isinstance(request_id, str) and request_id and (record.get("request_id") is None
                                                                      or request_id == record["request_id"]):
                        record["request_id"] = request_id
                    topic_id = data.get("topic_id")
                    if isinstance(topic_id, str) and topic_id and record["kind"].startswith("production_"):
                        target = "production-topic:" + topic_id
                        if target not in record["targets"]:
                            record["targets"].append(target)
            self._save(state)
            if not successful and not certain_failure:
                # Neither an incomplete 2xx nor a post-dispatch error proves
                # the write did not happen. Preserve the original error and
                # exact receipt while telling both the model and UI to check.
                payload = dict(data) if isinstance(data, dict) else {}
                if "message" in payload:
                    payload["upstream_message"] = payload["message"]
                payload.update(error=payload.get("error") or result.error_type or "studio_outcome_unknown",
                               outcome_unknown=True, message="写入结果待核对，不要重复提交。",
                               upstream_raw_content=result.content)
                if not payload.get("request_id"):
                    payload["request_id"] = record.get("request_id")
                topics = [target.split(":", 1)[1] for target in record["targets"]
                          if target.startswith(("topic:", "production-topic:"))]
                if len(topics) == 1 and "topic_id" not in payload:
                    payload["topic_id"] = topics[0]
                return ToolResult(_encoded(payload), True, result.error_type or "studio_outcome_unknown", retryable=False,
                                  details={**result.details, "outcome_unknown": True})
            return None

    def observe_read(self, call, result):
        """Unfreeze only a matching successful production receipt, never a plain get."""
        if call.name != "manage_topics" or result.is_error:
            return False
        try:
            args, data = _args(call), json.loads(result.content)
            receipt = data.get("production_receipt")
            topic, request_id = args.get("topic_id"), args.get("production_request_id")
            if (args.get("action") != "get" or not isinstance(topic, str) or not isinstance(request_id, str)
                    or not isinstance(receipt, dict) or receipt.get("status") != "accepted"
                    or receipt.get("request_id") != request_id or receipt.get("topic_id") != topic):
                return False
        except (TypeError, ValueError, AttributeError):
            return False
        with self.lock:
            state = self._load()
            matches = [r for r in state["records"] if r["phase"] == "unknown" and r["kind"] == "production_tasks"
                       and r.get("request_id") == request_id and "production-topic:" + topic in r["targets"]]
            for record in matches:
                record["phase"] = "confirmed"
            if matches:
                self._save(state)
            return bool(matches)
