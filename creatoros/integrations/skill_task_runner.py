"""Execute Python Skill scripts in an isolated WebAssembly runtime."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path, PureWindowsPath
import shutil
import stat
import subprocess
import sys
import tempfile
import threading


class SkillTaskRunnerError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SkillScriptResult:
    returncode: int
    stdout: str
    stderr: str


_READ_ONLY = ("skills", "inputs")
_WRITABLE = ("work", "out")
_WASMTIME_VERSION = "50.0.1"
_STARTUP_ALLOWANCE_SECONDS = 30
_GUEST_MEMORY_BYTES = 512 * 1024**2
_UNAVAILABLE = "The isolated Python runtime is unavailable. Configure the WASI script runtime and retry."
_GUEST_BOOTSTRAP = """
import os
import runpy
import sys

os.chdir("/")
sys.argv = sys.argv[1:]
sys.path.insert(0, os.path.dirname(sys.argv[0]))
runpy.run_path(sys.argv[0], run_name="__main__")
"""


class SkillTaskRunner:
    def __init__(self, *, timeout: float = 30, output_limit: int = 32768):
        if not 0 < timeout <= 120 or not isinstance(output_limit, int) or not 0 < output_limit <= 1_048_576:
            raise ValueError("Script execution limits are out of range.")
        self.timeout = timeout
        self.output_limit = output_limit

    def run(self, workspace: Path, script: str, args: Sequence[str] = ()) -> SkillScriptResult:
        relative = _script_path(script)
        if (
            isinstance(args, (str, bytes))
            or not isinstance(args, Sequence)
            or len(args) > 32
            or any(not isinstance(arg, str) or "\0" in arg for arg in args)
            or sum(len(arg) for arg in args) > 8192
        ):
            raise SkillTaskRunnerError("script_invalid", "Script arguments must be a bounded list of strings.")
        workspace = Path(os.path.abspath(workspace))
        try:
            _validate_workspace(workspace)
        except OSError as exc:
            raise SkillTaskRunnerError("script_invalid", "The script workspace cannot be read.") from exc
        if not workspace.joinpath(*relative.parts).is_file():
            raise SkillTaskRunnerError("script_invalid", "The selected Python script does not exist.")
        python = _configured_path(
            os.environ.get("CREATOROS_SKILL_TASK_PYTHON", sys.executable), workspace, is_directory=False
        )
        support = os.environ.get("CREATOROS_SKILL_TASK_WASI_PATH")
        if support:
            support = _configured_path(support, workspace, is_directory=True)
        wasm_root = _configured_path(
            os.environ.get("CREATOROS_SKILL_TASK_WASM_ROOT", ""), workspace, is_directory=True
        )
        request = {
            "workspace": str(workspace),
            "script": "/" + relative.as_posix(),
            "args": list(args),
            "timeout": self.timeout,
            "output_limit": self.output_limit,
            "support": support,
            "wasm_root": wasm_root,
        }
        return self._run_worker(python, request)

    def _run_worker(self, python: str, request: dict) -> SkillScriptResult:
        broker = Path(__file__).resolve()
        # Python's loader and temporary directory need basic platform locations.
        # Credentials and alternate Python/WASI configuration stay outside the worker.
        environment = {
            key: value for key, value in os.environ.items()
            if key.upper() in {
                "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "LOCALAPPDATA", "USERPROFILE", "HOME",
                "PROCESSOR_ARCHITECTURE", "PROCESSOR_ARCHITEW6432",
            }
        }
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        interpreter_flags = ["-I"]
        if request["support"]:
            interpreter_flags.append("-S")
        try:
            completed = subprocess.run(
                [python, *interpreter_flags, str(broker), "--wasi-worker"],
                input=json.dumps(request).encode("ascii"), capture_output=True, cwd=broker.parent,
                env=environment, timeout=self.timeout + _STARTUP_ALLOWANCE_SECONDS, creationflags=flags,
                check=True,
            )
            payload = json.loads(completed.stdout)
        except subprocess.TimeoutExpired as exc:
            raise SkillTaskRunnerError("script_timeout", "The isolated script worker exceeded its time limit.") from exc
        except (OSError, subprocess.CalledProcessError, ValueError) as exc:
            raise SkillTaskRunnerError("script_runner_unavailable", _UNAVAILABLE) from exc
        if payload.get("code") == "script_timeout":
            raise SkillTaskRunnerError("script_timeout", "The script exceeded its time limit.")
        if payload.get("code") == "script_output_limit":
            raise SkillTaskRunnerError("script_output_limit", "The script exceeded its output limit.")
        return SkillScriptResult(**payload)


def _script_path(script: str) -> PureWindowsPath:
    message = "Choose a relative Python script from skills/ or work/."
    if not isinstance(script, str) or "\0" in script:
        raise SkillTaskRunnerError("script_invalid", message)
    path = PureWindowsPath(script)
    if (
        path.anchor
        or len(path.parts) < 2
        or path.parts[0] not in ("skills", "work")
        or path.suffix.lower() != ".py"
        or any(part == ".." or ":" in part or part.endswith((".", " ")) for part in path.parts)
    ):
        raise SkillTaskRunnerError("script_invalid", message)
    return path


def _configured_path(value: str, workspace: Path, *, is_directory: bool) -> str:
    path = Path(value)
    try:
        if not path.is_absolute():
            raise ValueError("Runtime path must be absolute.")
        resolved = path.resolve(strict=True)
        if resolved.is_relative_to(workspace) or not (resolved.is_dir() if is_directory else resolved.is_file()):
            raise ValueError("Runtime path must be a trusted host location.")
    except (OSError, RuntimeError, ValueError) as exc:
        raise SkillTaskRunnerError("script_runner_unavailable", _UNAVAILABLE) from exc
    return str(resolved)


def _validate_workspace(workspace: Path) -> None:
    for parent in reversed((workspace, *workspace.parents)):
        _validate_node(parent)
    if {child.name for child in workspace.iterdir()} != set(_READ_ONLY + _WRITABLE):
        raise SkillTaskRunnerError("script_invalid", "The script workspace has an unexpected layout.")
    pending = [workspace / name for name in _READ_ONLY + _WRITABLE]
    if any(not root.is_dir() for root in pending):
        raise SkillTaskRunnerError("script_invalid", "The script workspace is missing a directory.")
    while pending:
        path = pending.pop()
        if stat.S_ISDIR(_validate_node(path)):
            pending.extend(path.iterdir())


def _validate_node(path: Path) -> int:
    info = path.lstat()
    if (
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
        or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)
    ):
        raise SkillTaskRunnerError("script_invalid", "Script workspace links and special files are forbidden.")
    return info.st_mode


def _wasi_worker(request: dict) -> dict:
    if request["support"]:
        sys.path.insert(0, request["support"])
    import wasmtime

    if importlib.metadata.version("wasmtime") != _WASMTIME_VERSION:
        raise RuntimeError(f"The script worker requires Wasmtime {_WASMTIME_VERSION}.")
    root = Path(request["workspace"])
    _validate_workspace(root)
    wasm_root = Path(request["wasm_root"])
    config = wasmtime.Config()
    config.epoch_interruption = True
    engine = wasmtime.Engine(config)
    module = wasmtime.Module.from_file(engine, wasm_root / "python.wasm")
    store = wasmtime.Store(engine)
    store.set_limits(memory_size=_GUEST_MEMORY_BYTES, instances=1, memories=1)
    store.set_epoch_deadline(1)
    linker = wasmtime.Linker(engine)
    linker.define_wasi()
    wasi = wasmtime.WasiConfig()
    wasi.argv = ["python", "-B", "-u", "-s", "-S", "-X", "utf8", "-c", _GUEST_BOOTSTRAP,
                 request["script"], *request["args"]]
    wasi.env = [("PYTHONHOME", "/"), ("PYTHONPATH", "/packages"), ("TMPDIR", "/work")]
    streams = (bytearray(), bytearray())
    overflow = False

    def collect(data: bytes, stream: bytearray) -> int:
        nonlocal overflow
        remaining = request["output_limit"] - sum(map(len, streams))
        stream.extend(data[:remaining])
        if len(data) > remaining and not overflow:
            overflow = True
            engine.increment_epoch()
        return len(data)

    wasi.stdout_custom = lambda data: collect(data, streams[0])
    wasi.stderr_custom = lambda data: collect(data, streams[1])
    expired = threading.Event()

    def expire() -> None:
        expired.set()
        engine.increment_epoch()

    timer = threading.Timer(request["timeout"], expire)
    with tempfile.TemporaryDirectory(prefix="creatoros-skill-guest-") as temporary:
        guest_root = Path(temporary)
        for name in (*_READ_ONLY, *_WRITABLE, "lib", "packages"):
            (guest_root / name).mkdir()
        png = importlib.util.find_spec("png")
        if png is not None and png.origin:
            shutil.copyfile(png.origin, guest_root / "packages" / "png.py")
        wasi.preopen_dir(str(guest_root), "/", fs_mutable=False)
        wasi.preopen_dir(str(wasm_root / "lib"), "/lib", fs_mutable=False)
        for name in _READ_ONLY + _WRITABLE:
            wasi.preopen_dir(str(root / name), "/" + name, fs_mutable=name in _WRITABLE)
        store.set_wasi(wasi)
        timer.start()
        returncode = 0
        try:
            instance = linker.instantiate(store, module)
            instance.exports(store)["_start"](store)
        except wasmtime.ExitTrap as exc:
            returncode = exc.code
        except wasmtime.Trap:
            returncode = 1
        finally:
            timer.cancel()
            timer.join()
            store.close()
    if overflow:
        return {"code": "script_output_limit"}
    if expired.is_set():
        return {"code": "script_timeout"}
    stdout, stderr = (bytes(data).decode("utf-8", errors="replace") for data in streams)
    if returncode and not stderr:
        stderr = "The script stopped inside the isolated runtime."
    return asdict(SkillScriptResult(returncode, stdout, stderr))


if __name__ == "__main__":
    if sys.argv[1:] != ["--wasi-worker"]:
        sys.exit("Run the isolated worker through SkillTaskRunner.")
    print(json.dumps(_wasi_worker(json.load(sys.stdin))))
