"""In-turn image delivery: validate now, return a durable host receipt."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path

from .atomic_file import atomic_write_text
from creatoros.delivery.image_materials import FileDelivery, accept_delivery_value

from creatoros.delivery.image import SUBMISSION, SUBMISSION_INTENT, accepted_submission
MAX_FAILURES = 3
RECEIVED_ONLY = "received:only"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ImageSubmission:
    """One handler per Attempt; the same payload is safe to submit repeatedly."""
    def __init__(self, directory: Path, checkpoint, generated_root: Path, bundle: dict):
        self.directory, self.checkpoint = directory, checkpoint
        self.generated_root, self.bundle = generated_root, bundle
        self.turn_id = None
        self._lock = asyncio.Lock()
        self._inflight_images = set()
        self._outcome_unknown = False

    def observe_image_event(self, event):
        """Track observed generation events; a pending image makes the alias ambiguous."""
        def value(item):
            return getattr(item, "root", item)
        payload = event.payload
        item = value(getattr(payload, "item", None))
        if (getattr(item, "type", "") != "imageGeneration"
                or value(getattr(payload, "thread_id", None)) != self.checkpoint.thread_id
                or value(getattr(payload, "turn_id", None)) != self.turn_id):
            return
        item_id = getattr(item, "id", None)
        if not item_id:
            return
        if event.method == "item/started":
            self._inflight_images.add(item_id)
        elif event.method == "item/completed":
            self._inflight_images.discard(item_id)

    async def __call__(self, arguments):
        # SDK server requests and event consumption use separate queues. Give
        # already emitted image completion events a bounded opportunity to drain.
        async with self._lock:
            if self._outcome_unknown:
                return self._unknown_result()
            if not isinstance(arguments, dict):
                return {"accepted": False, "retryable": True, "error": "提交参数必须为JSON对象。"}
            from creatoros.delivery.image_assets import ImageAssetStore
            artifacts = arguments.get("artifacts")
            paths = [p.get("source_image_path") for p in artifacts if isinstance(p, dict)] if isinstance(artifacts, list) else []
            store = ImageAssetStore(self.directory, self.generated_root, self.checkpoint.thread_id,
                                    self.checkpoint.input_digest)
            try:
                for _ in range(10):
                    known = {p.source_image_path for p in store.read().assets}
                    if (not paths or any(not isinstance(path, str) for path in paths)
                            or (all(path in known or (path == RECEIVED_ONLY and known) for path in paths)
                                and not (RECEIVED_ONLY in paths and self._inflight_images))):
                        break
                    await asyncio.sleep(0.1)
            except ValueError:
                return {"accepted": False, "retryable": False,
                        "error": "已接收图片记录无法核对，图片保留；请停止，不要重新生图。"}
            return self.submit(arguments)

    def submit(self, arguments):
        if self._outcome_unknown:
            return self._unknown_result()
        before = self.checkpoint.model_copy(deep=True)
        try:
            return self._submit(arguments)
        except OSError:
            # Receipt/file writes may partially succeed. Never acknowledge an
            # in-memory acceptance that was not durably checkpointed.
            for field in type(before).model_fields:
                setattr(self.checkpoint, field, getattr(before, field))
            self._outcome_unknown = True
            return self._unknown_result()

    @staticmethod
    def _unknown_result():
        return {"accepted": False, "retryable": False, "outcome_unknown": True,
                "error": "图片提交结果待核对，原回执和图片已保留；请查看原 Attempt，不要重新生图。"}

    def _submit(self, arguments):
        from creatoros.delivery.image import CHECKPOINT, ingest, verified_pages
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
        intent = self.directory / SUBMISSION_INTENT
        receipt_file = self.directory / SUBMISSION
        if (intent.exists() or intent.is_symlink() or receipt_file.exists() or receipt_file.is_symlink()):
            self._outcome_unknown = True
            return self._unknown_result()
        if checkpoint.submission_failures >= MAX_FAILURES:
            return {"accepted": False, "retryable": False, "error": "交付修正已达3次上限，已有图片保留。"}
        from creatoros.delivery.image_assets import ImageAssetStore
        artifacts = arguments.get("artifacts")
        if isinstance(artifacts, list) and any(isinstance(item, dict) and
                item.get("source_image_path") == RECEIVED_ONLY for item in artifacts):
            try:
                assets = ImageAssetStore(self.directory, self.generated_root, checkpoint.thread_id,
                                         checkpoint.input_digest).read().assets
            except ValueError:
                return {"accepted": False, "retryable": False,
                        "error": "已接收图片记录无法核对，图片保留；请停止，不要重新生图。"}
            if self._inflight_images:
                return {"accepted": False, "retryable": True,
                        "error": "仍有图片正在生成或接收，请等待完成后明确选择，不要重新生图。",
                        "received_images": [{"source_image_path": item.source_image_path,
                                             "image_prompt": item.revised_prompt} for item in assets]}
            # A dynamic-tool request and SDK image events travel through
            # separate queues. Even one observed image does not prove there is
            # no later event. Never choose a new submission on that guess.
            return {"accepted": False, "retryable": True,
                    "error": "请从已接收图片中明确选择实际图片路径。",
                    "received_images": [{"source_image_path": item.source_image_path,
                                         "image_prompt": item.revised_prompt} for item in assets],
                    "instruction": "清单只包含目前已接收的图片；若仍有生成或接收未完成，请等待，不要重新生图。"}
        intent_owned = False
        try:
            value = FileDelivery.model_validate(arguments)
            # Exclusive marker is durable before any delivery/index mutations.
            # A crash or partially successful write leaves evidence to stop recovery.
            try:
                with intent.open("x", encoding="utf-8") as stream:
                    intent_owned = True
                    json.dump({"protocol": "image-submission-intent-v1", "thread_id": checkpoint.thread_id,
                               "turn_id": self.turn_id, "input_digest": checkpoint.input_digest,
                               "payload_sha256": payload_hash}, stream, ensure_ascii=False)
                    stream.flush()
                    os.fsync(stream.fileno())
            except FileExistsError:
                self._outcome_unknown = True
                return self._unknown_result()
            accept_delivery_value(self.directory, checkpoint, self.generated_root, self.bundle,
                                  value.model_dump())
            ingest(self.directory, checkpoint, self.generated_root)
        except ValueError as error:
            # A rejected, validated submission has no accepted receipt. Allow
            # existing correction semantics only after removing our own intent.
            if intent_owned:
                intent.unlink()
            elif intent.exists() or intent.is_symlink() or receipt_file.exists() or receipt_file.is_symlink():
                self._outcome_unknown = True
                return self._unknown_result()
            checkpoint.submission_failures += 1
            atomic_json(self.directory / CHECKPOINT, checkpoint)
            from creatoros.delivery.image_assets import ImageAssetStore
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
    "如果拿不到图片路径，可先将source_image_path写为received:only查询已接收清单；"
    "它不会自动选择图片。等所有生成调用完成后，按返回的实际路径明确提交，不要重新生图。"
    "宿主当场检查；accepted=false时只修正交付信息后重试，不重新生图；retryable=false时停止。"
    "accepted=true后交付已保存，再向用户简短汇报。重复提交相同清单复用同一回执。"
)
