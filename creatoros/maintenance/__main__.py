"""Explicit safe restart, with no OS process-kill fallback."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from filelock import FileLock, Timeout
from sqlalchemy.engine import make_url

from creatoros.config import DATABASE_URL, PROJECT_ROOT
from .state import (Layout, MaintenanceError, backup, inspect_idle, migration_pending,
                    read_json, safe_path, snapshot, verify_snapshot)


class ControlError(MaintenanceError):
    pass


def write_new(path, value):
    with safe_path(path).open("x", encoding="utf-8") as target:
        json.dump(value, target, ensure_ascii=False, indent=2)


def control(receipt, action):
    if (not isinstance(receipt.get("instance_id"), str) or not receipt["instance_id"]
            or type(receipt.get("port")) is not int or not 1 <= receipt["port"] <= 65535
            or not isinstance(receipt.get("token"), str) or len(receipt["token"]) < 32):
        raise ControlError("实例登记损坏，未发送维护请求。")
    body = json.dumps(dict(action=action, instance_id=receipt["instance_id"])).encode()
    request = Request(f"http://127.0.0.1:{receipt['port']}/api/_maintenance", data=body,
        headers={"Content-Type": "application/json", "X-CreatorOS-Maintenance": receipt["token"]}, method="POST")
    # Never follow a redirect with a maintenance credential.
    import urllib.request
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(request, timeout=20) as response:
            result = json.load(response)
    except HTTPError as error:
        raise ControlError(f"受管服务拒绝 {action}（HTTP {error.code}）；未强制停止进程。") from None
    except (OSError, ValueError) as error:
        raise ControlError("无法确认受管实例，未尝试杀进程或重复启动。") from error
    if not isinstance(result, dict) or result.get("instance_id") != receipt["instance_id"]:
        raise ControlError("控制响应的实例身份不符。")
    return result


def port_open(port):
    with socket.socket() as client:
        client.settimeout(0.3)
        return client.connect_ex(("127.0.0.1", port)) == 0


def managed(layout, port):
    receipt = read_json(layout.instance_file)
    if (receipt.get("layout") != layout.fingerprint() or receipt.get("port") != port
            or receipt.get("protocol") != 1
            or type(receipt.get("pid")) is not int or receipt["pid"] <= 0
            or receipt.get("project_root") != str(PROJECT_ROOT.resolve())
            or receipt.get("executable") != str(Path(sys.executable).resolve())
            or not isinstance(receipt.get("token"), str) or len(receipt["token"]) < 32):
        raise MaintenanceError("实例登记与本地路径/解释器不符，拒绝自动接管。")
    status = control(receipt, "status")
    if status.get("layout") != layout.fingerprint():
        raise MaintenanceError("服务实际数据目录不符。")
    return receipt, status


def check(layout, port):
    report = inspect_idle(layout)
    if port_open(port):
        try:
            _, status = managed(layout, port)
            report.update(server="managed", instance_id=status["instance_id"], live=status)
            if not status.get("idle"):
                report["idle"] = False
        except (OSError, ValueError, MaintenanceError):
            report.update(idle=False, server="unmanaged", reason="服务未登记或版本不支持安全维护，不能自动停止。")
    else:
        report["server"] = "offline"
    return report


def process_finished(pid):
    """Read-only process completion check; never terminate an OS process."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only, not terminate rights.
        if not handle:
            if ctypes.get_last_error() == 87:  # No process with this ID.
                return True
            raise MaintenanceError("无法只读核对旧进程是否退出。")
        try:
            return kernel.WaitForSingleObject(handle, 0) == 0
        finally:
            kernel.CloseHandle(handle)
    status = Path(f"/proc/{pid}/stat")
    if not status.exists():
        return True
    return status.read_text().rsplit(")", 1)[1].strip().startswith("Z")


def wait_closed(layout, port, timeout, pid=None):
    deadline = time.monotonic() + timeout
    owner = FileLock(str(safe_path(layout.control_dir / "owner.lock")), timeout=0)
    while time.monotonic() < deadline:
        if not port_open(port):
            try:
                with owner:
                    if pid is None or process_finished(pid):
                        return
            except Timeout:
                pass
        time.sleep(0.15)
    raise MaintenanceError("旧实例尚未确认退出；没有启动替代实例，也没有强杀。")


def launch(layout, port, token, evidence):
    args = [sys.executable, "-m", "creatoros.web", "--port", str(port),
            "--database-url", "sqlite:///" + layout.database.as_posix(),
            "--output-root", str(layout.output), "--maintenance-start"]
    environment = os.environ.copy()
    environment["CREATOROS_MAINTENANCE_TOKEN"] = token
    kwargs = {}
    if os.name == "nt":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        kwargs.update(startupinfo=startup, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    with (evidence / "server.stdout.log").open("xb") as stdout, (evidence / "server.stderr.log").open("xb") as stderr:
        return subprocess.Popen(args, cwd=PROJECT_ROOT, env=environment, stdin=subprocess.DEVNULL,
                                stdout=stdout, stderr=stderr, **kwargs)


def restart(layout, port, timeout=40, *, full=False):
    layout.control_dir.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(safe_path(layout.control_dir / "operation.lock")), timeout=0)
    try:
        with lock:
            return _restart(layout, port, timeout, full=full)
    except Timeout:
        raise MaintenanceError("已有维护命令在执行，请勿并发重启。") from None


