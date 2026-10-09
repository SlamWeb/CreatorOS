"""Business acceptance requires a completed SDK turn, not just a final message."""
from __future__ import annotations


def require_completed_turn(result) -> None:
    # SDK 0.157.1 returns interrupted turns normally, including prior final items.
    # Keep the stream/receipt evidence intact; do not infer success from files.
    from .codex import CodexProducerError

    status = getattr(result, "status", None)
    status = getattr(status, "value", status)
    if status == "completed":
        return
    if status == "interrupted":
        raise CodexProducerError(
            "Codex 本轮执行已中断；已写文件保留，未验收为完成，不自动重试。",
            error_type="codex_interrupted",
        )
    raise CodexProducerError(
        "Codex 本轮执行没有完成终态；不能验收最终回复或文件。",
        error_type="codex_turn_failed" if status == "failed" else "codex_protocol_error",
    )
