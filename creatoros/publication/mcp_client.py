"""Small, allowlisted adapter for the local xiaohongshu-mcp service.

This module does not grant publication authority or retry writes. Its caller must
hold a confirmed publication request before invoking ``publish``. MCP receipts
are not proof that a remote note exists; verification is a separate read.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, urlsplit

from PIL import Image


class XhsMcpError(RuntimeError):
    """Public-safe error; never includes transport headers or private tokens."""


def _metric(value: Any) -> int | None:
    # Rounded counters such as 1.2万 are not exact observations.
    text = str(value).replace(",", "").strip()
    return int(text) if re.fullmatch(r"\d+", text) else None


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    except FileNotFoundError:
        return False


def normalize_note(data: dict[str, Any]) -> dict[str, Any]:
    note = data.get("note", data)
    if not isinstance(note, dict):
        raise XhsMcpError("笔记详情格式不受支持，未确认发布结果。")
    note_id = str(note.get("noteId", note.get("id", "")))
    if not re.fullmatch(r"[a-fA-F0-9]{24}", note_id):
        raise XhsMcpError("笔记详情缺少有效标识，未确认发布结果。")
    images = note.get("imageList") or []
    stats = note.get("interactInfo") or {}
    timestamp = note.get("time")
    published_at = None
    if isinstance(timestamp, (int, float)) and timestamp > 0:
        try:
            published_at = datetime.fromtimestamp(
                timestamp / 1000 if timestamp > 100_000_000_000 else timestamp,
                tz=timezone.utc,
            ).isoformat()
        except (ValueError, OverflowError, OSError):
            pass
    return {
        "id": note_id,
        "author_id": str((note.get("user") or {}).get("userId", "")),
        "title": str(note.get("title", "")),
        "body": str(note.get("desc", "")),
        "images": [str(i.get("urlDefault") or i.get("urlPre") or "") for i in images],
        "image_count": len(images),
        "published_at": published_at,
        "metrics": {
            "views": None,
            "likes": _metric(stats.get("likedCount")),
            "favorites": _metric(stats.get("collectedCount")),
            "comments": _metric(stats.get("commentCount")),
            "shares": _metric(stats.get("sharedCount")),
        },
        "url": f"https://www.xiaohongshu.com/explore/{note_id}",
    }


class XhsMcpClient:
    ALLOWED_TOOLS = frozenset({"get_my_profile", "get_feed_detail", "publish_content"})

    def __init__(
        self, *, url: str | None = None, token_file: Path | None = None,
        staging_root: Path | None = None, server_staging_root: str | None = None,
    ) -> None:
        root = Path(__file__).resolve().parents[2] / "tmp" / "xhs-mcp-smoke"
        self.url = url or os.getenv("CREATOROS_XHS_MCP_URL", "http://127.0.0.1:18060/mcp")
        parsed = urlsplit(self.url)
        if parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            raise XhsMcpError("第一版仅支持本机小红书 MCP 服务。")
        self.token_file = token_file or Path(os.getenv("CREATOROS_XHS_MCP_TOKEN_FILE", str(root / "auth-token.txt")))
        self.staging_root = staging_root or Path(os.getenv("CREATOROS_XHS_STAGING_ROOT", str(root / "docker-data" / "publications")))
        self.server_staging_root = server_staging_root or os.getenv("CREATOROS_XHS_SERVER_STAGING_ROOT", "/app/data/publications")
        if not PurePosixPath(self.server_staging_root).is_absolute() or ".." in PurePosixPath(self.server_staging_root).parts:
            raise XhsMcpError("MCP 图片目录配置无效。")
        # Access tokens remain memory-only and never enter service results/Trace.
        self._note_tokens: dict[str, str] = {}

    async def _call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in self.ALLOWED_TOOLS:
            raise XhsMcpError("此 MCP 工具未开放。")
        try:
            import httpx
            import anyio
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
            token = os.getenv("CREATOROS_XHS_MCP_TOKEN") or self.token_file.read_text(encoding="utf-8").strip()
            if not token:
                raise XhsMcpError("请先配置本机小红书 MCP 凭证。")
            timeout = 240 if name == "publish_content" else 90
            with anyio.fail_after(timeout + 15):
                async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=timeout, trust_env=False) as http:
                    async with streamable_http_client(self.url, http_client=http) as (read, write, _):
                        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=timeout)) as session:
                            await session.initialize()
                            listing = await session.list_tools()
                            tool = next((tool for tool in listing.tools if tool.name == name), None)
                            if tool is None or not set(tool.inputSchema.get("required", [])).issubset(arguments):
                                raise XhsMcpError("MCP 工具契约已变化，请检查版本。")
                            result = await session.call_tool(name, arguments)
                            if result.isError:
                                raise XhsMcpError("小红书 MCP 未完成请求；发布请求不可直接重试，请先核对结果。")
                            if name == "publish_content":
                                # Upstream reports only prose, no remote ID. Do not echo
                                # arbitrary server text (paths, tokens, or debug content).
                                return {"transport_completed": True, "verification_required": True}
                            structured = getattr(result, "structuredContent", None)
                            if isinstance(structured, dict):
                                return structured
                            for item in result.content:
                                if item.type == "text":
                                    try:
                                        data = json.loads(item.text)
                                    except ValueError:
                                        continue
                                    if isinstance(data, dict):
                                        return data
                            raise XhsMcpError("MCP 没有返回可核对的数据。")
        except asyncio.CancelledError:
            raise
        except ImportError:
            raise XhsMcpError("请安装项目声明的 mcp 依赖。") from None
        except Exception as exc:
            # ExceptionGroup may contain sensitive MCP HTTP errors. Never stringify it.
            if isinstance(exc, XhsMcpError):
                raise
            raise XhsMcpError("无法完成小红书 MCP 请求；若已发送发布请求，请先核对，勿重复发布。") from None

    async def _profile(self) -> dict[str, Any]:
        data = await self._call("get_my_profile", {"tab": "note"})
        for feed in data.get("feeds") or []:
            if feed.get("id") and feed.get("xsecToken"):
                self._note_tokens[str(feed["id"])] = str(feed["xsecToken"])
        return data

    async def identity(self) -> dict[str, str]:
        profile = await self._profile()
        info = profile.get("userBasicInfo") or {}
        # v2.5.5 omits userId from basicInfo. Its explicit `note` tab is the
        # logged-in user's own publications, unlike fav/liked tabs.
        ids = {str((feed.get("noteCard", {}).get("user") or {}).get("userId", "")) for feed in profile.get("feeds") or []}
        ids.discard("")
        direct = info.get("userId")
        if direct:
            ids.add(str(direct))
        if len(ids) != 1 or not re.fullmatch(r"[a-fA-F0-9]{24}", next(iter(ids), "")):
            raise XhsMcpError("无法确认小红书账号的稳定标识；请先检查登录与个人主页，不能仅凭昵称发布。")
        return {"id": next(iter(ids)), "name": str(info.get("nickname") or "小红书账号")}

    def _stage(self, publication_id: str, local_images: list[Path], hashes: list[str]) -> list[str]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", publication_id) or len(local_images) != len(hashes) or not local_images:
            raise XhsMcpError("发布图片快照无效。")
        root = self.staging_root.absolute()
        for parent in (root, *root.parents):
            if _is_link(parent):
                raise XhsMcpError("图片暂存路径不能使用链接目录。")
        directory = root / publication_id
        directory.mkdir(parents=True, exist_ok=True)
        if _is_link(directory) or directory.resolve().parent != root.resolve():
            raise XhsMcpError("图片暂存路径越界。")
        result = []
        for index, (source, expected) in enumerate(zip(local_images, hashes, strict=True)):
            source = Path(source).absolute()
            if any(_is_link(part) for part in (source, *source.parents)):
                raise XhsMcpError("发布图片不能使用链接文件。")
            image_bytes = source.read_bytes()
            if hashlib.sha256(image_bytes).hexdigest() != expected:
                raise XhsMcpError("图片在确认后发生变化，请重新准备发布单。")
            import io
            with Image.open(io.BytesIO(image_bytes)) as image:
                suffix = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}.get(image.format)
                image.verify()
            if suffix is None:
                raise XhsMcpError("发布图片格式不受支持。")
            target = directory / f"{index + 1:02d}{suffix}"
            if _is_link(target):
                raise XhsMcpError("图片暂存文件不能为链接。")
            target.write_bytes(image_bytes)
            result.append(str(PurePosixPath(self.server_staging_root) / publication_id / target.name))
        return result

    async def publish(self, payload: dict[str, Any], publication_id: str, local_images: list[Path]) -> dict[str, Any]:
        identity = await self.identity()
        if identity["id"] != payload.get("account_id"):
            raise XhsMcpError("当前登录账号与确认的发布账号不一致。")
        paths = self._stage(publication_id, local_images, payload.get("image_hashes") or [])
        return await self._call("publish_content", {
            "title": payload["title"], "content": payload["content"],
            "images": paths, "tags": payload.get("tags") or [],
            "visibility": payload["visibility"],
        })

    async def inspect(self, target: str | dict[str, Any]) -> dict[str, Any]:
        if isinstance(target, dict):
            note_id = str(target.get("id", ""))
        else:
            parsed = urlsplit(target)
            if parsed.scheme != "https" or parsed.hostname not in {"www.xiaohongshu.com", "xiaohongshu.com"} or parsed.username or parsed.password:
                raise XhsMcpError("请提供小红书笔记完整链接，不支持短链。")
            match = re.fullmatch(r"/(?:explore|discovery/item)/([a-fA-F0-9]{24})/?", parsed.path)
            if not match:
                raise XhsMcpError("小红书笔记链接格式无效。")
            note_id = match[1]
            token = parse_qs(parsed.query).get("xsec_token", [""])[0]
            if token:
                self._note_tokens[note_id] = token
        if not re.fullmatch(r"[a-fA-F0-9]{24}", note_id):
            raise XhsMcpError("笔记标识无效。")
        if note_id not in self._note_tokens:
            await self._profile()
        token = self._note_tokens.get(note_id)
        if not token:
            raise XhsMcpError("暂时无法读取这条笔记，请提供 App 复制的完整笔记链接后核对。")
        data = await self._call("get_feed_detail", {"feed_id": note_id, "xsec_token": token, "load_all_comments": False})
        note = normalize_note(data.get("data", data))
        if note["id"] != note_id:
            raise XhsMcpError("返回笔记与请求不一致，未确认发布结果。")
        return note

    async def find_candidates(self, payload: dict[str, Any], started_at: datetime) -> list[dict[str, Any]]:
        profile = await self._profile()
        matches = [feed for feed in profile.get("feeds") or [] if str((feed.get("noteCard") or {}).get("displayTitle", "")).strip() == payload["title"].strip()]
        # Detailed identity/time/content checks belong to PublicationService.
        # No title match never implies failure: moderation/listing can be delayed.
        notes = []
        for feed in matches[:10]:
            notes.append(await self.inspect({"id": feed["id"]}))
        return notes
