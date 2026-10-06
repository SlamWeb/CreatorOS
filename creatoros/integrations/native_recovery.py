"""Read-only SDK verification for pre-protocol native deliveries; never runs a turn.

--record adds an SDK receipt to a failed Attempt. The normal explicit Run recovery
then imports its validated files into a NEW Attempt without image/model calls.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .native_production import load_checkpoint, safe_file
from .worker_protocol import _write


async def verify_history(directory: Path) -> dict:
    from openai_codex import AsyncCodex, AsyncThread
    checkpoint = load_checkpoint(directory)
    if checkpoint is None:
        raise ValueError("原任务的输入、Skill 或预览证据无效。")
    # Constructing a handle and reading history does not start/resume a turn.
    async with AsyncCodex() as client:
        response = await asyncio.wait_for(AsyncThread(client, checkpoint.thread_id).read(include_turns=True), 30)
    thread = response.thread
    cwd = getattr(thread.cwd, "root", thread.cwd)
    if thread.id != checkpoint.thread_id or Path(cwd).resolve() != (directory / "work").resolve():
        raise ValueError("SDK thread 与本次生产工作目录不一致。")
    if not thread.turns:
        raise ValueError("SDK 未返回可核验的 turn 历史。")
    turn = thread.turns[-1]
    status = getattr(turn.status, "value", turn.status)
    if status != "completed":
        raise ValueError(f"SDK 最后一次 turn 并未成功：{status}")
    texts = []
    for item in turn.items:
        item = getattr(item, "root", item)
        # Persisted history on SDK 0.157 may omit phase. Require the last message
        # to equal our saved completed response rather than inferring its meaning.
        if getattr(item, "type", None) == "agentMessage":
            texts.append(item.text)
    expected = directory / ("production_repair_response.txt" if checkpoint.repair_attempted else "production_response.txt")
    text = safe_file(directory, expected, 2_000_000).read_text(encoding="utf-8")
    if not texts or text.strip() != texts[-1].strip():
        raise ValueError("SDK 最终回复与本 Attempt 保存的回复不一致。")
    return {"protocol": "creatoros-worker-v1", "thread_id": thread.id,
            "verification": "sdk_thread_read_no_inference",
            "turns": [{"id": turn.id, "phase": "delivery_repair" if checkpoint.repair_attempted else "production",
                       "status": "completed"}]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args()
    directory = args.directory.resolve()
    receipt = asyncio.run(verify_history(directory))
    if args.record:
        target = directory / "worker_receipt.json"
        if target.exists():
            raise ValueError("执行回执已存在，拒绝覆盖历史。")
        _write(target, receipt)
    print(json.dumps({**receipt, "recorded": args.record}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
