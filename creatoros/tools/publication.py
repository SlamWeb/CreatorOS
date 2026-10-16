"""Publication tools never grant consent or call the remote publishing tool."""
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field

from .studio import _call


class PreparePublicationArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    run_id: str = Field(min_length=1, max_length=80, description="已有内容的 Run ID。")
    title: str | None = Field(default=None, max_length=500, description="可选发布标题；不改原产物。")
    body: str | None = Field(default=None, max_length=10000, description="可选发布正文；不改原产物。")
    tags: list[str] | None = Field(default=None, max_length=10)
    visibility: Literal["公开可见", "仅自己可见", "仅互关好友可见"] = "公开可见"


class GetPublicationArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    publication_id: str = Field(min_length=1, max_length=80, description="准备发布工具返回的发布单 ID。")
    reconcile: bool = Field(default=False, description="是否从平台核对结果；不会再次发布。")
    refresh_metrics: bool = Field(default=False, description="已核实发布后，按用户要求刷新真实互动数据。")


def _summary(data):
    return {
        "publication_id": data["id"], "run_id": data["run_id"],
        "status": data["status"], "account": data["account"],
        "title": data["payload"]["title"], "blockers": data.get("blockers", []),
        "post_url": data.get("post_url"), "message": data.get("last_error") or (
            "已核实发布。" if data["status"] == "published" else
            "仅准备预览，需用户在内容页亲自确认发布。" if data["status"] == "draft" else
            "发布结果尚未核实，不要重新提交发布。"),
        "metrics": data.get("metrics", []), "url": f"/runs/{data['run_id']}",
    }


def prepare_publication(run_id, title=None, body=None, tags=None, visibility="公开可见", context=None):
    return _call(lambda client: _summary(client.request(
        "POST", f"/api/runs/{quote(run_id, safe='')}/publication-request",
        payload={"title": title, "body": body, "tags": tags, "visibility": visibility},
        timeout_seconds=120)), context)


def get_publication(publication_id, reconcile=False, refresh_metrics=False, context=None):
    def query(client):
        base = f"/api/publication-requests/{quote(publication_id, safe='')}"
        if reconcile:
            client.request("POST", base + "/reconcile", payload={}, timeout_seconds=120)
        if refresh_metrics:
            return _summary(client.request("POST", base + "/metrics", payload={}, timeout_seconds=120))
        return _summary(client.request("GET", base))
    return _call(query, context)
