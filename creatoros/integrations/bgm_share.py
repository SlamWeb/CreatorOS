"""Resolve public share links without giving the media extractor local network access."""
from __future__ import annotations

from contextlib import contextmanager
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import ipaddress
import json
import os
from pathlib import Path
import re
from select import select
import shutil
import socket
from socketserver import ThreadingMixIn
import subprocess
from tempfile import TemporaryDirectory
from threading import BoundedSemaphore, Lock, Thread
from time import monotonic, sleep
from urllib.parse import urljoin, urlsplit

from .managed_materials import MAX_BYTES, MAX_SHARE_TEXT, SHARE_DOMAINS


def share_url(text):
    if not isinstance(text, str) or len(text) > MAX_SHARE_TEXT:
        raise ValueError("分享原文最多 10000 字。")
    for candidate in re.findall(r"https?://[^\s<>\]\)）]+", text):
        try:
            parts = urlsplit(candidate.rstrip("。，,.!！；;"))
            url = parts._replace(scheme="https", fragment="").geturl()
            hostname = https_url(url).hostname
        except ValueError:
            continue
        if any(hostname == domain or hostname.endswith("." + domain) for domain in SHARE_DOMAINS):
            return url
    raise ValueError("分享内容需要包含抖音、哔哩哔哩、小红书或 YouTube 的公开链接。也可以上传音频或视频。")


def https_url(url):
    if not isinstance(url, str) or re.search(r"[\x00-\x20\x7f\\]", url):
        raise ValueError("分享链接包含无效字符。")
    parts = urlsplit(url)
    if (parts.scheme != "https" or not parts.hostname or parts.username is not None
            or parts.port not in (None, 443)):
        raise ValueError("导入只允许访问公开 HTTPS 地址。")
    return parts


def public_connection(host, timeout=8):
    """Validate every resolved address and dial the validated IP directly."""
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    ips = [ipaddress.ip_address(item[4][0]) for item in addresses]
    if not ips or any(not ip.is_global or ip.is_multicast or ip.is_reserved for ip in ips):
        raise ValueError("分享链接不能访问本机或内网。")
    deadline = monotonic() + timeout
    for family, socktype, protocol, _, address in addresses[:8]:
        connection = socket.socket(family, socktype, protocol)
        try:
            connection.settimeout(max(.05, deadline - monotonic()))
            connection.connect(address)
            return connection
        except OSError:
            connection.close()
    raise OSError("No public address accepted the connection")


class ExtractionProxy(ThreadingMixIn, HTTPServer):
    daemon_threads = True

    def __init__(self):
        self.slots = BoundedSemaphore(8)
        self.guard = Lock()
        self.bytes_remaining = 24 * 1024 * 1024
        super().__init__(("127.0.0.1", 0), ConnectOnly)

    def process_request(self, request, address):
        if self.slots.acquire(blocking=False):
            super().process_request(request, address)
        else:
            self.shutdown_request(request)

    def consume(self, size):
        with self.guard:
            self.bytes_remaining -= size
            return self.bytes_remaining > 0

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


class ConnectOnly(BaseHTTPRequestHandler):
    timeout = 10

    def log_message(self, *_args):
        pass

    def do_CONNECT(self):
        try:
            parts = https_url("https://" + self.path)
            if parts.path or parts.query or parts.fragment:
                raise ValueError("Invalid CONNECT address")
            remote = public_connection(parts.hostname)
        except (OSError, ValueError):
            self.send_error(403)
            return
        with remote:
            self.send_response_only(200, "Connection established")
            self.end_headers()
            deadline = monotonic() + 45
            try:
                while monotonic() < deadline:
                    ready, _, _ = select([self.connection, remote], [], [], 10)
                    if not ready:
                        break
                    for source in ready:
                        buffer = source.recv(65536)
                        if not buffer or not self.server.consume(len(buffer)):
                            return
                        target = remote if source is self.connection else self.connection
                        target.sendall(buffer)
            except OSError:
                return


@contextmanager
def extraction_proxy():
    with ExtractionProxy() as server:
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": .1}, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            thread.join()


def _run_extractor(command, workdir, env):
    data, errors = workdir / "extract.json", workdir / "extract.err"
    deadline = monotonic() + 45
    with data.open("wb") as stdout, errors.open("wb") as stderr:
        with subprocess.Popen(command, cwd=workdir, env=env, stdout=stdout, stderr=stderr) as process:
            try:
                while process.poll() is None:
                    if monotonic() > deadline:
                        raise subprocess.TimeoutExpired(command[0], 45)
                    if data.stat().st_size + errors.stat().st_size > 4 * 1024 * 1024:
                        raise ValueError("分享页解析结果超过大小限制，请上传音频或视频。")
                    sleep(.05)
            finally:
                if process.poll() is None:
                    process.kill()
    if data.stat().st_size + errors.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("分享页解析结果超过大小限制，请上传音频或视频。")
    return process.returncode, data.read_bytes(), errors.read_text(encoding="utf-8", errors="replace")


