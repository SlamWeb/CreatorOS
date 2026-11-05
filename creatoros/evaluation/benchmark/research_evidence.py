"""B48 source evidence captured separately from the researcher's own claims.

An SDK event proves only what its structured public fields actually say. A
subsequent host fetch is independent verification at a later time: it is NOT a
copy of what the researcher saw. Unknown or incomplete evidence never passes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

VERSION = "research-source-evidence-v3"
ALLOWED_DOMAINS = frozenset({"dictionary.cambridge.org", "www.oxfordlearnersdictionaries.com", "www.merriam-webster.com"})
MAX_SOURCES = 12
MAX_DOCUMENT = 512 * 1024
MAX_EXCERPT = 16000
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
HEX = re.compile(r"^[0-9a-f]{64}$")


class MissingResearchEvidence(ValueError):
    pass


def _instant(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except ValueError:
        return None


def _url(value):
    if not isinstance(value, str) or len(value) > 2000:
        raise MissingResearchEvidence("Source URL missing or oversized")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise MissingResearchEvidence("Invalid source port") from exc
    if (parts.scheme != "https" or parts.hostname not in ALLOWED_DOMAINS or parts.username or parts.password
            or port not in (None, 443) or not parts.path.startswith("/") or parts.query or parts.fragment):
        raise MissingResearchEvidence("Source is outside the explicit dictionary allowlist")
    return urlunsplit(("https", parts.hostname, parts.path, parts.query, ""))


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts = []
    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style", "noscript", "template", "svg"}:
            self.hidden += 1
    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style", "noscript", "template", "svg"} and self.hidden:
            self.hidden -= 1
    def handle_data(self, data):
        if not self.hidden and data.strip():
            self.parts.append(data.strip())


def fetch_public_dictionary(url):
    """Bounded, no-proxy/no-redirect HTTPS GET to exact approved public hosts."""
    canonical = _url(url)
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    request = Request(canonical, headers={"User-Agent": "CreatorOS-Benchmark-Evidence/1.0", "Accept": "text/html,text/plain"})
    try:
        with opener.open(request, timeout=8) as response:
            if response.status != 200 or _url(response.geturl()) != canonical:
                raise MissingResearchEvidence("Source response status or URL mismatch")
            content_type = response.headers.get_content_type()
            if content_type not in {"text/html", "text/plain"}:
                raise MissingResearchEvidence("Unsupported source content type")
            raw = response.read(MAX_DOCUMENT + 1)
            if not raw or len(raw) > MAX_DOCUMENT:
                raise MissingResearchEvidence("Source document is empty or too large")
            charset = response.headers.get_content_charset() or "utf-8"
            try:
                text = raw.decode(charset, errors="strict")
            except (LookupError, UnicodeError) as exc:
                raise MissingResearchEvidence("Source document encoding unavailable") from exc
            if content_type == "text/html":
                parser = _VisibleText()
                parser.feed(text)
                text = " ".join(parser.parts)
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) < 80:
                raise MissingResearchEvidence("Source body is not substantively available")
            return {"url": canonical, "status": 200, "content_type": content_type,
                    "raw_sha256": hashlib.sha256(raw).hexdigest(), "text": text[:MAX_EXCERPT],
                    "text_truncated": len(text) > MAX_EXCERPT,
                    "fetched_at": datetime.now(timezone.utc).isoformat()}
    except (HTTPError, URLError, OSError, TimeoutError) as exc:
        raise MissingResearchEvidence("Source fetch unavailable: " + type(exc).__name__) from exc


def _read_snapshot(root, relative, files, *, limit=4 * 1024 * 1024):
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        raise MissingResearchEvidence("Unsafe research evidence path")
    expected = files.get(rel.as_posix()) if isinstance(files, dict) else None
    if not isinstance(expected, str) or not HEX.fullmatch(expected):
        raise MissingResearchEvidence("Research evidence snapshot hash missing")
    base = Path(root).resolve()
    current = base
    try:
        for name in rel.parts:
            current = current / name
            info = current.lstat()
            if current.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                raise MissingResearchEvidence("Linked research evidence path")
        if not current.resolve().is_relative_to(base) or not current.is_file() or current.stat().st_size > limit:
            raise MissingResearchEvidence("Research evidence is unavailable or outside trial")
        raw = current.read_bytes()
    except OSError as exc:
        raise MissingResearchEvidence("Research evidence file unavailable") from exc
    if hashlib.sha256(raw).hexdigest() != expected:
        raise MissingResearchEvidence("Research evidence changed after snapshot")
    return raw


def _json(root, relative, files):
    try:
        value = json.loads(_read_snapshot(root, relative, files))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MissingResearchEvidence("Research JSON evidence malformed") from exc
    if not isinstance(value, dict):
        raise MissingResearchEvidence("Research JSON evidence is not an object")
    return value


def collect_research_evidence(root, evidence, *, fetcher=fetch_public_dictionary):
    """Bind new candidate/source URLs to a completed SDK search and open.

    A *separate* host fetch supplies public source text to the evaluator. No
    external request is made unless all source identities and both event types
    have been confirmed first. A model-written note is never access evidence.
    """
    result = {"version": VERSION, "status": "needs_review", "sources": [], "errors": [],
              "limitations": "Host pages are fetched after the Agent run and do not prove the exact content the Agent saw."}
    try:
        variables, before, after = evidence["variables"], evidence["before"], evidence["after"]
        series, creator = variables["series_a"], variables["creator_a"]
        records = after["database"]["series"]
        if len([r for r in records if r.get("id") == series and r.get("creator_id") == creator]) != 1:
            raise MissingResearchEvidence("Research series ownership is ambiguous")
        summaries = evidence["background"]["research"]
        if not isinstance(summaries, list):
            raise MissingResearchEvidence("Research batch index missing")
        prefix = "world-producer-skills-topic-research"
        previous_files, files = before["files"], after["files"]
        choices = []
        for row in summaries:
            identifier = row.get("id") if isinstance(row, dict) else None
            if not isinstance(identifier, str) or not SAFE_ID.fullmatch(identifier):
                continue
            record_path = f"{prefix}/batches/{identifier}.json"
            if record_path not in previous_files and row.get("status") == "ready":
                choices.append((identifier, record_path))
        if len(choices) != 1:
            raise MissingResearchEvidence("Expected exactly one new ready research batch")
        batch_id, record_path = choices[0]
        batch = _json(root, record_path, files)
        if batch.get("id") != batch_id or batch.get("series_id") != series or batch.get("status") != "ready":
            raise MissingResearchEvidence("Research batch identity or status mismatch")
        thread, attempt, candidates = batch.get("thread_id"), batch.get("attempt"), batch.get("candidates")
        if (not isinstance(thread, str) or not thread or type(attempt) is not int or attempt < 1
                or not isinstance(candidates, list) or len(candidates) != 1 or not isinstance(candidates[0], dict)):
            raise MissingResearchEvidence("Research thread, attempt or candidate identity unavailable")
        candidate = candidates[0]
        if not isinstance(candidate.get("id"), str) or not candidate["id"] or not isinstance(candidate.get("angle"), str) or not candidate["angle"]:
            raise MissingResearchEvidence("Candidate identity or content unavailable")
        sources = candidate.get("sources")
        if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_SOURCES:
            raise MissingResearchEvidence("Candidate sources missing or exceed bounded verification")
        urls = [_url(row.get("url") if isinstance(row, dict) else None) for row in sources]
        if len(set(urls)) != len(urls):
            raise MissingResearchEvidence("Duplicate candidate source URLs")
        if any(not isinstance(row.get("title"), str) or not row["title"].strip() or len(row["title"]) > 500 for row in sources):
            raise MissingResearchEvidence("Source title missing or oversized")
        workspace = f"{prefix}/work/{batch_id}/{attempt}"
        receipt = _json(root, f"{workspace}/worker_receipt.json", files)
        turns = receipt.get("turns")
        completed = [x.get("id") for x in turns if isinstance(x, dict) and x.get("status") == "completed" and x.get("phase") == "research"] if isinstance(turns, list) else []
        if receipt.get("thread_id") != thread or len(completed) != 1 or not isinstance(completed[0], str):
            raise MissingResearchEvidence("Completed research thread/turn receipt missing")
        turn_id = completed[0]
        raw_events = _read_snapshot(root, f"{workspace}/codex_public_events.jsonl", files)
        try:
            events = [json.loads(line) for line in raw_events.splitlines() if line.strip()]
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise MissingResearchEvidence("Research SDK event stream malformed") from exc
        searches, opened, attempts, snippets = [], {}, {}, {}
        for index, event in enumerate(events, 1):
            if not isinstance(event, dict) or event.get("method") != "item/completed" or event.get("status") != "completed":
                continue
            if event.get("thread_id") != thread or event.get("turn_id") != turn_id:
                continue
            item = event.get("payload", {}).get("item", {})
            if not isinstance(item, dict) or item.get("type") != "webSearch":
                continue
            if event.get("incomplete") or event.get("truncated") or event.get("redacted") or event.get("omissions"):
                raise MissingResearchEvidence("Research SDK event was incomplete or redacted")
            action = item.get("action")
            if not isinstance(action, dict):
                continue
            if action.get("type") == "search" and (action.get("query") or action.get("queries")):
                searches.append(index)
                results = item.get("results")
                if isinstance(results, list):
                    for hit in results:
                        if not isinstance(hit, dict) or hit.get("type") != "text_result":
                            continue
                        try:
                            target = _url(hit.get("url"))
                        except MissingResearchEvidence:
                            continue
                        snippet = hit.get("snippet")
                        title = hit.get("title")
                        if (target in urls and isinstance(snippet, str) and 1 <= len(snippet) <= 2000
                                and isinstance(title, str) and 1 <= len(title) <= 500):
                            snippets.setdefault(target, []).append({"event_line": index, "event_at": event.get("at"),
                                "title": title, "text": snippet, "sha256": hashlib.sha256(snippet.encode("utf-8")).hexdigest()})
            elif action.get("type") == "openPage":
                try:
                    target = _url(action.get("url"))
                except MissingResearchEvidence:
                    continue
                if target in urls:
                    attempts[target] = {"event_line": index, "event_at": event.get("at")}
                results = item.get("results")
                # SDK marks item/completed even when a page failed; its opaque
                # results may be None or an error page. A URL/title alone is
                # not a transport success or proof that meaningful source text
                # reached the researcher. Require an explicit status and an
                # intact captured body; otherwise keep the case unknown.
                matches = [x for x in results if isinstance(x, dict) and x.get("type") == "text_result"
                           and x.get("url") == target and type(x.get("status_code")) is int
                           and x["status_code"] == 200 and x.get("content_type") in {"text/plain", "text/html"}
                           and isinstance(x.get("body"), str) and 80 <= len(x["body"].strip()) <= MAX_EXCERPT
                           and x.get("truncated") is False and not x.get("error")
                           and isinstance(x.get("title"), str) and x["title"].strip()] if isinstance(results, list) else []
                if len(matches) == 1:
                    opened[target] = {"line": index, "at": event.get("at"), "agent_text": matches[0]["body"]}
        if not searches:
            raise MissingResearchEvidence("Completed search event not evidenced")
        captured = []
        inventory = []
        for number, url in enumerate(urls, 1):
            opened_item = opened.get(url)
            valid_open = (opened_item is not None and opened_item["line"] > min(searches)
                          and _instant(opened_item["at"]) is not None)
            row = {"id": f"source-{number}", "url": url, "title": sources[number-1].get("title"),
                   "search_snippets": snippets.get(url, [])[:5],
                   "agent_open": "verified_body" if valid_open else "attempted_no_body" if url in attempts else "not_evidenced",
                   "open_attempt": attempts.get(url),
                   "host_fetch": "not_attempted"}
            inventory.append(row)
            if not valid_open:
                continue
            try:
                page = fetcher(url)
            except (MissingResearchEvidence, OSError, TimeoutError, ValueError):
                row["host_fetch"] = "unavailable"
                continue
            if not isinstance(page, dict) or page.get("url") != url or page.get("status") != 200:
                row["host_fetch"] = "unavailable"
                continue
            text, digest, fetched_at = page.get("text"), page.get("raw_sha256"), page.get("fetched_at")
            if (not isinstance(text, str) or len(text.strip()) < 80 or len(text) > MAX_EXCERPT
                    or not isinstance(digest, str) or not HEX.fullmatch(digest)
                    or _instant(fetched_at) is None or _instant(fetched_at) < _instant(opened[url]["at"])
                    or page.get("text_truncated")):
                row["host_fetch"] = "unavailable"
                continue
            row["host_fetch"] = "verified_body"
            captured.append({"id": f"source-{number}", "url": url, "title": sources[number-1].get("title"),
                             "text": text, "raw_sha256": digest, "fetched_at": fetched_at,
                             "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                             "agent_text": opened[url]["agent_text"],
                             "agent_text_sha256": hashlib.sha256(opened[url]["agent_text"].encode("utf-8")).hexdigest(),
                             "text_truncated": bool(page.get("text_truncated")),
                             "open_event_line": opened[url]["line"], "open_event_at": opened[url]["at"]})
        complete = len(captured) == len(urls)
        result.update(status="complete" if complete else "needs_review", case_id="B48", creator_id=creator, series_id=series,
                      batch_id=batch_id, thread_id=thread, turn_id=turn_id, candidate_id=candidate["id"],
                      candidate_title=candidate.get("title"), candidate_angle=candidate["angle"],
                      search_event_lines=searches, sources=captured, source_inventory=inventory,
                      provenance={"batch_path": record_path, "batch_sha256": files[record_path],
                          "events_path": f"{workspace}/codex_public_events.jsonl",
                          "events_sha256": files[f"{workspace}/codex_public_events.jsonl"],
                          "receipt_path": f"{workspace}/worker_receipt.json",
                          "receipt_sha256": files[f"{workspace}/worker_receipt.json"]})
        if not complete:
            result["errors"].append("Some cited sources lack verified agent-open bodies or independent fetch; snippets alone are partial evidence")
    except (KeyError, TypeError, ValueError, MissingResearchEvidence) as exc:
        result["errors"].append(str(exc))
    return result
