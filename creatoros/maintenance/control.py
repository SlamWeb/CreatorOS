"""Authenticated loopback admission control for one explicitly managed Studio."""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
import hmac
import ipaddress
import json
import os
from pathlib import Path
import secrets
import sys
from uuid import uuid4

from filelock import FileLock

from creatoros.config import PROJECT_ROOT
from . import state


class MaintenanceController:
    def __init__(self, app, layout, *, port, token=None, held=False, shutdown_callback=None):
        self.app, self.layout, self.port = app, layout, port
        self.instance_id = uuid4().hex
        self.token = token or secrets.token_urlsafe(32)
        if not isinstance(self.token, str) or len(self.token) < 32:
            raise ValueError("Maintenance token is invalid.")
        self.held, self.inflight_writes = bool(held), 0
        self.shutdown_callback = shutdown_callback
        self.owner = None

    def register(self, *, owner=None):
        """The lock, not a stale receipt/PID, establishes ownership."""
        control_dir = state.safe_path(self.layout.control_dir)
        control_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        instance_file = state.safe_path(self.layout.instance_file)
        owner_path = state.safe_path(control_dir / "owner.lock")
        if owner is None:
            owner = FileLock(str(owner_path), timeout=0, thread_local=False)
            owner.acquire()
        elif not owner.is_locked or Path(owner.lock_file).resolve() != owner_path.resolve():
            raise RuntimeError("Maintenance owner lock does not match this database.")
        temporary, created_identity = None, None
        try:
            receipt = {"instance_id": self.instance_id, "token": self.token,
                       "layout": self.layout.fingerprint(), "project_root": str(PROJECT_ROOT.resolve()),
                       "executable": str(Path(sys.executable).resolve()), "pid": os.getpid(), "port": self.port,
                       "protocol": 1}
            temporary = state.safe_path(control_dir / (".instance-" + uuid4().hex + ".tmp"))
            # Exclusive creation never truncates a pre-existing file or follows
            # an existing link, even if a concurrent process guessed this name.
            with open(temporary, "x", encoding="utf-8",
                      opener=lambda path, flags: os.open(path, flags, 0o600)) as handle:
                original = os.fstat(handle.fileno())
                created_identity = original.st_dev, original.st_ino
                json.dump(receipt, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            state.safe_path(temporary).chmod(0o600)
            state.safe_path(owner_path)
            state.safe_path(instance_file)
            temporary.replace(instance_file)
            self.owner = owner
        except BaseException:
            owner.release()
            raise
        finally:
            if temporary is not None and created_identity is not None:
                try:
                    remaining = temporary.lstat()
                    if (remaining.st_dev, remaining.st_ino) == created_identity:
                        temporary.unlink()
                except FileNotFoundError:
                    pass

    def close(self):
        if self.owner is None:
            return
        try:
            try:
                instance_file = state.safe_path(self.layout.instance_file)
                receipt = json.loads(instance_file.read_text(encoding="utf-8"))
                if receipt.get("instance_id") == self.instance_id:
                    instance_file.unlink()
            except (OSError, ValueError, state.MaintenanceError):
                pass
        finally:
            self.owner.release()
            self.owner = None

    def _live_busy(self):
        service = self.app.state
        busy = []
        if self.inflight_writes:
            busy.append("http_writes")
        chat = service.chat
        if chat.active or any(thread.is_alive() for thread in chat.threads.values()):
            busy.append("chat")
        if service.executor._active:
            busy.append("production")
        extraction = service.skill_extractions
        if extraction.active_id or extraction._draining:
            busy.append("skill_extraction")
        research = service.topic_research
        if research.active_batch_id or getattr(research, "_draining", False):
            busy.append("research")
        installs = service.skill_installs
        if installs._draining or (installs.thread and installs.thread.is_alive()):
            busy.append("skill_install")
        if any(thread.is_alive() for thread in service.content_discussions.workers.values()):
            busy.append("discussion")
        if any(not task.done() for task in service.feedback._jobs.values()):
            busy.append("feedback")
        publishing = service.publishing
        if (any(not task.done() for task in publishing.tasks) or publishing._mcp_lock.locked()
                or publishing._foreground_waiters):
            busy.append("publication")
        return busy

    def inspect(self):
        """No await between these locks, the final idle read and admission close."""
        services = self.app.state
        locks = (services.executor._lock, services.executor.service._write_lock,
                 services.chat.lock, services.skill_installs.lock, services.topic_research.lock,
                 services.skill_extractions.lock, services.skill_iterations.lock,
                 services.content_discussions.lock,
                 *([services.agent_plans._lock] if hasattr(services, "agent_plans") else []))
        with ExitStack() as stack:
            for lock in locks:
                if not lock.acquire(blocking=False):
                    return {"idle": False, "busy": ["worker_state_locked"], "metadata_scanned": 0}
                stack.callback(lock.release)
            live = self._live_busy()
            try:
                result = state.inspect_idle(self.layout)
            except Exception:
                return {"idle": False, "busy": [*live, "state_unavailable"], "metadata_scanned": 0}
            return {**result, "idle": bool(result["idle"] and not live),
                    "busy": [*live, *result.get("busy", [])]}

    def status(self):
        info = self.inspect()
        return {"instance_id": self.instance_id, "layout": self.layout.fingerprint(),
                "held": self.held, "workers_started": self.app.state.background_services_started,
                "pid": os.getpid(), "idle": info["idle"], "info": info}

    def action(self, action):
        if action == "status":
            return 200, self.status()
        if action == "begin":
            info = self.inspect()
            if not info["idle"]:
                return 409, {"error": "maintenance_busy", "info": info}
            self.held = True
            return 200, self.status()
        if action == "release":
            if not self.held:
                return 200, self.status()
            info = self.inspect()
            if not info["idle"]:
                return 409, {"error": "maintenance_busy", "info": info}
            try:
                self.app.state.start_background_services()
            except Exception:
                # Stay closed: the operator may inspect or request shutdown.
                return 503, {"error": "background_start_failed", "held": True}
            self.held = False
            return 200, self.status()
        if not self.held:
            return 409, {"error": "maintenance_not_held"}
        info = self.inspect()
        if not info["idle"]:
            return 409, {"error": "maintenance_busy", "info": info}
        if self.shutdown_callback is None:
            return 503, {"error": "shutdown_unavailable"}
        try:
            self.shutdown_callback()
        except Exception:
            return 503, {"error": "shutdown_failed", "held": True}
        return 200, {"instance_id": self.instance_id, "shutdown_requested": True}


class MaintenanceMiddleware:
    """Pure ASGI so long-lived SSE does not become an in-flight mutation."""
    def __init__(self, app, *, controller):
        self.app, self.controller = app, controller

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        controller = self.controller
        if scope["path"] == "/api/_maintenance":
            return await self.control(scope, receive, send)
        mutating = scope["method"] in {"POST", "PUT", "PATCH", "DELETE"}
        if mutating and controller.held:
            return await self.respond(send, 503, {"error": {"code": "maintenance_held",
                "message": "服务正在安全维护，请稍后再试；已有内容保留。"}})
        if not mutating:
            return await self.app(scope, receive, send)
        controller.inflight_writes += 1
        try:
            return await self.app(scope, receive, send)
        finally:
            controller.inflight_writes -= 1

    async def control(self, scope, receive, send):
        headers = scope.get("headers", [])
        keys = [key.lower() for key, _ in headers]
        try:
            local = ipaddress.ip_address(scope.get("client", ("",))[0]).is_loopback
        except ValueError:
            local = False
        rejected = (not local or b"origin" in keys or scope["method"] != "POST"
                    or any(key.startswith(b"x-creatoros-agent") for key in keys)
                    or b"x-creatoros-origin" in keys
                    or any(keys.count(key) != 1 for key in (b"content-type", b"x-creatoros-maintenance")))
        values = dict(headers)
        supplied = values.get(b"x-creatoros-maintenance", b"")
        if rejected or not hmac.compare_digest(supplied, self.controller.token.encode()):
            return await self.respond(send, 403, {"error": "maintenance_forbidden"})
        if values[b"content-type"].split(b";", 1)[0].strip().lower() != b"application/json":
            return await self.respond(send, 415, {"error": "json_required"})
        try:
            body = b""
            while True:
                message = await asyncio.wait_for(receive(), timeout=3)
                if message["type"] != "http.request":
                    raise ValueError("Disconnected request")
                body += message.get("body", b"")
                if len(body) > 1024:
                    raise ValueError("Request too large")
                if not message.get("more_body", False):
                    break
            payload = json.loads(body)
            if (not isinstance(payload, dict) or set(payload) != {"action", "instance_id"}
                    or payload["action"] not in {"status", "begin", "release", "shutdown"}
                    or not isinstance(payload["instance_id"], str)):
                raise ValueError("Invalid maintenance request")
        except (ValueError, TypeError, asyncio.TimeoutError):
            return await self.respond(send, 422, {"error": "invalid_request"})
        if not hmac.compare_digest(payload["instance_id"].encode(), self.controller.instance_id.encode()):
            return await self.respond(send, 409, {"error": "instance_mismatch"})
        code, result = self.controller.action(payload["action"])
        return await self.respond(send, code, result)

    @staticmethod
    async def respond(send, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json; charset=utf-8"),
                                (b"cache-control", b"no-store"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