def extract(source, workdir):
    executable = os.getenv("CREATOROS_MEDIA_DOWNLOADER") or shutil.which("yt-dlp")
    if not executable:
        raise ValueError("未配置 yt-dlp。请上传音频或视频，或配置 CREATOROS_MEDIA_DOWNLOADER。")
    env = {key: value for key, value in os.environ.items() if not key.lower().endswith("_proxy")}
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    try:
        with extraction_proxy() as proxy:
            status, data, stderr = _run_extractor([executable, "--ignore-config", "--no-plugin-dirs", "--no-js-runtimes", "--no-playlist",
                "--no-cache-dir", "--proxy", proxy, "--socket-timeout", "12", "--retries", "0",
                "--extractor-retries", "0", "--skip-download", "--dump-json", "--no-progress",
                "--format", "bestaudio[protocol=https]/best[protocol=https]", "--", source],
                workdir, env)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("分享页解析超时或解析器无法启动，请重试或上传音频或视频。") from error
    if status:
        if re.search(r"no such option|unknown option", stderr, re.I):
            raise ValueError("本机 yt-dlp 版本不支持分享导入所需的选项，请更新后重试或上传文件。")
        if re.search(r"(?:\b(?:401|403|412|429)\b|login|sign in|cookies|verification)", stderr, re.I):
            raise ValueError("来源平台限制了这次公开解析，可能需要登录、验证或稍后重试。也可以上传音频或视频。")
        raise ValueError("分享页没有提供可导入的公开音频或视频，请检查原链接或上传文件。")
    try:
        value = json.loads(data)
        if value.get("protocol") != "https" or value.get("has_drm"):
            raise ValueError
        https_url(value["url"])
        ext = value["ext"]
        if not isinstance(ext, str) or not re.fullmatch(r"[a-z0-9]{2,6}", ext):
            raise ValueError
        headers = value.get("http_headers", {})
        headers = {key: item for key, item in headers.items()
                   if key.lower() in {"user-agent", "referer", "origin", "accept", "accept-language"}
                   and isinstance(item, str) and len(item) <= 2000 and "\n" not in item and "\r" not in item}
        return str(value.get("title") or "分享音乐")[:170] + "." + ext, value["url"], headers
    except (KeyError, ValueError, TypeError, AttributeError) as error:
        raise ValueError("分享页没有提供可导入的公开音频或视频，请上传文件。") from error


def _download(url, headers):
    deadline = monotonic() + 70
    for _ in range(6):
        parts = https_url(url)
        if monotonic() >= deadline:
            break
        connection = http.client.HTTPSConnection(parts.hostname, 443, timeout=8)
        # Preserve Host/SNI/certificate verification with the original name, while
        # the underlying TCP connection uses only the validated public address.
        connection._create_connection = lambda *_args: public_connection(parts.hostname)
        try:
            target = parts.path or "/"
            if parts.query:
                target += "?" + parts.query
            connection.request("GET", target, headers=headers)
            with connection.getresponse() as response:
                if response.status in (301, 302, 303, 307, 308) and response.getheader("Location"):
                    url = urljoin(url, response.getheader("Location"))
                    continue
                if response.status != 200:
                    raise ValueError(f"来源平台未返回完整音频或视频（HTTP {response.status}）。请上传文件。")
                length = response.getheader("Content-Length")
                if length and length.isdecimal() and int(length) > MAX_BYTES:
                    raise ValueError("素材不能超过 64 MiB。")
                data = bytearray()
                while monotonic() < deadline:
                    chunk = response.read1(64 * 1024)
                    if not chunk:
                        if length and length.isdecimal() and len(data) != int(length):
                            raise ValueError("来源中断了音频下载，请重试或上传文件。")
                        return bytes(data)
                    data.extend(chunk)
                    if len(data) > MAX_BYTES:
                        raise ValueError("素材不能超过 64 MiB。")
                break
        finally:
            connection.close()
    raise ValueError("分享下载超时或重定向过多，请重试或上传文件。")


def download_share(source):
    with TemporaryDirectory(prefix="creatoros-bgm-share-") as directory:
        name, url, headers = extract(source, Path(directory))
        try:
            return name, _download(url, headers)
        except (OSError, http.client.HTTPException) as error:
            raise ValueError("来源音频下载失败，请重试或上传文件。") from error
