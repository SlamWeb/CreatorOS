import os
import platform
from dataclasses import dataclass, field
from pathlib import Path


def detect_shell() -> str:
    if os.name == "nt":
        if os.environ.get("PSModulePath"):
            return "PowerShell"
        return Path(os.environ.get("COMSPEC", "cmd.exe")).name
    return Path(os.environ.get("SHELL", "/bin/sh")).name


@dataclass(frozen=True)
class RuntimeContext:
    project_root: Path
    operating_system: str = field(default_factory=platform.system)
    shell: str = field(default_factory=detect_shell)

    @classmethod
    def from_defaults(cls):
        from .config import PROJECT_ROOT

        return cls(project_root=PROJECT_ROOT.resolve())
