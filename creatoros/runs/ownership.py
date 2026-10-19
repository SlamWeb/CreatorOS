from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from threading import RLock

from filelock import FileLock, Timeout


class ExecutionOwnershipError(RuntimeError):
    """The local producer cannot safely acquire exclusive ownership."""


class LocalExecutionGuard:
    """OS lock plus a durable record of an execution that may have survived a crash."""

    def __init__(self, database):
        from creatoros.config import PROJECT_ROOT

        url = database.engine.url
        if url.get_backend_name() == "sqlite" and url.database not in {None, "", ":memory:"}:
            base = Path(url.database).resolve()
        else:
            base = PROJECT_ROOT / "data" / "creatoros"
        self.path = base.with_name(base.name + ".execution.lock")
        self.journal = base.with_name(base.name + ".execution.json")
        self.lock = FileLock(self.path, timeout=0, thread_local=False)
        self._mutex = RLock()
        self._registered: dict[str, dict] = {}

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.lock.acquire()
        except Timeout as error:
            raise ExecutionOwnershipError("已有 CreatorOS Web/CLI 占用此数据库，请先关闭该实例。") from error
        return self

    def __exit__(self, *_args):
        with self._mutex:
            self.lock.release()
            if not self.lock.is_locked:
                self._registered.clear()

    def assert_clean(self) -> None:
        with self._mutex:
            self._require_lock()
            if self.journal.exists() or self.journal.is_symlink() or self._registered:
                raise ExecutionOwnershipError(
                    "上次执行未确认停止，恢复已阻止。请检查 execution.json 中的 PID、创建时间和 owner；"
                    "确认旧进程及其子进程已退出后，人工归档该记录再启动。"
                )

    def _require_lock(self) -> None:
        if not self.lock.is_locked:
            raise ExecutionOwnershipError("尚未取得本地执行器独占锁。")

    def assert_dispatchable(self) -> None:
        """Only executions registered during this lock tenure may coexist."""
        with self._mutex:
            self._require_lock()
            self._current_records()

    def _current_records(self) -> list[dict]:
        if not self.journal.exists() and not self.journal.is_symlink():
            if self._registered:
                raise ExecutionOwnershipError("活跃执行记录丢失，禁止继续派发或覆盖。")
            return []
        records = self._read_records()
        actual = {record["owner_id"]: record for record in records}
        if actual != self._registered:
            raise ExecutionOwnershipError("执行记录包含未知执行者或已变化，禁止继续派发或覆盖。")
        return records

    def begin(self, *, owner_id: str, run_id: str, attempt_id: str) -> None:
        with self._mutex:
            self._require_lock()
            records = self._current_records()
            record = {"owner_id": owner_id, "run_id": run_id, "attempt_id": attempt_id,
                      "phase": "claimed", "host_pid": os.getpid()}
            self._validate_records([*records, record])
            self._save_records([*records, record])
            self._registered[owner_id] = deepcopy(record)

    def process_started(self, owner_id: str, identity: dict) -> None:
        with self._mutex:
            records, record = self._owned(owner_id)
            record.update(phase="process_running", process=deepcopy(identity))
            self._validate_records(records)
            self._save_records(records)
            self._registered[owner_id] = deepcopy(record)

    def process_stopped(self, owner_id: str) -> None:
        with self._mutex:
            records, record = self._owned(owner_id)
            record["phase"] = "process_stopped"
            self._save_records(records)
            self._registered[owner_id] = deepcopy(record)

    def finish(self, owner_id: str) -> None:
        with self._mutex:
            self._require_lock()
            # Preserve the existing explicit, single-record reconciliation hook.
            # It is never used to adopt a stale multi-execution journal.
            if not self._registered:
                records = self._read_records()
                if len(records) != 1 or records[0]["owner_id"] != owner_id:
                    raise ExecutionOwnershipError("执行记录 owner 已变化，拒绝覆盖。")
                record = records[0]
            else:
                records, record = self._owned(owner_id)
            if record["phase"] not in {"claimed", "process_stopped"}:
                raise ExecutionOwnershipError("旧子进程未确认回收，保留执行记录并禁止恢复。")
            remaining = [item for item in records if item["owner_id"] != owner_id]
            if remaining:
                self._save_records(remaining)
            else:
                self.journal.unlink()
            self._registered.pop(owner_id, None)

    def _owned(self, owner_id: str) -> tuple[list[dict], dict]:
        self._require_lock()
        records = self._current_records()
        for record in records:
            if record["owner_id"] == owner_id and owner_id in self._registered:
                return records, record
        raise ExecutionOwnershipError("执行记录 owner 未登记，拒绝覆盖。")

    @staticmethod
    def _validate_records(records: list[dict]) -> None:
        owners, runs, attempts = set(), set(), set()
        for record in records:
            if not isinstance(record, dict):
                raise ExecutionOwnershipError("执行记录格式无效，禁止继续派发。")
            for key, seen in (("owner_id", owners), ("run_id", runs), ("attempt_id", attempts)):
                value = record.get(key)
                if not isinstance(value, str) or not value.strip() or value in seen:
                    raise ExecutionOwnershipError("执行记录身份缺失或重复，禁止继续派发。")
                seen.add(value)
            pid = record.get("host_pid")
            if type(pid) is not int or pid <= 0 or record.get("phase") not in {
                "claimed", "process_running", "process_stopped"
            }:
                raise ExecutionOwnershipError("执行记录阶段或宿主身份无效，禁止继续派发。")
            if record["phase"] == "process_running" and not isinstance(record.get("process"), dict):
                raise ExecutionOwnershipError("执行记录缺少进程身份，禁止继续派发。")

    def _read_records(self) -> list[dict]:
        try:
            if self.journal.is_symlink():
                raise ValueError("linked journal")
            payload = json.loads(self.journal.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("not an object")
            if "executions" in payload or "schema_version" in payload:
                if set(payload) != {"schema_version", "executions"} or payload["schema_version"] != 2:
                    raise ValueError("unknown journal schema")
                records = payload["executions"]
                if not isinstance(records, list) or not records:
                    raise ValueError("empty executions")
            else:
                records = [payload]
            self._validate_records(records)
            return records
        except (OSError, ValueError, TypeError) as error:
            raise ExecutionOwnershipError("执行记录不可读取或格式无效，禁止继续派发或覆盖。") from error

    def _save_records(self, records: list[dict]) -> None:
        self._write(records[0] if len(records) == 1 else {"schema_version": 2, "executions": records})

    def _write(self, record: dict) -> None:
        temporary = self.journal.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.journal)
