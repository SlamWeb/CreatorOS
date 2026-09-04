"""Contain only the subprocess tree created by this invocation.

Windows: create suspended, attach to a kill-on-close Job, then resume.
POSIX: create a new process group. Never terminate by executable name.
"""
from __future__ import annotations

import os
import signal
import subprocess
from threading import RLock
from time import monotonic, sleep

import psutil


class ProcessTree:
    def __init__(self):
        self.process = None
        self.job = _WindowsJob() if os.name == "nt" else None
        self._lock = RLock()
        self._closed = False

    def start(self, command, *, on_started=None, **kwargs):
        options = {"creationflags": 0x00000004 | subprocess.CREATE_NO_WINDOW} if self.job else {"start_new_session": True}
        self.process = subprocess.Popen(command, **kwargs, **options)
        process = psutil.Process(self.process.pid)
        if self.job:
            self.job.assign(self.process.pid)
        if on_started:
            on_started({"pid": self.process.pid, "created_at": process.create_time(),
                        "containment": "windows_job" if self.job else "process_group"})
        if self.job:
            process.resume()
        return self.process

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            if self.job:
                try:
                    self.job.stop()
                finally:
                    # Also handles a suspended child whose assignment failed.
                    if self.process is not None and self.process.poll() is None:
                        self.process.kill()
            elif self.process is not None:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if self.process is not None:
                self.process.wait(timeout=5)
            self._closed = True


class _WindowsJob:
    def __init__(self):
        import ctypes as c
        from ctypes import wintypes as w

        class BasicLimits(c.Structure):
            _fields_ = [("process_time", c.c_longlong), ("job_time", c.c_longlong),
                        ("flags", w.DWORD), ("min_ws", c.c_size_t), ("max_ws", c.c_size_t),
                        ("active_limit", w.DWORD), ("affinity", c.c_size_t),
                        ("priority", w.DWORD), ("scheduling", w.DWORD)]

        class ExtendedLimits(c.Structure):
            _fields_ = [("basic", BasicLimits), ("io", c.c_ulonglong * 6),
                        ("process_memory", c.c_size_t), ("job_memory", c.c_size_t),
                        ("peak_process", c.c_size_t), ("peak_job", c.c_size_t)]

        self.c = c
        self.api = c.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([c.c_void_p, w.LPCWSTR], w.HANDLE),
            "SetInformationJobObject": ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL),
            "QueryInformationJobObject": ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p], w.BOOL),
            "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
            "OpenProcess": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
            "CloseHandle": ([w.HANDLE], w.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            method = getattr(self.api, name)
            method.argtypes, method.restype = arguments, result
        self.handle = self.api.CreateJobObjectW(None, None)
        self._check(self.handle)
        limits = ExtendedLimits()
        limits.basic.flags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        try:
            self._check(self.api.SetInformationJobObject(self.handle, 9, c.byref(limits), c.sizeof(limits)))
        except BaseException:
            self.api.CloseHandle(self.handle)
            raise

    def _check(self, success):
        if not success:
            raise self.c.WinError(self.c.get_last_error())

    def assign(self, pid: int):
        handle = self.api.OpenProcess(0x0100 | 0x0001, False, pid)
        self._check(handle)
        try:
            self._check(self.api.AssignProcessToJobObject(self.handle, handle))
        finally:
            self.api.CloseHandle(handle)

    def stop(self):
        if not self.handle:
            return
        self._check(self.api.TerminateJobObject(self.handle, 1))
        deadline = monotonic() + 5
        # JOBOBJECT_BASIC_ACCOUNTING_INFORMATION: four int64 followed by four DWORD.
        accounting = self.c.create_string_buffer(48)
        while True:
            self._check(self.api.QueryInformationJobObject(self.handle, 1, accounting, 48, None))
            active = int.from_bytes(accounting.raw[40:44], "little")
            if not active:
                break
            if monotonic() >= deadline:
                raise RuntimeError("本次生产子进程树未在关闭期限内退出。")
            sleep(0.02)
        self.api.CloseHandle(self.handle)
        self.handle = None
