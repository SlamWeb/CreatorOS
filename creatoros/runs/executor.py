from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from threading import Event, RLock, Thread
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


class ManagedRunExecutor:
    """Durable explicit submissions, capacity-one consumer and bounded shutdown."""

    def __init__(self, service: ContentRunService, *, shutdown_timeout: float = 8.0):
        self.service = service
        self.shutdown_timeout = shutdown_timeout
        self.instance_id = f"studio-{uuid4().hex[:12]}"
        self._pool: ThreadPoolExecutor | None = None
        self._lock = RLock()
        self._active: tuple[str, str, Event, Future] | None = None
        self._closed = False
        self.queue = ProductionQueue(service)
        self._wake = Event()
        self._stop = Event()
        self._dispatcher: Thread | None = None
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
                self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="creatoros-run")
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
            if self._dispatcher is not None:
                self._dispatcher.join(timeout=self.shutdown_timeout)
                if self._dispatcher.is_alive():
                    raise ExecutionOwnershipError("队列消费者未按时退出，保留独占锁。")
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
                    # Never start another writer if the previous process is unaccounted for.
                    self.service.guard.assert_clean()
                    item = self.queue.next_pending()
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
                        self._active = (item.run_id, owner, cancel, future)
                if item is None:
                    self._wake.wait(timeout=1)
                    self._wake.clear()
                else:
                    future.result()
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
        finally:
            self.queue.finish(item_id, prepared["run_id"])
