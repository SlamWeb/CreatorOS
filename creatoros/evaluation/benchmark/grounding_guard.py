"""Qualify B48 claims only with host-attested search and source evidence."""
from copy import deepcopy
import hashlib
import re

from .research_evidence import VERSION as EVIDENCE_VERSION, MAX_EXCERPT, _url, _instant, MissingResearchEvidence

VERSION = "research-grounding-qualification-v2"
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
    if not isinstance(sources, list) or not 1 <= len(sources) <= 3 or not isinstance(searches, list) or not searches:
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
    return result