def _restart(layout, port, timeout, *, full=False):
    initial = check(layout, port)
    if not initial["idle"]:
        raise MaintenanceError("不能安全重启：" + json.dumps(initial, ensure_ascii=False))
    old = None
    offline_owner = offline_execution = None
    held, stopped = False, False
    evidence = layout.control_dir / "evidence" / (datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8])
    evidence.mkdir(parents=True, exist_ok=False)
    write_new(evidence / "initial.json", initial)
    try:
        if port_open(port):
            old, _ = managed(layout, port)
            if initial.get("live", {}).get("held"):
                raise MaintenanceError("上次维护尚未结束，请先核对其证据；不会覆盖未完成的维护。")
            control(old, "begin")
            held = True
        else:
            # Even an offline API can have a surviving non-Web producer.
            try:
                offline_owner = FileLock(str(safe_path(layout.control_dir / "owner.lock")), timeout=0)
                offline_owner.acquire()
                offline_execution = FileLock(str(safe_path(Path(str(layout.database) + ".execution.lock"))), timeout=0)
                offline_execution.acquire()
            except Timeout:
                raise MaintenanceError("数据库仍被执行者占用，不能启动替代实例。") from None
        status = inspect_idle(layout)
        if not status["idle"]:
            raise MaintenanceError("备份前状态已变化，未停止服务：" + json.dumps(status, ensure_ascii=False))
        full = full or migration_pending(layout)
        verification = "full" if full else "startup"
        before = snapshot(layout) if full else None
        write_new(evidence / "before.json", before if full else {"verification": verification})
        backup(layout, evidence / "before.sqlite")
        if old:
            stopped = True
            control(old, "shutdown")
            wait_closed(layout, port, timeout, old.get("pid"))
        if offline_execution:
            offline_execution.release()
        if offline_owner:
            offline_owner.release()
        token = secrets.token_urlsafe(40)
        child = launch(layout, port, token, evidence)
        deadline = time.monotonic() + timeout
        replacement = None
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise MaintenanceError("替代服务启动失败，查看本次维护日志；未重新尝试。")
            try:
                candidate = read_json(layout.instance_file)
                if candidate.get("token") == token:
                    status = control(candidate, "status")
                    if status.get("held") and not status.get("workers_started"):
                        replacement = candidate
                        break
            except (OSError, ValueError, MaintenanceError):
                pass
            time.sleep(0.2)
        if replacement is None:
            raise MaintenanceError("替代服务身份/启动结果未知；不再启动第二个进程，请检查本次日志。")
        if (replacement.get("layout") != layout.fingerprint() or replacement.get("port") != port):
            raise MaintenanceError("替代服务路径或端口不符，保持维护状态。")
        health = json.load(urlopen(f"http://127.0.0.1:{port}/api/health", timeout=10))
        if health.get("status") != "ok" or health.get("database") != "ok":
            raise MaintenanceError("替代服务健康检查未通过，保持维护状态。")
        preservation = {"verification": verification}
        if full:
            preservation.update(verify_snapshot(layout, before))
        if not inspect_idle(layout)["idle"]:
            raise MaintenanceError("验证阶段出现新活动，保持维护状态。")
        write_new(evidence / "verified.json", dict(**preservation, health=health,
            instance_id=replacement["instance_id"], consumers_deferred=True))
        released = control(replacement, "release")
        if released.get("held") or not released.get("workers_started"):
            raise MaintenanceError("恢复接单未得到确认；不要再次重启，请核对当前实例。")
        report = dict(status="completed", evidence=str(evidence), **preservation,
                      instance_id=replacement["instance_id"], port=port)
        write_new(evidence / "result.json", report)
        return report
    except Exception as error:
        try:
            write_new(evidence / "failure.json", dict(error=str(error), old_stop_requested=stopped,
                policy="不自动恢复旧任务、不重画、不回滚业务数据；替代实例已启动时保持维护状态待核对。"))
        except OSError:
            print("维护错误详情无法写入磁盘；仍执行停机前恢复接单检查。", file=sys.stderr)
        finally:
            if held and not stopped:
                try:
                    control(old, "release")
                except MaintenanceError:
                    print("未能确认恢复接单，请核对当前实例；没有重试或强杀。", file=sys.stderr)
        raise
    finally:
        if offline_execution:
            offline_execution.release()
        if offline_owner:
            offline_owner.release()


def main():
    parser = argparse.ArgumentParser(description="CreatorOS只读检查与显式安全重启")
    parser.add_argument("action", choices=("check", "restart"))
    parser.add_argument("--database-url", default=DATABASE_URL)
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "outputs")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--full", action="store_true", help="重启时完整核对旧行/文件；有待执行迁移时自动启用")
    args = parser.parse_args()
    try:
        url = make_url(args.database_url)
        if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:" or url.query:
            raise MaintenanceError("维护仅支持明确的本地SQLite文件。")
        if not 1 <= args.port <= 65535:
            raise MaintenanceError("无效端口。")
        layout = Layout.from_paths(Path(url.database), args.output_root)
        report = check(layout, args.port) if args.action == "check" else restart(layout, args.port, full=args.full)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if args.action == "restart" or report["idle"] else 2
    except (OSError, ValueError, sqlite3.Error, MaintenanceError) as error:
        print(json.dumps(dict(status="refused", reason=str(error)), ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
