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


def _iteration_receipt(data, tool):
    identity = data.get("iteration_id") or data.get("id")
    if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{64}", identity):
        return None
    path = f"/skills?iteration={identity}"
    if data.get("url") not in {None, path}:
        return None
    title = data.get("title") or data.get("topic_title") or data.get("skill_name")
    return {"url": path, "label": f"校准 · {title[:70]}" if isinstance(title, str) and title else "查看校准任务",
            "kind": "iteration", "source_tool": tool}


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
    link = {"url": path, "label": _LABELS[kind], "kind": kind, "source_tool": tool}
    if kind == "production":
        title = data.get("topic_title") or data.get("title")
        version = data.get("active_revision_number")
        if isinstance(title, str) and title.strip():
            title = title.strip()[:70]
            link.update(title=title, run_id=identity)
            # Fresh Runs may both be revision 1. A short immutable discriminator
            # is preferable to inventing a latest-version relationship.
            suffix = f" · 第{version}版" if type(version) is int and version > 0 else ""
            link["label"] = f"{title}{suffix} · {identity[:6]}"
        if type(version) is int and version > 0:
            link["version"] = version
    return link


def tool_links(name, content, *, is_error=False, error_type=None, studio_url=None):
    """Never recursively harvest links from untrusted topic/Skill/source bodies."""
    try:
        if error_type in {"agent_scope_rejected", "invalid_arguments", "unknown_tool"}:
            return []
        data = json.loads(content)
        if not isinstance(data, dict):
            return []
        if is_error and data.get("error") == "skill_iteration_active":
            link = _iteration_receipt(data, name)
            return [link] if link else []
        if name == "manage_skill_iteration" and not is_error:
            link = _iteration_receipt(data, name)
            return [link] if link else []
        if name in {"create_skill_draft", "extract_skill_from_images", "get_skill_extraction", "save_extracted_skills", "edit_extracted_skills"} and not is_error:
            identity = data.get("id")
            if isinstance(identity, str) and re.fullmatch(r"[a-f0-9]{64}", identity):
                return [{"url": f"/skills?extraction={identity}", "label": "查看 Skill 草稿", "kind": "skill", "source_tool": name}]
            return []
        if name in {"research_series_topics", "get_topic_research"}:
            if data.get("status") not in {"ready", "researching", "queued", "failed", "unknown", "interrupted", "stale"}:
                return []
            kind, rows = "research", [data]
        elif name in {"discuss_content_run", "get_content_discussion"}:
            kind, rows = "discussion", []
            for row in data.get("items", [data]):
                if not isinstance(row, dict) or row.get("status") not in {"queued", "running", "completed", "failed", "interrupted", "unknown"}:
                    continue
                run, identity, revision = row.get("run_id"), row.get("id"), row.get("revision_id")
                if not all(isinstance(value, str) and re.fullmatch(_ID, value) for value in (run, identity, revision)):
                    continue
                rows.append({**row, "url": row.get("url") or f"/runs/{run}?discussion={identity}&revision={revision}"})
        elif is_error:
            return []
        elif name == "get_creator_tasks":
            links = []
            for item in data.get("items", []):
                if not isinstance(item, dict):
                    continue
                if item.get("kind") == "skill_iteration":
                    link = _iteration_receipt(item, name)
                elif item.get("kind") in {"research", "production", "discussion"}:
                    link = _receipt(item, item["kind"], name, studio_url)
                else:
                    continue
                if link:
                    links.append(link)
            return links
        elif name in {"manage_topics", "list_series_topics", "queue_topics", "compose_series", "update_series_composition", "assign_series"}:
            kind, rows = "series", [data]
        elif name == "prepare_topic_selection":
            kind, rows = "preview", [data]
        elif name in {"start_content_run", "get_content_run", "request_content_revision", "import_content_images", "prepare_publication", "get_publication"}:
            kind, rows = "production", data.get("items", [data])
        else:
            return []
        return [link for row in rows if (link :=
            _iteration_receipt(row, name) if isinstance(row, dict) and row.get("error_code") == "skill_iteration_active"
            else _receipt(row, kind, name, studio_url))]
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
