"""Host-owned navigation receipts, separate from model prose and source URLs.

Only explicitly handled DTO locations from completed scoped tools are eligible.
This is not a permission check: the existing Studio API guard runs first.
"""
import json
import re
from urllib.parse import parse_qsl, urlsplit

_ID = r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}"
_ROUTE = re.compile(rf"^/(series|runs)/({_ID})$")
_LABELS = {"series": "查看栏目", "research": "查看调研", "preview": "查看入队预览", "production": "查看内容", "discussion": "查看讨论"}


def _path(value, studio_url):
    if not isinstance(value, str) or len(value) > 1000 or re.search(r"[\s\\%]", value):
        return None
    try:
        url = urlsplit(value)
        if url.fragment or url.username or url.password:
            return None
        if url.netloc or url.scheme:
            origin = urlsplit(studio_url or "")
            if url.scheme not in {"http", "https"} or not origin.netloc or (
                url.scheme, url.netloc
            ) != (origin.scheme, origin.netloc):
                return None
        elif not value.startswith("/") or value.startswith("//"):
            return None
        if not _ROUTE.fullmatch(url.path):
            return None
        pairs = parse_qsl(url.query, keep_blank_values=True)
        if len({key for key, _ in pairs}) != len(pairs) or any(
            key not in {"research", "operation", "discussion", "revision"}
            or not re.fullmatch(_ID, val) for key, val in pairs
        ):
            return None
        return url.path + ("?" + url.query if url.query else "")
    except ValueError:
        return None


def _receipt(data, kind, tool, studio_url):
    if not isinstance(data, dict):
        return None
    path = _path(data.get("url"), studio_url)
    if not path:
        return None
    parsed = urlsplit(path)
    query = dict(parse_qsl(parsed.query))
    route, identity = _ROUTE.fullmatch(parsed.path).groups()
    if kind in {"series", "research", "preview"}:
        series = data.get("series_id") or (data.get("series") or {}).get("id")
        if route != "series" or (series is not None and identity != series):
            return None
        if kind == "series" and (series is None or query):
            return None
        if kind == "research" and query.get("research") != (data.get("batch_id") or data.get("id")):
            return None
        if kind == "research" and set(query) - {"research", "operation"}:
            return None
        if kind == "preview" and (series is None or query != {
            "research": data.get("batch_id"), "operation": data.get("operation_id")
        }):
            return None
    else:
        if route != "runs" or identity != (data.get("run_id") or data.get("id")):
            return None
        if kind == "production" and query:
            return None
        if kind == "discussion" and (query.get("discussion") != (data.get("id") or data.get("request_id"))
            or (data.get("revision_id") and query.get("revision") != data["revision_id"])
            or set(query) - {"discussion", "revision"}):
            return None
    return {"url": path, "label": _LABELS[kind], "source_tool": tool}


def tool_links(name, content, *, is_error=False, error_type=None, studio_url=None):
    """Never recursively harvest links from untrusted topic/Skill/source bodies."""
    try:
        if error_type in {"agent_scope_rejected", "invalid_arguments", "unknown_tool"}:
            return []
        data = json.loads(content)
        if not isinstance(data, dict):
            return []
        if name in {"research_series_topics", "get_topic_research"}:
            if data.get("status") not in {"ready", "researching", "queued", "failed", "unknown", "interrupted", "stale"}:
                return []
            kind, rows = "research", [data]
        elif is_error:
            return []
        elif name == "get_creator_tasks":
            return [link for item in data.get("items", []) if isinstance(item, dict)
                    and item.get("kind") in {"research", "production", "discussion"}
                    if (link := _receipt(item, item["kind"], name, studio_url))]
        elif name in {"list_series_topics", "queue_topics", "compose_series", "update_series_composition", "assign_series"}:
            kind, rows = "series", [data]
        elif name == "prepare_topic_selection":
            kind, rows = "preview", [data]
        elif name in {"start_content_run", "get_content_run", "request_content_revision"}:
            kind, rows = "production", [data]
        elif name in {"discuss_content_run", "get_content_discussion"}:
            kind, rows = "discussion", data.get("items", [data])
        else:
            return []
        return [link for row in rows if (link := _receipt(row, kind, name, studio_url))]
    except (ValueError, TypeError, AttributeError):
        # A presentation failure must not invalidate a completed business action.
        return []


def turn_links(entries, turn_id):
    links = {}
    for entry in entries:
        if entry.get("kind") == "tool" and entry.get("turn_id") == turn_id:
            for link in entry.get("links", []):
                links[link["url"]] = link
    return list(links.values())
