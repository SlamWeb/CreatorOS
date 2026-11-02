"""Start one disposable Studio API and Vite pair without using production data."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading


REPO = Path(__file__).resolve().parent.parent


def _port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535 or port == 8765:
        raise argparse.ArgumentTypeError("choose a port from 1-65535 other than production port 8765")
    return port


def _check_available(port: int) -> None:
    with socket.socket() as listener:
        # Do not connect to, reuse, or stop an existing listener.
        listener.bind(("127.0.0.1", port))


def _serve_api(root: Path, port: int) -> None:
    # The parent sets DATABASE_URL before importing creatoros.web.app, whose
    # module-level app otherwise uses the checkout's default database.
    from creatoros.storage import Database, upgrade_database
    from creatoros.runs import ContentRunService
    from creatoros.web.app import create_app
    import uvicorn

    database_url = os.environ["DATABASE_URL"]
    upgrade_database(database_url)
    database = Database(database_url)
    try:
        runs = ContentRunService(database, output_root=root / "outputs")
        app = create_app(database=database, run_service=runs, eval_root=root / "eval-runs",
                         studio_dist=root / "unused-dist")
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    finally:
        database.close()


def _credential_sources(env: dict[str, str]) -> str:
    from dotenv import dotenv_values

    dotenv = dotenv_values(REPO / ".env") if (REPO / ".env").is_file() else {}
    sources = []
    for name in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY"):
        # Report presence and precedence only, never the value.
        source = ("environment" if env[name] else "empty environment") if name in env else (
            ".env" if dotenv.get(name) else "not detected")
        sources.append(f"{name}: {source}")
    return ", ".join(sources)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="new, non-existing directory for this instance")
    parser.add_argument("--api-port", type=_port, required=True)
    parser.add_argument("--vite-port", type=_port, required=True)
    parser.add_argument("--fixtures", action="store_true", help="use the deterministic browser-test producer")
    parser.add_argument("--stop-on-stdin", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--serve-api", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.serve_api:
        _serve_api(root, args.api_port)
        return 0
    if args.api_port == args.vite_port:
        parser.error("API and Vite must use different ports")
    node = shutil.which("node")
    vite = REPO / "web" / "node_modules" / "vite" / "bin" / "vite.js"
    if not node or not vite.is_file():
        parser.error("Node and the existing web/node_modules installation are required")
    try:
        _check_available(args.api_port)
        _check_available(args.vite_port)
        # A fresh root prevents accidental migration/recovery of an existing queue.
        root.mkdir(parents=True, exist_ok=False)
    except (OSError, ValueError) as error:
        parser.error(f"ports must be unused and root must be new: {error}")

    temp = root / "temp"
    temp.mkdir()
    env = os.environ.copy()
    env.update(DATABASE_URL=f"sqlite:///{(root / 'studio.db').as_posix()}",
               CREATOROS_E2E_ROOT=str(root), CREATOROS_E2E_DIST=str(root / "unused-dist"),
               CREATOROS_STUDIO_URL=f"http://127.0.0.1:{args.api_port}",
               CREATOROS_API_URL=f"http://127.0.0.1:{args.api_port}",
               CREATOROS_XHS_STAGING_ROOT=str(root / "publication-staging"),
               CREATOROS_VITE_PORT=str(args.vite_port), CREATOROS_VITE_CACHE_DIR=str(root / "vite-cache"),
               TEMP=str(temp), TMP=str(temp), TMPDIR=str(temp))
    if args.fixtures:
        # Prevent ordinary fixture runs from inheriting live model or publish
        # credentials; external networking is still not an OS sandbox.
        env.update(DEEPSEEK_API_KEY="", OPENAI_API_KEY="", CODEX_API_KEY="",
                   ZHIHU_ACCESS_SECRET="", PERSONCLONE_SESSION_COOKIE="",
                   PERSONCLONE_BASE_URL="http://127.0.0.1:1", PYTHON_DOTENV_DISABLED="1",
                   CODEX_HOME=str(root / "codex-home"),
                   CREATOROS_CODEX_EXECUTABLE=str(root / "no-codex.exe"),
                   CREATOROS_POWERSHELL_EXECUTABLE=str(root / "no-powershell.exe"),
                   CREATOROS_MEDIA_DOWNLOADER=str(root / "no-downloader.exe"),
                   CREATOROS_XHS_MCP_TOKEN="", CREATOROS_XHS_MCP_URL="http://127.0.0.1:1/mcp",
                   CREATOROS_XHS_MCP_TOKEN_FILE=str(root / "no-publication-token"))

    codex_home = Path(env.get("CODEX_HOME") or Path.home() / ".codex").resolve()
    print(f"Instance: {root}\nAPI: {env['CREATOROS_STUDIO_URL']}\nVite: http://127.0.0.1:{args.vite_port}", flush=True)
    print(f"SQLite, output, sessions, temp and Vite cache: {root}", flush=True)
    print(f"Codex user directory: {codex_home} ({'isolated for fixture' if args.fixtures else 'inherited/shared'})", flush=True)
    print(f"Model credential sources (values hidden): {_credential_sources(env)}", flush=True)
    print("Codex authentication may also use a user credential store; external services remain shared. "
          "Fixture mode is not a network sandbox.", flush=True)

    command = ([sys.executable, "-m", "tests.studio_e2e_server", "--port", str(args.api_port)] if args.fixtures else
               [sys.executable, "-m", "scripts.isolated_dev", "--serve-api", "--root", str(root),
                "--api-port", str(args.api_port), "--vite-port", str(args.vite_port)])
    children = []
    stop = threading.Event()
    if args.stop_on_stdin:
        threading.Thread(target=lambda: (sys.stdin.readline(), stop.set()), daemon=True).start()
    try:
        children.append(subprocess.Popen(command, cwd=REPO, env=env, stdin=subprocess.DEVNULL))
        children.append(subprocess.Popen([node, str(vite), "--configLoader", "runner", "--host", "127.0.0.1", "--port", str(args.vite_port)],
                                         cwd=REPO / "web", env=env, stdin=subprocess.DEVNULL))
        while not stop.wait(.25):
            if any(child.poll() is not None for child in children):
                print("A child exited; stopping only this instance.", file=sys.stderr, flush=True)
                return 1
    except KeyboardInterrupt:
        pass
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
