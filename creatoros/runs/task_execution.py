"""Compatibility imports for frozen execution input handling."""
from creatoros.delivery.task_execution import _safe_source, file_sha256, freeze_execution_spec, verify_execution_spec

__all__ = ["file_sha256", "freeze_execution_spec", "verify_execution_spec"]
