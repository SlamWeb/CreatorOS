from __future__ import annotations

import json
import os
from pathlib import Path

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

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.lock.acquire()
        except Timeout as error:
            raise ExecutionOwnershipError("已有 CreatorOS Web/CLI 占用此数据库，请先关闭该实例。") from error
        return self

    def __exit__(self, *_args):
        self.lock.release()

    def assert_clean(self) -> None:
        if not self.lock.is_locked:
            raise ExecutionOwnershipError("尚未取得本地执行器独占锁。")
        if self.journal.exists():
            raise ExecutionOwnershipError(
                "上次执行未确认停止，恢复已阻止。请检查 execution.json 中的 PID、创建时间和 owner；"
                "确认旧进程及其子进程已退出后，人工归档该记录再启动。"
            )

    def begin(self, *, owner_id: str, run_id: str, attempt_id: str) -> None:
        self.assert_clean()
        self._write({"owner_id": owner_id, "run_id": run_id, "attempt_id": attempt_id,
                     "phase": "claimed", "host_pid": os.getpid()})

    def process_started(self, owner_id: str, identity: dict) -> None:
        record = self._owned(owner_id)
        record.update(phase="process_running", process=identity)
        self._write(record)

    def process_stopped(self, owner_id: str) -> None:
        record = self._owned(owner_id)
        record["phase"] = "process_stopped"
        self._write(record)

    def finish(self, owner_id: str) -> None:
        record = self._owned(owner_id)
        if record["phase"] not in {"claimed", "process_stopped"}:
            raise ExecutionOwnershipError("旧子进程未确认回收，保留执行记录并禁止恢复。")
        self.journal.unlink()

    def _owned(self, owner_id: str) -> dict:
        record = json.loads(self.journal.read_text(encoding="utf-8"))
        if record.get("owner_id") != owner_id:
            raise ExecutionOwnershipError("执行记录 owner 已变化，拒绝覆盖。")
        return record

    def _write(self, record: dict) -> None:
        temporary = self.journal.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.journal)
