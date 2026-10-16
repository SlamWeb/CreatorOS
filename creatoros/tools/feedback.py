"""Read persisted feedback; never refresh the platform or change content."""
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field

from .studio import _call


class QueryFeedbackArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    creator_id: str = Field(min_length=1, max_length=80, description="真实账号 ID；账号会话由宿主绑定。")
    metric: Literal["favorites", "likes", "comments", "views", "shares"] = Field(
        default="favorites", description="按哪个累计指标降序比较；默认收藏。缺失指标不是 0。")
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=10, ge=1, le=20)


def feedback_summary(data, metric="favorites", offset=0, limit=10):
    notes = []
    for note in data["notes"]:
        latest = note["samples"][-1] if note["samples"] else {}
        notes.append({
            **{key: note.get(key) for key in (
                "request_id", "run_id", "series_id", "series_name", "title", "published_at", "error")},
            "measured_at": latest.get("measured_at"),
            **{key: latest.get(key) for key in ("likes", "favorites", "comments", "views", "shares")},
        })
    # Stable ties retain the service's publication order, not a claim of superiority.
    notes.sort(key=lambda note: (note[metric] is not None, note[metric] or 0), reverse=True)
    end = offset + limit
    return {
        "creator_id": data["creator_id"], "window_days": data["window_days"],
        "scope": "最近窗口内已核实发布的笔记；指标为各笔记最近采样的累计值，不是窗口内增量。",
        "metric": metric, "summary": data["summary"],
        "known_metric_notes": sum(note[metric] is not None for note in notes),
        "last_updated_at": data["last_updated_at"], "collection_error": data.get("error"),
        "items": notes[offset:end],
        "page": {"offset": offset, "limit": limit, "total": len(notes),
                 "next_offset": end if end < len(notes) else None},
        "limitations": "同分无优劣之分；未采集指标为 null。样本少或缺少曝光量时不能推断成功原因或转化率。",
    }


def query_feedback(creator_id, metric="favorites", offset=0, limit=10, context=None):
    return _call(lambda client: feedback_summary(client.request(
        "GET", f"/api/creators/{quote(creator_id, safe='')}/analytics"), metric, offset, limit), context)
