"""Session-owned images. Only the provider materializes bytes into an API request."""
import base64
import hashlib
import io
import json
import re
import stat
from pathlib import Path
from threading import RLock
from uuid import UUID, uuid4

from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError


MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_IMAGES = 6
MAX_ENCODED = ((MAX_IMAGE_BYTES + 2) // 3) * 4
_ID = re.compile(r"^[a-f0-9]{64}$")
_MIMES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


def _linked(path):
    return path.is_symlink() or (path.exists() and bool(
        getattr(path.lstat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)))


class ChatAttachments:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.lock = RLock()

    def _folder(self, session_id, identifier=None):
        try:
            sid = str(UUID(session_id))
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(404, "附件不存在。") from None
        if identifier is not None and not _ID.fullmatch(identifier):
            raise HTTPException(404, "附件不存在。")
        folder = self.root / sid / "attachments"
        if identifier is not None:
            folder /= identifier
        # Reject symlinks/junction escapes in every component, including a session dir.
        for parent in (folder, *folder.parents):
            if parent == self.root:
                break
            if _linked(parent) or not parent.resolve().is_relative_to(self.root):
                raise HTTPException(404, "附件不存在。")
        return folder

    def put(self, session_id, name, data_base64):
        if not isinstance(data_base64, str) or len(data_base64) > MAX_ENCODED:
            raise HTTPException(413, "单张图片不能超过 4 MiB。")
        try:
            data = base64.b64decode(data_base64, validate=True)
            if not data or len(data) > MAX_IMAGE_BYTES:
                raise ValueError()
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > 30_000_000 or max(image.size) > 8192:
                    raise ValueError()
                if getattr(image, "n_frames", 1) != 1:
                    raise ValueError()
                width, height, kind = image.width, image.height, image.format
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.load()  # JPEG.verify alone does not reject truncated pixel data.
            if kind == "GIF":
                # A static GIF becomes PNG, also accepted by the extraction pipeline.
                with Image.open(io.BytesIO(data)) as image:
                    out = io.BytesIO()
                    image.convert("RGBA").save(out, format="PNG")
                    data, kind = out.getvalue(), "PNG"
                if len(data) > MAX_IMAGE_BYTES:
                    raise ValueError()
            mime = _MIMES[kind]
        except (ValueError, KeyError, OSError, UnidentifiedImageError, Image.DecompressionBombError):
            raise HTTPException(422, "请上传静态 PNG、JPEG、WebP 或 GIF 图片，最长边不超过 8192 像素。") from None
        identifier = hashlib.sha256(data).hexdigest()
        folder = self._folder(session_id, identifier)
        meta = {"id": identifier, "name": Path(name.replace("\\", "/")).name[:160] or "图片",
                "mime_type": mime, "size": len(data), "width": width, "height": height,
                "url": f"/api/agent/sessions/{session_id}/attachments/{identifier}"}
        with self.lock:
            folder.mkdir(parents=True, exist_ok=True)
            # Immutable content-addressed files; an existing upload keeps its original name.
            if (folder / "meta.json").is_file():
                try:
                    return self.get(session_id, identifier)
                except HTTPException:
                    pass  # A fully validated re-upload repairs the same content hash.
            for filename, content in (("image", data), ("meta.json", json.dumps(meta, ensure_ascii=False).encode("utf-8"))):
                target = folder / filename
                if _linked(target):
                    raise HTTPException(404, "附件路径无效。")
                temporary = folder / (uuid4().hex + ".tmp")
                try:
                    temporary.write_bytes(content)
                    temporary.replace(target)
                finally:
                    temporary.unlink(missing_ok=True)
        return meta

    def get(self, session_id, identifier):
        folder = self._folder(session_id, identifier)
        try:
            meta_path, image_path = folder / "meta.json", folder / "image"
            if _linked(meta_path) or _linked(image_path):
                raise ValueError()
            if meta_path.stat().st_size > 4096 or image_path.stat().st_size > MAX_IMAGE_BYTES:
                raise ValueError()
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            raw = image_path.read_bytes()
            if (meta["id"] != identifier or not isinstance(meta["name"], str) or not 1 <= len(meta["name"]) <= 160
                    or type(meta["size"]) is not int or meta["size"] != len(raw)
                    or any(type(meta[key]) is not int or meta[key] <= 0 for key in ("width", "height"))
                    or hashlib.sha256(raw).hexdigest() != identifier):
                raise ValueError()
            with Image.open(io.BytesIO(raw)) as image:
                if meta["mime_type"] != _MIMES.get(image.format) or image.size != (meta["width"], meta["height"]):
                    raise ValueError()
            meta["url"] = f"/api/agent/sessions/{session_id}/attachments/{identifier}"
            return meta
        except (OSError, ValueError, KeyError, TypeError):
            raise HTTPException(404, "图片不存在或已变化，请重新上传。") from None

    def path(self, session_id, identifier):
        self.get(session_id, identifier)
        return self._folder(session_id, identifier) / "image"

    def resolve(self, session_id, reference):
        identifier = reference.get("id", "")
        meta = self.get(session_id, identifier)
        if reference.get("url") != meta["url"]:
            raise HTTPException(403, "图片不属于当前会话。")
        data = self.path(session_id, identifier).read_bytes()
        return f"data:{meta['mime_type']};base64,{base64.b64encode(data).decode('ascii')}"
