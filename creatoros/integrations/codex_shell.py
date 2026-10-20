"""Select an unpackaged Windows shell for this SDK child, never globally."""
from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath
import subprocess
from typing import Mapping


def _store_powershell(path: str | Path) -> bool:
    parts = PureWindowsPath(str(path)).parts
    for index, part in enumerate(parts[:-1]):
        if part.casefold() == "windowsapps":
            child = parts[index + 1].casefold()
            if child in {"pwsh.exe", "powershell.exe"} or child.startswith("microsoft.powershell"):
                return True
    return False


def _usable_executable(path: Path) -> bool:
    try:
        return (
            path.is_absolute()
            and path.name.casefold() in {"pwsh.exe", "powershell.exe"}
            and not _store_powershell(path)
            and path.is_file()
            and not _store_powershell(path.resolve())
        )
    except OSError:
        return False


def _launches(path: Path) -> bool:
    try:
        result = subprocess.run(
            [str(path), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "exit 0"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=5, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def codex_subprocess_env(environ: Mapping[str, str] | None = None) -> dict[str, str] | None:
    """Return PATH-only overrides for CodexConfig.env; POSIX needs no overrides.

    Windows Codex discovers pwsh before powershell, irrespective of SHELL.
    A host launch probe catches broken executables but is not a sandbox probe.
    """
    if os.name != "nt":
        return None
    environment = dict(os.environ if environ is None else environ)
    values = {key.casefold(): value for key, value in environment.items()}
    path_keys = [key for key in environment if key.casefold() == "path"]
    path_key = "Path" if "Path" in path_keys else (path_keys[-1] if path_keys else "PATH")
    entries = [entry.strip('"') for entry in environment.get(path_key, "").split(";") if entry]
    candidates: list[Path] = []
    explicit = values.get("creatoros_powershell_executable", "").strip()
    if explicit:
        selected = Path(explicit)
        if not _usable_executable(selected) or not _launches(selected):
            raise RuntimeError(
                "CREATOROS_POWERSHELL_EXECUTABLE 必须指向可启动的非商店版 "
                "pwsh.exe 或 powershell.exe。"
            )
    else:
        if values.get("programfiles"):
            candidates.append(Path(values["programfiles"]) / "PowerShell/7/pwsh.exe")
        if values.get("userprofile"):
            candidates.append(Path(values["userprofile"]) /
                ".cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe")
        candidates.extend(Path(entry) / "pwsh.exe" for entry in entries)
        candidates.append(Path(values.get("systemroot", r"C:\Windows")) /
            "System32/WindowsPowerShell/v1.0/powershell.exe")
        selected = next((path for path in candidates
                         if _usable_executable(path) and _launches(path)), None)
        if selected is None:
            raise RuntimeError("无法启动可用于 Codex 的非商店版 PowerShell；请配置 CREATOROS_POWERSHELL_EXECUTABLE。")

    retained = []
    selected_parent = str(selected.parent)
    for entry in entries:
        if entry.casefold() == selected_parent.casefold():
            continue
        # Do not broadly remove WindowsApps: it can contain valid Codex runtimes.
        if any(_store_powershell(Path(entry) / name) for name in ("pwsh.exe", "powershell.exe")):
            continue
        # Windows discovery prefers pwsh anywhere on PATH to powershell.
        # When falling back to Windows PowerShell, remove those shadowing dirs.
        if selected.name.casefold() == "powershell.exe" and (Path(entry) / "pwsh.exe").exists():
            continue
        retained.append(entry)
    path_value = ";".join([selected_parent, *retained])
    # SDK merges overrides into inherited env; update every casing rather than
    # adding a competing PATH/Path key that Windows may resolve unpredictably.
    return {key: path_value for key in (path_keys or ["PATH"])}
