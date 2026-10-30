"""Host-owned images received from SDK events, never from model-written paths."""
from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path
from typing import Literal

from PIL import Image
from pydantic import Field

from .atomic_file import atomic_write_bytes, atomic_write_text
from .codex import ProductionModel
from .visual_production import atomic_json

INVENTORY = "native_image_assets.json"
VIEW = "generated-assets.json"
MAX_IMAGE_BYTES = 32 * 1024 * 1024


def _value(item):
    return getattr(item, "root", item)


def _within(root: Path, path: Path) -> Path:
    root, path = root.absolute(), path.absolute()
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("接收图片路径越界。")
    for part in (path, *path.parents):
        if part.is_symlink() or (part.exists() and getattr(part.lstat(), "st_file_attributes", 0) & 0x400):
            raise ValueError("接收图片或库存路径不能是链接。")
        if part == root:
            break
    return path


def _file(root: Path, path: Path, limit=MAX_IMAGE_BYTES) -> Path:
    path = _within(root, path)
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError("接收图片或库存文件缺失、过大。")
    return path


class ReceivedImage(ProductionModel):
    asset_id: str = Field(pattern=r"^img_[a-f0-9]{24}$")
    item_id: str = Field(min_length=1, max_length=512)
    turn_id: str = Field(min_length=1, max_length=512)
    source_image_path: str
    file_name: str = Field(pattern=r"^img_[a-f0-9]{24}\.(png|jpg|jpeg|webp)$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    revised_prompt: str | None = None


class ImageInventory(ProductionModel):
    protocol: Literal["native-assets-v1"] = "native-assets-v1"
    thread_id: str
    input_digest: str
    assets: list[ReceivedImage] = Field(default_factory=list)


class ImageAssetStore:
    def __init__(self, directory: Path, generated_root: Path, thread_id: str, input_digest: str):
        self.directory, self.generated_root = Path(directory), Path(generated_root)
        self.thread_id, self.input_digest = thread_id, input_digest
        self.expected_turn_id: str | None = None
        if not thread_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in thread_id):
            raise ValueError("接收图片thread标识无效。")

    @property
    def path(self):
        return self.directory / INVENTORY

    def initialize(self):
        if self.path.exists() or self.path.is_symlink():
            state = self.read()
        else:
            state = ImageInventory(thread_id=self.thread_id, input_digest=self.input_digest)
            atomic_json(_within(self.directory, self.path), state)
        self._view(state)

    def read(self) -> ImageInventory:
        state = ImageInventory.model_validate_json(_file(self.directory, self.path, 8_000_000).read_text(encoding="utf-8"))
        if state.thread_id != self.thread_id or state.input_digest != self.input_digest:
            raise ValueError("图片库存不属于本次thread或冻结输入。")
        if len({a.asset_id for a in state.assets}) != len(state.assets):
            raise ValueError("图片库存编号重复。")
        return state

    def _view(self, state):
        # This model-readable projection is not an authority or an input path.
        path = _within(self.directory / "work", self.directory / "work" / VIEW)
        value = {"images": [{"asset_id": a.asset_id, "source_file": Path(a.source_image_path).name,
            "preview_file": f"../received-images/{a.file_name}", "width": a.width, "height": a.height,
            "revised_prompt": a.revised_prompt} for a in state.assets]}
        atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2))

    def _checked(self, asset):
        path = _file(self.directory, self.directory / "received-images" / asset.file_name)
        if hashlib.sha256(path.read_bytes()).hexdigest() != asset.sha256:
            raise ValueError("已接收图片发生变化，不能继续交付。")
        with Image.open(path) as image:
            if image.size != (asset.width, asset.height):
                raise ValueError("已接收图片尺寸发生变化。")
            image.verify()
        return asset, path

    def resolve(self, asset_id: str):
        asset = next((a for a in self.read().assets if a.asset_id == asset_id), None)
        if asset is None:
            raise ValueError(f"未知图片编号 {asset_id}；请读取 {VIEW} 选择已接收图片，不要重新生图。")
        return self._checked(asset)

    def resolve_path(self, raw: str):
        """Resolve a model file reference through this task's SDK receipts."""
        path = Path(raw)
        if not path.is_absolute():
            path = self.directory / "work" / path
        state = self.read()
        for asset in state.assets:
            if path.absolute() == Path(asset.source_image_path).absolute():
                return self._checked(asset)
        copy = _file(self.directory / "work", path)
        sha = hashlib.sha256(copy.read_bytes()).hexdigest()
        for asset in state.assets:
            if asset.sha256 == sha:
                return self._checked(asset)
        raise ValueError("该图片路径没有本次SDK完成事件的接收记录；请使用实际生图工具返回的路径或宿主提供的已接收图片路径，不要重新生图。")

    def observe(self, event):
        if event.method != "item/completed":
            return
        payload = event.payload
        item = _value(getattr(payload, "item", None))
        if getattr(item, "type", "") != "imageGeneration":
            return
        status = _value(getattr(item, "status", ""))
        if getattr(status, "value", status) != "completed" or getattr(item, "failure", None) is not None:
            return
        if _value(getattr(payload, "thread_id", None)) != self.thread_id:
            raise ValueError("拒绝接收其他thread的生图完成事件。")
        turn_id, item_id = _value(getattr(payload, "turn_id", None)), getattr(item, "id", None)
        if not turn_id or not item_id:
            raise ValueError("生图完成事件缺少turn或图片标识。")
        if self.expected_turn_id is not None and turn_id != self.expected_turn_id:
            raise ValueError("拒绝接收其他turn的生图完成事件。")
        raw_path = _value(getattr(item, "saved_path", None))
        if not raw_path:
            raise ValueError("SDK生图完成事件没有真实保存路径；图片保留，请核对执行记录。")
        source = _file(self.generated_root / self.thread_id, Path(raw_path))
        if source.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise ValueError("SDK图片格式不支持。")
        raw = source.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        with Image.open(BytesIO(raw)) as image:
            width, height = image.size
            image.verify()
        asset_id = "img_" + hashlib.sha256(f"{self.thread_id}\0{turn_id}\0{item_id}".encode()).hexdigest()[:24]
        state = self.read()
        old = next((a for a in state.assets if a.asset_id == asset_id), None)
        if old:
            if old.sha256 != sha or Path(old.source_image_path) != source:
                raise ValueError("同一SDK图片事件的内容发生变化。")
            self._checked(old)
            return old
        asset = ReceivedImage(asset_id=asset_id, item_id=item_id, turn_id=turn_id,
            source_image_path=str(source), file_name=asset_id + source.suffix.lower(),
            sha256=sha, width=width, height=height, revised_prompt=getattr(item, "revised_prompt", None))
        target = _within(self.directory, self.directory / "received-images" / asset.file_name)
        target.parent.mkdir(exist_ok=True)
        atomic_write_bytes(target, raw)
        state.assets.append(asset)
        atomic_json(self.path, state)
        self._view(state)
        return asset

    def restore_from(self, previous: Path):
        if not (previous / INVENTORY).exists() and not (previous / INVENTORY).is_symlink():
            return
        old = ImageAssetStore(previous, self.generated_root, self.thread_id, self.input_digest)
        state = old.read()
        for asset in state.assets:
            _, source = old._checked(asset)
            target = _within(self.directory, self.directory / "received-images" / asset.file_name)
            target.parent.mkdir(exist_ok=True)
            atomic_write_bytes(target, source.read_bytes())
        atomic_json(_within(self.directory, self.path), state)
        self._view(state)
