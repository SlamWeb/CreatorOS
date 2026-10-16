"""Launch the pinned local probe server; never prints credentials."""
import hashlib
import os
from pathlib import Path
import secrets
import socket
import subprocess

ROOT = Path(__file__).resolve().parents[2] / "tmp" / "xhs-mcp-smoke"
EXE = ROOT / "xiaohongshu-mcp-windows-amd64.exe"
SHA256 = "cb55674f90c1649c875be9c4cde3af193b02fc34ffc860c40c8d711e6ca53e27"


def main():
    if hashlib.sha256(EXE.read_bytes()).hexdigest() != SHA256:
        raise RuntimeError("Unexpected server binary; download and verify v2.5.5 first")
    with socket.socket() as sock:
        if sock.connect_ex(("127.0.0.1", 18060)) == 0:
            raise RuntimeError("Port 18060 is occupied; leave the existing process untouched")
    token_path = ROOT / "auth-token.txt"
    if not token_path.exists():
        with token_path.open("x", encoding="utf-8") as handle:
            handle.write(secrets.token_urlsafe(32))
    env = dict(os.environ, AUTH_TOKEN=token_path.read_text().strip(),
               COOKIES_PATH=str(ROOT / "cookies.json"))
    # Logs remain private and ignored; do not forward third-party raw output into Trace.
    with (ROOT / "server.log").open("ab") as log:
        process = subprocess.Popen([str(EXE), "-headless=true", "-port=127.0.0.1:18060"],
            cwd=ROOT, env=env, stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        print(f"Local probe PID {process.pid}; private log: {ROOT / 'server.log'}", flush=True)
        try:
            return process.wait()
        except KeyboardInterrupt:
            process.terminate()
            return process.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
