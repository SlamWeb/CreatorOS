"""Read only nested Codex evidence from one disposable benchmark world.

SDK totals are thread cumulative. Attempt deltas and copied checkpoints are
retained, but never blindly added to cumulative totals or counted as new images.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import re

from sqlalchemy import text

from creatoros.integrations.codex_public_events import sanitize_public


COUNTERS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")
METADATA = {"native_checkpoint.json", "video_checkpoint.json", "visual_checkpoint.json",
            "artifact-submission.json", "native_image_assets.json", "worker_receipt.json", "production_session.json"}


def _objects(value):
    """Decode JSON-encoded public tool results without executing their contents."""
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str) and item.lstrip().startswith(("{", "[")):
            try:
                stack.append(json.loads(item))
            except (ValueError, RecursionError):
                pass
        elif isinstance(item, dict):
            yield item
            stack.extend(v for v in item.values() if isinstance(v, (dict, list, str)))
        elif isinstance(item, list):
            stack.extend(item)


def _usage(value):
    if not isinstance(value, dict):
        return None
    aliases = dict(zip(COUNTERS, ("inputTokens", "cachedInputTokens", "outputTokens", "reasoningOutputTokens")))
    result = {key: value.get(key, value.get(aliases[key])) for key in COUNTERS}
    return {key: number if type(number) is int and number >= 0 else None for key, number in result.items()}


def collect_codex_evidence(world) -> dict:
    root = Path(world.root).resolve()
    result = {"schema_version": 1, "scope": "isolated_world_only", "attempts": [], "files": [],
              "parse_errors": [], "excluded": [], "submit_artifacts": [], "image_assets": [],
              "event_counts": {}, "threads": [], "usage": {}, "nested_constraints": {}}
    seeded = {world.variables.get("run_a"), world.variables.get("run_b")}
    thread_ids, completed_turns, generated = set(), set(), set()
    generation_events = []
    observations = defaultdict(list)
    call_names = {}
    event_counts = Counter()

    def relative(path):
        path = Path(path)
        if not path.resolve().is_relative_to(root):
            raise ValueError("Codex evidence path escaped the disposable world")
        return path.relative_to(root).as_posix()

    def error(path, kind, **extra):
        result["parse_errors"].append({"path": relative(path), "kind": kind, **extra})

    def observe_usage(thread, value, source, *, definitive=False):
        parsed = _usage(value)
        if not thread or not parsed or not any(v is not None for v in parsed.values()):
            return
        # A default empty CodexUsage is not evidence of measured zero usage.
        if not definitive and not any((v or 0) > 0 for v in parsed.values()):
            return
        thread_ids.add(thread)
        observations[thread].append({"source": source, "scope": "thread_cumulative", "usage": parsed})

    with world.database.engine.connect() as connection:
        rows = connection.execute(text("""SELECT a.id,a.revision_id,a.attempt_number,a.status,
            a.producer_thread_id,a.output_directory,a.usage_json,a.error_type,r.content_run_id
            FROM content_attempts a JOIN content_revisions r ON r.id=a.revision_id""")).mappings()
        for row in rows:
            if row["content_run_id"] in seeded:
                continue
            entry = dict(row)
            directory = entry.pop("output_directory")
            if directory:
                try:
                    entry["output_directory"] = relative(directory)
                except ValueError:
                    result["excluded"].append({"attempt_id": entry["id"], "reason": "output_directory_outside_world"})
                    continue
            value = entry["usage_json"]
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except ValueError:
                    value = None
                    result["parse_errors"].append({"attempt_id": entry["id"], "kind": "invalid_usage_json"})
            entry["usage_json"] = sanitize_public(value)[0]
            entry["usage_aggregation"] = "preserved_only; native/video per-attempt delta is not added to thread cumulative"
            if entry["producer_thread_id"]:
                thread_ids.add(entry["producer_thread_id"])
            result["attempts"].append(entry)

    directories = [Path(world.runs.output_root), Path(world.extractions.root) / "jobs", Path(world.research.root) / "work"]
    paths = set()
    for directory in directories:
        if not directory.resolve().is_relative_to(root):
            result["excluded"].append({"reason": "scan_root_outside_world"})
            continue
        for base, dirs, names in os.walk(directory, followlinks=False):
            for name in dirs[:]:
                candidate = Path(base) / name
                if name in {"node_modules", ".git", "__pycache__", ".venv"} or candidate.is_symlink() or getattr(candidate, "is_junction", lambda: False)():
                    dirs.remove(name)
                    result["excluded"].append({"path": candidate.relative_to(root).as_posix(), "reason": "dependency_or_link"})
            for name in names:
                if name in METADATA or name.endswith("_usage.json") or name in {"codex_trace.jsonl", "codex_public_events.jsonl"}:
                    paths.add(Path(base) / name)

    for path in sorted(paths):
        try:
            if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
                result["excluded"].append({"path": path.relative_to(root).as_posix(), "reason": "linked_file"})
                continue
            rel = relative(path)
            size = path.stat().st_size
        except (OSError, ValueError) as exc:
            result["parse_errors"].append({"path": path.relative_to(root).as_posix(), "kind": type(exc).__name__})
            continue
        summary = {"path": rel, "size_bytes": size, "records": 0, "parsed": 0}
        sha = hashlib.sha256()
        if path.suffix != ".jsonl":
            try:
                raw = path.read_bytes(); sha.update(raw)
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError("Expected metadata object")
                clean, redacted, omissions = sanitize_public(value)
                summary.update(records=1, parsed=1, metadata=clean, redacted=redacted, omissions=omissions)
                thread = value.get("thread_id")
                if thread:
                    thread_ids.add(thread)
                if "checkpoint" in path.name or path.name == "production_session.json":
                    observe_usage(thread, value.get("usage"), rel)
                if path.name == "worker_receipt.json":
                    completed_turns.update((thread, turn.get("id")) for turn in value.get("turns", []) if turn.get("status") == "completed")
                if path.name == "native_image_assets.json":
                    for asset in value.get("assets", []):
                        local = path.parent / "received-images" / asset.get("file_name", "")
                        exists = local.resolve().is_relative_to(root) and local.is_file()
                        result["image_assets"].append({"thread_id": thread, "turn_id": asset.get("turn_id"),
                            "item_id": asset.get("item_id"), "asset_id": asset.get("asset_id"), "sha256": asset.get("sha256"),
                            "width": asset.get("width"), "height": asset.get("height"), "inventory": rel,
                            "local_path": relative(local) if exists else None, "exists": exists,
                            "size_bytes": local.stat().st_size if exists else None})
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                error(path, type(exc).__name__)
        else:
            try:
                with path.open("rb") as stream:
                    for line_number, raw in enumerate(stream, 1):
                        sha.update(raw)
                        if not raw.strip():
                            continue
                        summary["records"] += 1
                        try:
                            value = json.loads(raw)
                            summary["parsed"] += 1
                            method = value.get("method") or value.get("type") or "unknown"
                            event_counts[method] += 1
                            thread, turn = value.get("thread_id"), value.get("turn_id")
                            if thread:
                                thread_ids.add(thread)
                            payload = value.get("payload") or {}
                            if method == "thread/tokenUsage/updated":
                                usage = payload.get("token_usage", payload.get("tokenUsage", {}))
                                observe_usage(thread, usage.get("total"), f"{rel}:{line_number}", definitive=True)
                            if method == "turn/completed" and (payload.get("turn") or {}).get("status") == "completed":
                                completed_turns.add((thread, turn or payload["turn"].get("id")))
                            item = payload.get("item") or value.get("item") or {}
                            kind = item.get("type") or value.get("item_type")
                            item_id = item.get("id") or value.get("item_id")
                            if method == "item/completed" and kind == "imageGeneration" and value.get("status", item.get("status")) == "completed" and not item.get("failure"):
                                generated.add((thread, turn, item_id))
                                generation_events.append({"path": rel, "line": line_number, "at": value.get("at"),
                                                          "thread_id": thread, "turn_id": turn, "item_id": item_id})
                            name = item.get("tool") or item.get("name") or item.get("tool_name")
                            call_id = item.get("call_id") or item.get("callId") or item_id
                            if name:
                                call_names[(thread, turn, call_id)] = name
                            name = name or call_names.get((thread, turn, call_id))
                            if kind not in {"dynamicToolCall", "mcpToolCall", "functionCallOutput"} or name != "submit_artifacts":
                                continue
                            # Only tool OUTPUT fields can prove accepted/rejected. Arguments cannot.
                            output = [item.get(key) for key in ("output", "result", "content_items", "contentItems", "content", "structured_content", "structuredContent") if item.get(key) is not None]
                            outcomes = [v for v in _objects(output) if type(v.get("accepted")) is bool]
                            clean, redacted, omissions = sanitize_public(item)
                            result["submit_artifacts"].append({"path": rel, "line": line_number, "at": value.get("at"),
                                "thread_id": thread, "turn_id": turn, "item_id": item_id, "method": method,
                                "public_item": clean, "outcomes": sanitize_public(outcomes)[0], "redacted": redacted,
                                "incomplete": bool(value.get("incomplete") or value.get("truncated")), "omissions": omissions})
                        except (ValueError, TypeError, AttributeError) as exc:
                            error(path, type(exc).__name__, line=line_number)
            except OSError as exc:
                error(path, type(exc).__name__)
        summary["sha256"] = sha.hexdigest()
        result["files"].append(summary)

    result["event_counts"] = dict(event_counts)
    result["threads"] = sorted(thread_ids)
    per_thread = []
    for thread in sorted(thread_ids):
        observed = observations[thread]
        total = {key: max([row["usage"][key] for row in observed if row["usage"][key] is not None], default=None) for key in COUNTERS}
        per_thread.append({"thread_id": thread, "usage": total, "observations": observed,
                           "policy": "maximum observed cumulative counter per thread; copies/resumed Attempts are not summed"})
    totals = {key: sum(row["usage"][key] for row in per_thread) if per_thread and all(row["usage"][key] is not None for row in per_thread) else None for key in COUNTERS}
    result["usage"] = {"by_thread": per_thread, "total": totals, "status": "complete" if per_thread and all(v is not None for v in totals.values()) else "unavailable_or_partial", "reasoning_tokens_included_in_output": True}
    assets = {(v["thread_id"], v["turn_id"], v["item_id"]) for v in result["image_assets"] if v["exists"]}
    result["images"] = {"received_unique": len(assets), "completed_generation_items": len(generated),
                        "generation_identities": [list(v) for v in sorted(generated, key=str)],
                        "generation_events": generation_events,
                        "policy": "SDK item identity, not PNG count/reference copies; no source_image_path outside world is read"}

    file_map = {entry["path"]: entry for entry in result["files"]}
    accepted_receipts = []
    for entry in result["files"]:
        if not entry["path"].endswith("/artifact-submission.json"):
            continue
        value = entry.get("metadata", {})
        checkpoint = file_map.get(str(Path(entry["path"]).with_name("native_checkpoint.json")).replace("\\", "/"), {}).get("metadata", {})
        valid = (value.get("accepted") is True and value.get("thread_id") == checkpoint.get("thread_id")
                 and value.get("input_digest") == checkpoint.get("input_digest")
                 and checkpoint.get("submission_sha256") == entry["sha256"])
        accepted_receipts.append({"path": entry["path"], "thread_id": value.get("thread_id"),
                                  "turn_id": value.get("turn_id"), "checkpoint_bound": valid})
    result["accepted_receipts"] = accepted_receipts

    constraints = world.case.get("nested_codex_constraints")
    if not constraints:
        result["nested_constraints"] = {"status": "not_applicable", "detail": "Case declares no nested Codex constraints."}
        return result
    events = result["submit_artifacts"]
    rejected, accepted = [], []
    for event in events:
        if event["method"] != "item/completed" or event["incomplete"]:
            continue
        for outcome in event["outcomes"]:
            if outcome["accepted"]:
                accepted.append(event)
            elif re.search(r"path|路径|不存在|not.?found|越界", str(outcome.get("error", "")), re.I):
                rejected.append(event)
    pairs = []
    for first in rejected:
        for second in accepted:
            same = first["thread_id"] and first["turn_id"] and (first["thread_id"], first["turn_id"]) == (second["thread_id"], second["turn_id"])
            ordered = (first["path"] == second["path"] and first["line"] < second["line"]) or (first["at"] and second["at"] and first["at"] < second["at"])
            generated_before = any((event["thread_id"], event["turn_id"]) == (first["thread_id"], first["turn_id"])
                and ((event["path"] == first["path"] and event["line"] < first["line"]) or (event["at"] and first["at"] and event["at"] < first["at"])) for event in generation_events)
            if same and ordered and generated_before and (second["thread_id"], second["turn_id"]) in completed_turns:
                pairs.append({"thread_id": first["thread_id"], "turn_id": first["turn_id"], "rejected": [first["path"], first["line"]], "accepted": [second["path"], second["line"]]})
    contradictions = []
    if constraints.get("new_threads") is not None and len(thread_ids) > constraints["new_threads"]:
        contradictions.append("Observed more new threads than allowed")
    if constraints.get("new_image_generations") is not None and len(generated) > constraints["new_image_generations"]:
        contradictions.append("Observed more image-generation items than allowed")
    pair_bound = any(any(receipt["checkpoint_bound"] and (receipt["thread_id"], receipt["turn_id"]) == (pair["thread_id"], pair["turn_id"]) for receipt in accepted_receipts)
                     and all((thread, turn) == (pair["thread_id"], pair["turn_id"]) for thread, turn, _ in generated | assets) for pair in pairs)
    complete = pair_bound and len(thread_ids) == constraints.get("new_threads", len(thread_ids)) and len(generated) == constraints.get("new_image_generations", len(generated)) and len(assets) == constraints.get("new_image_generations", len(assets))
    status = "failed" if contradictions else ("passed" if complete and not result["parse_errors"] else "needs_review")
    result["nested_constraints"] = {"status": status, "detail": "; ".join(contradictions) if contradictions else ("Same thread/turn rejected invalid path, then accepted, completed, with exactly one observed/received image" if complete else "Missing same-thread/turn ordered rejection→accepted/completed or exact image-generation evidence; final images alone do not prove repair"), "pairs": pairs, "rejections": len(rejected), "acceptances": len(accepted)}
    return result
