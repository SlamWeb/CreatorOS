"""Agent actions for the shared BGM catalog and account-scoped playlists."""
from typing import Annotated, Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field

from .argument_contract import ActionContract
from .studio import _call


TrackId = Annotated[str, Field(pattern=r"^bgm-[a-f0-9]{64}$")]
SeriesId = Annotated[str, Field(min_length=1, max_length=80)]


class BgmArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)


class SearchBgmArgs(BgmArgs):
    action: Literal["list"]
    q: str = Field(default="", max_length=200, description="歌曲名或来源中的搜索词，省略时列出共享曲目。")
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=50)


class ReadBgmArgs(BgmArgs):
    action: Literal["get", "analysis"]
    track_id: TrackId


class PlaylistReadArgs(BgmArgs):
    action: Literal["list_playlist"]
    series_id: SeriesId
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=50)


class PlaylistEditArgs(BgmArgs):
    action: Literal["add_to_playlist", "remove_from_playlist"]
    series_id: SeriesId
    track_id: TrackId


class ImportBgmArgs(BgmArgs):
    action: Literal["import_link"]
    source_url: str = Field(min_length=1, max_length=10000, description="用户提供的音乐或视频分享原文。")


class ImportBgmStatusArgs(BgmArgs):
    action: Literal["import_status", "retry_import"]
    import_id: str = Field(pattern=r"^[a-f0-9]{64}$")


class AnalyzeBgmArgs(BgmArgs):
    action: Literal["analyze"]
    track_id: TrackId
    force: bool = Field(default=False, description="仅在用户明确要求重新分析已有结果时设为 true。")


ManageBgmArgs = ActionContract(Annotated[
    SearchBgmArgs | ReadBgmArgs | PlaylistReadArgs | PlaylistEditArgs |
    ImportBgmArgs | ImportBgmStatusArgs | AnalyzeBgmArgs,
    Field(discriminator="action"),
])


def manage_bgm(action, q="", offset=0, limit=20, track_id=None,
               series_id=None, source_url=None, import_id=None, force=False, context=None):
    def invoke(client):
        root = "/api/bgm"
        if action == "list":
            result = client.request("GET", root, params={"q": q})
            items = result["items"]
            end = offset + limit
            result = {"items": items[offset:end], "page": {
                "offset": offset, "limit": limit, "total": len(items),
                "next_offset": end if end < len(items) else None}}
        elif action == "import_link":
            result = client.request("POST", f"{root}/imports", payload={"source_url": source_url})
        elif action in {"import_status", "retry_import"}:
            url = f"{root}/imports/{quote(import_id, safe='')}"
            result = (client.request("POST", f"{url}/retry", payload={}) if action == "retry_import"
                      else client.request("GET", url))
        elif series_id:
            url = f"/api/series/{quote(series_id, safe='')}/bgm"
            if action == "list_playlist":
                result = client.request("GET", url)
                items = result["items"]
                result = {"items": items[offset:offset + limit], "page": {
                    "offset": offset, "limit": limit, "total": len(items),
                    "next_offset": offset + limit if offset + limit < len(items) else None}}
            else:
                method = "PUT" if action == "add_to_playlist" else "DELETE"
                result = client.request(method, f"{url}/{quote(track_id, safe='')}", payload={})
            result.update(series_id=series_id)
        else:
            url = f"{root}/{quote(track_id, safe='')}"
            if action == "analyze":
                result = client.request("POST", f"{url}/analysis/run", payload={"force": force})
            else:
                result = client.request("GET", f"{url}/analysis" if action == "analysis" else url)
        if track_id:
            result.setdefault("track_id", track_id)
        return {**result, "action": action}
    return _call(invoke, context)
