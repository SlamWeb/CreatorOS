"""In-turn image delivery: validate now, return a durable host receipt."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from .atomic_file import atomic_write_text
from .production_materials import FileDelivery, accept_delivery_value

SUBMISSION = "artifact-submission.json"
MAX_FAILURES = 3


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def accepted_submission(directory, checkpoint):
    from .native_production import safe_file
    raw = safe_file(directory, directory / SUBMISSION, 8_000_000).read_bytes()
    if not checkpoint.submission_sha256 or hashlib.sha256(raw).hexdigest() != checkpoint.submission_sha256:
        raise ValueError("提交回执缺少宿主凭证或已改变。")
    state = json.loads(raw)
    if (state.get("thread_id") != checkpoint.thread_id or state.get("input_digest") != checkpoint.input_digest
            or not state.get("accepted") or not state.get("turn_id")):
        raise ValueError("提交回执不属于本次任务。")
    return state


class ImageSubmission:
    """One handler per Attempt; the same payload is safe to submit repeatedly."""
    def __init__(self, directory: Path, checkpoint, generated_root: Path, bundle: dict):
        self.directory, self.checkpoint = directory, checkpoint
        self.generated_root, self.bundle = generated_root, bundle
        self.turn_id = None
        self._lock = asyncio.Lock()

    async def __call__(self, arguments):
        # SDK server requests and event consumption use separate queues. Give
        # already emitted image completion events a bounded opportunity to drain.
        async with self._lock:
            if not isinstance(arguments, dict):
                return {"accepted": False, "retryable": True, "error": "提交参数必须为JSON对象。"}
            from .native_image_assets import ImageAssetStore
            artifacts = arguments.get("artifacts")
            paths = [p.get("source_image_path") for p in artifacts if isinstance(p, dict)] if isinstance(artifacts, list) else []
            store = ImageAssetStore(self.directory, self.generated_root, self.checkpoint.thread_id,
                                    self.checkpoint.input_digest)
            for _ in range(10):
                known = {p.source_image_path for p in store.read().assets}
                if not paths or all(p in known for p in paths):
                    break
                await asyncio.sleep(0.1)
            return self.submit(arguments)

    def submit(self, arguments):
        before = self.checkpoint.model_copy(deep=True)
        try:
            return self._submit(arguments)
        except OSError:
            # Receipt/file writes may partially succeed. Never acknowledge an
            # in-memory acceptance that was not durably checkpointed.
            for field in type(before).model_fields:
                setattr(self.checkpoint, field, getattr(before, field))
            return {"accepted": False, "retryable": False,
                    "error": "宿主保存交付失败，已有图片保留；请停止，不要重新生图。"}

    def _submit(self, arguments):
        from .native_production import CHECKPOINT, ingest, verified_pages
        from .visual_production import atomic_json
        checkpoint = self.checkpoint
        if not self.turn_id:
            return {"accepted": False, "retryable": False, "error": "提交尚未绑定当前执行回合。"}
        payload_hash = hashlib.sha256(canonical(arguments).encode()).hexdigest()
        if checkpoint.submission_sha256:
            saved = accepted_submission(self.directory, checkpoint)
            verified_pages(self.directory, checkpoint)
            if saved["payload_sha256"] != payload_hash:
                return {"accepted": False, "retryable": False,
                        "error": "本次交付已确认，不能用不同清单覆盖；原交付已保留。"}
            return saved["receipt"]
        if checkpoint.submission_failures >= MAX_FAILURES:
            return {"accepted": False, "retryable": False, "error": "交付修正已达3次上限，已有图片保留。"}
        try:
            value = FileDelivery.model_validate(arguments)
            accept_delivery_value(self.directory, checkpoint, self.generated_root, self.bundle,
                                  value.model_dump())
            ingest(self.directory, checkpoint, self.generated_root)
        except ValueError as error:
            checkpoint.submission_failures += 1
            atomic_json(self.directory / CHECKPOINT, checkpoint)
            from .native_image_assets import ImageAssetStore
            assets = ImageAssetStore(self.directory, self.generated_root, checkpoint.thread_id,
                                     checkpoint.input_digest).read().assets
            return {"accepted": False, "retryable": checkpoint.submission_failures < MAX_FAILURES,
                    "error": str(error), "remaining_corrections": MAX_FAILURES - checkpoint.submission_failures,
                    "received_images": [{"source_image_path": p.source_image_path,
                                         "image_prompt": p.revised_prompt} for p in assets],
                    "instruction": "只修交付清单，使用已接收图片，不要重新生图。"}
        receipt = {"accepted": True, "receipt_id": "delivery_" + payload_hash[:20],
                   "count": len(checkpoint.pages), "message": "产物已接收，等待人工验收；不要重新生成或提交其他清单。"}
        state = {"protocol": "tool-v1", "accepted": True, "thread_id": checkpoint.thread_id,
                 "turn_id": self.turn_id, "input_digest": checkpoint.input_digest,
                 "payload_sha256": payload_hash, "submission": value.model_dump(), "receipt": receipt}
        raw = canonical(state)
        atomic_write_text(self.directory / SUBMISSION, raw)
        checkpoint.submission_sha256 = hashlib.sha256(raw.encode()).hexdigest()
        atomic_json(self.directory / CHECKPOINT, checkpoint)
        return receipt


SUBMIT_DESCRIPTION = (
    "提交本次已生成的最终图片及文案。图片使用生图工具实际返回的路径，按展示顺序从1编号。"
    "宿主当场检查；accepted=false时只修正交付信息后重试，不重新生图；retryable=false时停止。"
    "accepted=true后交付已保存，再向用户简短汇报。重复提交相同清单复用同一回执。"
)
