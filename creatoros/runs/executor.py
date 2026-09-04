from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from threading import Event, RLock
from uuid import uuid4

from .ownership import ExecutionOwnershipError
from .service import ContentRunError, ContentRunExecutionError, ContentRunService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunSubmission:
    run_id: str
    accepted: bool


class ManagedRunExecutor:
    """Capacity one, explicit submission, durable claims and bounded shutdown."""

    def __init__(self, service: ContentRunService, *, shutdown_timeout: float = 8.0):
        self.service = service
        self.shutdown_timeout = shutdown_timeout
        self.instance_id = f"studio-{uuid4().hex[:12]}"
        self._pool: ThreadPoolExecutor | None = None
        self._lock = RLock()
        self._active: tuple[str, str, Event, Future] | None = None
        self._closed = False

    def start(self):
        with self._lock:
            if self._closed:
                raise ExecutionOwnershipError("生产执行器已经关闭。")
            if self._pool is not None:
                return
            self.service.guard.__enter__()
            try:
                self.service.recover_inflight()
                self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="creatoros-run")
            except BaseException:
                self.service.guard.__exit__()
                raise

    def submit(self, run_id: str, *, expected_version: int) -> RunSubmission:
        with self._lock:
            self.start()
            if self._active is not None and not self._active[3].done():
                active_id = self._active[0]
                raise ContentRunError(
                    "已有内容正在生产，请查看当前运行，完成后再开始下一篇。",
                    code="already_running" if active_id == run_id else "producer_busy", run_id=active_id,
                )
            owner = f"{self.instance_id}:{uuid4().hex}"
            prepared = self.service.claim(run_id, owner_id=owner, expected_version=expected_version)
            cancel = Event()
            try:
                assert self._pool is not None
                future = self._pool.submit(self._run, prepared, owner, cancel)
            except BaseException:
                self.service.interrupt_owner(run_id, owner_id=owner, message="提交执行器失败，生产未启动，可显式恢复。")
                self.service.guard.finish(owner)
                raise
            self._active = (run_id, owner, cancel, future)
            return RunSubmission(run_id, accepted=True)

    def is_submitted(self, run_id: str) -> bool:
        with self._lock:
            return self._active is not None and self._active[0] == run_id and not self._active[3].done()

    def cancel(self, run_id: str, *, expected_version: int) -> None:
        self.service.cancel(run_id, expected_version=expected_version)

    def shutdown(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            active, pool = self._active, self._pool
        if pool is None:
            return
        try:
            if active is not None and not active[3].done():
                run_id, owner, cancel, future = active
                try:
                    self.service.interrupt_owner(run_id, owner_id=owner)
                finally:
                    cancel.set()
                try:
                    future.result(timeout=self.shutdown_timeout)
                except TimeoutError as error:
                    # Keep the OS lock and dirty journal while a writer may survive.
                    raise ExecutionOwnershipError("执行器未按时退出，保留独占锁；请核实旧执行者后再恢复。") from error
            pool.shutdown(wait=True, cancel_futures=True)
        except BaseException:
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            self.service.guard.__exit__()

    def _run(self, prepared: dict, owner_id: str, cancel: Event) -> None:
        try:
            self.service.execute_claimed(prepared, owner_id=owner_id, cancel_event=cancel)
        except (ContentRunError, ContentRunExecutionError):
            # The service has persisted the business failure or rejected a late owner.
            pass
        except Exception:
            logger.exception("ContentRun worker stopped unexpectedly; inspect execution journal")
