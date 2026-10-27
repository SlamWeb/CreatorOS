from __future__ import annotations

import logging
import os
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from threading import Event, RLock, Thread
from time import monotonic
from uuid import uuid4

from .ownership import ExecutionOwnershipError
from .service import ContentRunError, ContentRunExecutionError, ContentRunService
from .queue import ProductionQueue

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunSubmission:
    run_id: str
    accepted: bool
    queue_status: str = "pending"


@dataclass(frozen=True)
class ActiveRun:
    owner_id: str
    cancel: Event
    future: Future
    topic_id: str
    task_id: str | None = None


class ManagedRunExecutor:
    """Durable explicit submissions, bounded topic concurrency and shutdown."""

    def __init__(self, service: ContentRunService, *, shutdown_timeout: float = 8.0,
                 max_concurrency: int | None = None):
        self.service = service
        self.shutdown_timeout = shutdown_timeout
        if max_concurrency is None:
            try:
                max_concurrency = int(os.getenv("CREATOROS_PRODUCTION_CONCURRENCY", "30"))
            except ValueError as error:
                raise ValueError("CREATOROS_PRODUCTION_CONCURRENCY 必须为1–30的整数。") from error
        if type(max_concurrency) is not int or not 1 <= max_concurrency <= 30:
            raise ValueError("生产并发数必须为1–30的整数。")
        self.max_concurrency = max_concurrency
        self.instance_id = f"studio-{uuid4().hex[:12]}"
        self._pool: ThreadPoolExecutor | None = None
        self._lock = RLock()
        self._active: dict[str, ActiveRun] = {}
        self._closed = False
        self.queue = ProductionQueue(service)
        self._wake = Event()
        self._stop = Event()
        self._dispatcher: Thread | None = None
        self._shutdown_marker: Thread | None = None
        self._blocked_error: str | None = None

    def start(self):
        with self._lock:
            if self._closed:
                raise ExecutionOwnershipError("生产执行器已经关闭。")
            if self._blocked_error:
                raise ExecutionOwnershipError(self._blocked_error)
            if self._pool is not None:
                return
            self.service.guard.__enter__()
            try:
                self.service.recover_inflight()
                self.queue.recover()
                self.queue.pause_pending(None)
                self._pool = ThreadPoolExecutor(max_workers=self.max_concurrency, thread_name_prefix="creatoros-run")
                self._dispatcher = Thread(target=self._consume, daemon=True, name="creatoros-production-queue")
                self._dispatcher.start()
            except BaseException as error:
                if self._pool is not None:
                    self._pool.shutdown(wait=False, cancel_futures=True)
                    self._pool = None
                self.service.guard.__exit__()
                self._pause(str(error))
                raise

    def submit(self, run_id: str, *, expected_version: int) -> RunSubmission:
        with self._lock:
            self.start()
            blocked = self._readiness_error()
            if blocked:
                raise ExecutionOwnershipError(blocked)
            item = self.queue.enqueue(run_id, expected_version)
            self._wake.set()
            return RunSubmission(run_id, accepted=True, queue_status=item.status)

    def is_submitted(self, run_id: str) -> bool:
        with self._lock:
            item = self.queue.snapshot(run_id)
            return item is not None and item["status"] in {"pending", "running"}

    def queue_snapshot(self, run_id: str | None = None):
        with self._lock:
            result = self.queue.snapshot(run_id)
            blocked = self._readiness_error()
            for item in ([result] if isinstance(result, dict) else result or []):
                if item["status"] == "pending" and blocked:
                    item["blocked_reason"] = blocked
            return result

    def _readiness_error(self) -> str | None:
        if self._blocked_error:
            return self._blocked_error
        if (self._dispatcher is not None and not self._closed
                and not self._dispatcher.is_alive()):
            self._pause("生产队列消费者意外退出；任务已保留，请核实执行记录后重启服务。")
        return self._blocked_error

    def _pause(self, reason: str):
        with self._lock:
            self._blocked_error = reason
        logger.error("Production queue paused: %s", reason)
        try:
            self.queue.pause_pending(reason)
        except Exception:
            # Readiness stays blocked even when the database itself is unavailable.
            logger.exception("Could not persist queue pause; pending authorization is unchanged")

    def cancel(self, run_id: str, *, expected_version: int) -> None:
        with self._lock:
            self.service.cancel(run_id, expected_version=expected_version)
            self.queue.cancel_pending(run_id)

    def shutdown(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._stop.set()
            self._wake.set()
            active, pool = list(self._active.items()), self._pool
        if pool is None:
            return
        try:
            errors = []
            deadline = monotonic() + self.shutdown_timeout
            for _, task in active:
                task.cancel.set()
            def interrupt_active():
                for run_id, task in active:
                    if task.future.done():
                        continue
                    try:
                        self.service.interrupt_owner(run_id, owner_id=task.owner_id)
                    except Exception as error:
                        errors.append(error)
            # Database contention must not delay notifying other workers, nor
            # give five serial status writes five separate shutdown budgets.
            self._shutdown_marker = Thread(target=interrupt_active, daemon=True, name="creatoros-shutdown-state")
            self._shutdown_marker.start()
            futures = [task.future for _, task in active]
            if futures:
                _, pending = wait(futures, timeout=max(0, deadline - monotonic()))
                if pending:
                    # Keep the OS lock and journals while any writer may survive.
                    raise ExecutionOwnershipError("执行器未按时退出，保留独占锁；请核实旧执行者后再恢复。")
                for future in futures:
                    try:
                        future.result()
                    except Exception as error:
                        errors.append(error)
            self._shutdown_marker.join(timeout=max(0, deadline - monotonic()))
            if self._shutdown_marker.is_alive():
                raise ExecutionOwnershipError("关停状态保存未按时结束，保留独占锁；请检查数据库和执行记录。")
            pool.shutdown(wait=True, cancel_futures=True)
            if self._dispatcher is not None:
                self._dispatcher.join(timeout=max(0, deadline - monotonic()))
                if self._dispatcher.is_alive():
                    raise ExecutionOwnershipError("队列消费者未按时退出，保留独占锁。")
            self.service.guard.assert_clean()
            if errors:
                # All writers have exited and journals are clean. Queue/state
                # errors stay visible for restart reconciliation, not as a
                # fictitious surviving process that holds the OS lock forever.
                logger.warning("Production shutdown completed with recorded task errors: %s", errors)
        except BaseException:
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            self.service.guard.__exit__()

    def _consume(self):
        while not self._stop.is_set():
            item = None
            try:
                with self._lock:
                    if self._closed:
                        return
                    for run_id, task in list(self._active.items()):
                        if task.future.done():
                            # A lost result or unverified child must stop new dispatch,
                            # not cancel the independent work already in flight.
                            task.future.result()
                            del self._active[run_id]
                    # Never start another writer if the previous process is unaccounted for.
                    self.service.guard.assert_dispatchable()
                    advance_tasks = getattr(self, "advance_tasks", None)
                    if advance_tasks:
                        advance_tasks()
                    item = (self.queue.next_pending(
                        excluded_topics={task.topic_id for task in self._active.values() if task.task_id is None},
                        excluded_tasks={task.task_id for task in self._active.values() if task.task_id is not None})
                            if len(self._active) < self.max_concurrency else None)
                    if item is not None:
                        self.queue.transition(item.id, "running")
                        revision = self.service.get_active_revision(item.run_id)
                        if revision.id != item.revision_id:
                            raise ContentRunError("排队后内容版本已变化，需要重新确认生产。")
                        owner = f"{self.instance_id}:{uuid4().hex}"
                        prepared = self.service.claim(item.run_id, owner_id=owner,
                                                      expected_version=item.expected_version)
                        cancel = Event()
                        try:
                            assert self._pool is not None
                            future = self._pool.submit(self._run, prepared, owner, cancel, item.id)
                        except BaseException:
                            self.service.interrupt_owner(item.run_id, owner_id=owner,
                                message="提交执行器失败，生产未启动，可显式恢复。")
                            self.service.guard.finish(owner)
                            raise
                        self._active[item.run_id] = ActiveRun(owner, cancel, future, prepared["input"].topic_id, prepared["input"].task_id)
                        future.add_done_callback(lambda _future: self._wake.set())
                if item is None:
                    self._wake.wait(timeout=1)
                    self._wake.clear()
            except ExecutionOwnershipError as error:
                logger.exception("Production queue paused: execution ownership needs verification")
                self._pause(str(error))
                return
            except Exception as error:
                logger.exception("Production queue item stopped; later items remain eligible")
                if item is not None:
                    try:
                        run = self.service.get(item.run_id)
                        self.queue.transition(item.id, "cancelled" if run.status.value == "cancelled"
                                              else "needs_attention", str(error))
                    except Exception:
                        logger.exception("Could not persist failed dispatch; stop before another writer starts")
                        self._pause("无法保存当前生产结果，队列已暂停；任务已保留，请检查数据库和执行记录。")
                        return
                else:
                    self._pause("无法读取生产队列，已暂停派发；任务已保留，请检查数据库后重启服务。")
                    return

    def _run(self, prepared: dict, owner_id: str, cancel: Event, item_id: int) -> None:
        try:
            self.service.execute_claimed(prepared, owner_id=owner_id, cancel_event=cancel)
        except (ContentRunError, ContentRunExecutionError):
            # The service has persisted the business failure or rejected a late owner.
            pass
        except Exception:
            logger.exception("ContentRun worker stopped unexpectedly; inspect execution journal")
            raise
        finally:
            self.queue.finish(item_id, prepared["run_id"])
