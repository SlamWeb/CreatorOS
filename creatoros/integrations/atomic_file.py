"""Atomic text publication with bounded Windows file-sharing retries."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from time import sleep
from uuid import uuid4

LOG = logging.getLogger(__name__)
RETRY_DELAYS = (0.01, 0.02, 0.04)


def write_diagnostic_text(path: Path, text: str) -> bool:
    """Optional copies only; never use for authoritative task/receipt writes."""
    try:
        atomic_write_text(path, text)
        return True
    except OSError as error:
        # Keep raw paths, model text and credentials out of the server warning.
        LOG.warning("Optional Codex diagnostic copy unavailable (%s)", type(error).__name__)
        return False


def atomic_write_text(path: Path, text: str) -> None:
    # Unique, exclusively created sibling files inherit the destination ACL.
    # A fixed .tmp name can be overwritten by another writer.
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(text)
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                os.replace(temporary, path)
                return
            except OSError as error:
                if (getattr(error, "winerror", None) not in {5, 32, 33}
                        or attempt == len(RETRY_DELAYS)):
                    raise
                sleep(RETRY_DELAYS[attempt])
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError as error:
            # Cleanup must not replace the original publication error.
            LOG.warning("Atomic temporary cleanup failed (%s)", type(error).__name__)
