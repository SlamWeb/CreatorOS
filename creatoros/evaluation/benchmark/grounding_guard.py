"""Qualify B48 claims only with host-attested search and source evidence."""
from copy import deepcopy
import hashlib
import re

from .research_evidence import VERSION as EVIDENCE_VERSION, MAX_EXCERPT, MAX_SOURCES, _url, _instant, MissingResearchEvidence

VERSION = "research-grounding-qualification-v4"
SHA = re.compile(r"^[0-9a-f]{64}$")


def _supported(case, judge):
    """This host-owned record must be added *after* parsing the model reply."""
    ev = judge.get("research_evidence") if isinstance(judge, dict) else None
    if not isinstance(ev, dict) or ev.get("version") != EVIDENCE_VERSION or ev.get("case_id") != case.get("id") or ev.get("status") != "complete":
        return False
    for field in ("creator_id", "series_id", "batch_id", "thread_id", "turn_id", "candidate_id", "candidate_angle"):
        if not isinstance(ev.get(field), str) or not ev[field]:
            return False
    sources, sent, searches = ev.get("sources"), ev.get("sent_source_ids"), ev.get("search_event_lines")
    provenance = ev.get("provenance")
    if not isinstance(provenance, dict) or any(
            not isinstance(provenance.get(key), str) or not SHA.fullmatch(provenance[key])
            for key in ("batch_sha256", "events_sha256", "receipt_sha256")):
        return False
    if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_SOURCES or not isinstance(searches, list) or not searches:
        return False
    inventory = ev.get("source_inventory")
    if not isinstance(inventory, list) or len(inventory) != len(sources):
        return False
    if any(not isinstance(row, dict) or row.get("agent_open") != "verified_body" or row.get("host_fetch") != "verified_body"
           for row in inventory):
        return False
    if [(row.get("id"), row.get("url")) for row in inventory] != [(row.get("id"), row.get("url")) for row in sources]:
        return False
    if any(type(i) is not int or i <= 0 for i in searches):
        return False
    ids = []
    try:
        for source in sources:
            if not isinstance(source, dict) or _url(source.get("url")) != source["url"]:
                return False
            if (not isinstance(source.get("id"), str) or not source["id"]
                    or not isinstance(source.get("text"), str) or not 80 <= len(source["text"].strip()) <= MAX_EXCERPT
                    or source.get("text_truncated")):
                return False
            if source.get("text_sha256") != hashlib.sha256(source["text"].encode("utf-8")).hexdigest():
                return False
            if (not isinstance(source.get("agent_text"), str)
                    or not 80 <= len(source["agent_text"].strip()) <= MAX_EXCERPT
                    or source.get("agent_text_sha256") != hashlib.sha256(source["agent_text"].encode("utf-8")).hexdigest()):
                return False
            if (not isinstance(source.get("raw_sha256"), str) or not SHA.fullmatch(source["raw_sha256"])
                    or _instant(source.get("fetched_at")) is None or _instant(source.get("open_event_at")) is None
                    or _instant(source["fetched_at"]) < _instant(source["open_event_at"])):
                return False
            if type(source.get("open_event_line")) is not int or source["open_event_line"] <= min(searches):
                return False
            ids.append(source["id"])
    except (MissingResearchEvidence, KeyError, TypeError):
        return False
    return len(ids) == len(set(ids)) and ids == sent


def _host_supported(case, judge, source_id):
    """Qualify a single *post-task* source claim, not researcher access."""
    ev = judge.get("research_evidence") if isinstance(judge, dict) else None
    if (not isinstance(ev, dict) or ev.get("version") != EVIDENCE_VERSION or ev.get("case_id") != case.get("id")
            or not re.fullmatch(r"source-[1-9][0-9]*", source_id)):
        return False
    provenance = ev.get("provenance")
    if not isinstance(provenance, dict) or any(not isinstance(provenance.get(key), str) or not SHA.fullmatch(provenance[key])
            for key in ("batch_sha256", "events_sha256", "receipt_sha256")):
        return False
    hosts, inventory, sent = ev.get("host_sources"), ev.get("source_inventory"), ev.get("sent_host_source_ids")
    if not isinstance(hosts, list) or not isinstance(inventory, list) or not isinstance(sent, list):
        return False
    if len(hosts) > MAX_SOURCES or any(not isinstance(x, str) for x in sent) or len(sent) != len(set(sent)) or source_id not in sent:
        return False
    matches = [s for s in hosts if isinstance(s, dict) and s.get("id") == source_id]
    rows = [s for s in inventory if isinstance(s, dict) and s.get("id") == source_id]
    if len(matches) != 1 or len(rows) != 1:
        return False
    source, row = matches[0], rows[0]
    try:
        return (row.get("host_fetch") == "verified_body" and source.get("url") == row.get("url")
                and _url(source.get("url")) == source["url"]
                and source.get("provenance") == "host_post_task_independent_fetch"
                and isinstance(source.get("text"), str) and 80 <= len(source["text"].strip()) <= MAX_EXCERPT
                and source.get("text_truncated") is False
                and source.get("text_sha256") == hashlib.sha256(source["text"].encode("utf-8")).hexdigest()
                and isinstance(source.get("raw_sha256"), str) and bool(SHA.fullmatch(source["raw_sha256"]))
                and _instant(source.get("fetched_at")) is not None)
    except (MissingResearchEvidence, KeyError, TypeError):
        return False


def qualify_grounding_judgments(case, judge):
    result = deepcopy(judge)
    if case.get("id") != "B48":
        return result
    supported = _supported(case, judge)
    for item in result.get("items", []):
        if item.get("id") == "b48-s1" and item.get("pass") is not None and not supported:
            item["raw_pass"] = item.get("pass")
            item["pass"] = None
            item["qualification"] = VERSION
            item["evidence"] += "；未采到可核验的搜索、来源打开与独立正文绑定，待复核。"
        match = re.fullmatch(r"b48-(source-[1-9][0-9]*)-support", str(item.get("id", "")))
        if match and item.get("pass") is not None and not _host_supported(case, judge, match.group(1)):
            item["raw_pass"] = item.get("pass")
            item["pass"] = None
            item["qualification"] = VERSION
            item["evidence"] += "；对应事后抓取的正文未完整绑定或未发送 Judge，待复核。"
    return result
