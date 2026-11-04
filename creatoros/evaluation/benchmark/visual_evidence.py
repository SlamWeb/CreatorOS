"""Bind Judge images and candidate assets to captured business evidence.

Only isolated-world files with snapshot-verified hashes are considered. These
bindings identify the pixels; they do not assert what the pixels depict or
prove the model interpreted them correctly.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


VERSION = "visual-bindings-v1"
SHA = re.compile(r"^[0-9a-f]{64}$")


class UnavailableVisualEvidence(ValueError):
    pass


def _read(root, relative, expected):
    root = Path(root).resolve()
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts or not SHA.fullmatch(expected or ""):
        raise UnavailableVisualEvidence("Unsafe media path or missing checksum")
    part = root
    for name in rel.parts:
        part = part / name
        if part.is_symlink() or getattr(part.lstat(), "st_file_attributes", 0) & 0x400:
            raise UnavailableVisualEvidence("Linked media paths cannot be verified")
    resolved = part.resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise UnavailableVisualEvidence("Media is outside the trial or unavailable")
    body = resolved.read_bytes()
    if hashlib.sha256(body).hexdigest() != expected:
        raise UnavailableVisualEvidence("Media bytes differ from snapshot")
    return body


def _uploaded_digest(browser, before):
    turns = browser.get("turns") if isinstance(browser, dict) else None
    if not isinstance(turns, list) or not turns:
        raise UnavailableVisualEvidence("Missing browser user turn")
    turn = turns[0]
    if not isinstance(turn, dict):
        raise UnavailableVisualEvidence("Malformed browser user turn")
    session_id, turn_id = browser.get("session_id"), turn.get("turn_id")
    accepted = turn.get("accepted", {})
    if (not isinstance(session_id, str) or not session_id or not isinstance(turn_id, str) or not turn_id
            or turn.get("session_id") != session_id or not isinstance(accepted, dict)
            or accepted.get("id") != session_id):
        raise UnavailableVisualEvidence("Browser session/turn identity missing or inconsistent")
    entries = accepted.get("entries", []) if isinstance(accepted, dict) else []
    user = [x for x in entries if isinstance(x, dict) and x.get("kind") == "user"]
    if (len(user) != 1 or user[0].get("turn_id") != turn_id
            or not isinstance(user[0].get("attachments"), list) or len(user[0]["attachments"]) != 1):
        raise UnavailableVisualEvidence("Cannot identify the user's single uploaded attachment")
    attachment = user[0]["attachments"][0]
    digest = attachment.get("id") if isinstance(attachment, dict) else None
    files = before.get("files") if isinstance(before, dict) else None
    if not isinstance(digest, str) or not SHA.fullmatch(digest) or not isinstance(files, dict) or files.get("inputs/reference.png") != digest:
        raise UnavailableVisualEvidence("User attachment receipt and frozen input checksum do not match")
    return digest


def candidate_attachment_evidence(evidence):
    """Check B27 against the actual attachment and the returned candidate inventory."""
    from .grader import MissingEvidence, _selected_candidate
    try:
        before, after = evidence["before"], evidence["after"]
        family = evidence["variables"]["skill_a"]
        uploaded = _uploaded_digest(evidence.get("browser"), before)
        candidate, prefix = _selected_candidate(evidence, family)
        original_prefix = f"world-producer-skills/working/{family}/assets/"
        original = {p[len(original_prefix):]: digest for p, digest in before["files"].items()
                    if p.startswith(original_prefix)}
        if not original or uploaded in original.values():
            raise UnavailableVisualEvidence("Original assets missing or indistinguishable from uploaded image")
        after_files = after["files"]
        target = f"{prefix}/assets/reference-new.png"
        expected_old = {f"{prefix}/assets/{name}": digest for name, digest in original.items()}
        wrong_old = [p for p, digest in expected_old.items() if after_files.get(p) != digest]
        result = after_files.get(target) == uploaded and not wrong_old
        return {"status": "passed" if result else "failed", "attachment_sha256": uploaded,
                "candidate_id": candidate["id"], "candidate_path": target,
                "candidate_sha256": after_files.get(target), "old_assets_changed": wrong_old}
    except (KeyError, TypeError, MissingEvidence, UnavailableVisualEvidence) as error:
        return {"status": "needs_review", "detail": str(error)}


def _sent_to_agent_after_receipt(evidence, call, digest):
    """Require the same successful tool receipt and image in a later Agent input."""
    browser, requests = evidence.get("browser"), evidence.get("requests")
    if not isinstance(browser, dict) or not isinstance(requests, list):
        return False
    session, turns, turn_number = browser.get("session_id"), browser.get("turns"), call.get("turn_index")
    if (not isinstance(session, str) or not session or not isinstance(turns, list)
            or type(turn_number) is not int or not 1 <= turn_number <= len(turns)):
        return False
    turn = turns[turn_number - 1]
    if (not isinstance(turn, dict) or turn.get("session_id") != session or not turn.get("turn_id")
            or call.get("turn_id") != turn["turn_id"] or not isinstance(call.get("tool_call_id"), str)
            or not call["tool_call_id"]):
        return False
    expected_url = f"/api/agent/sessions/{session}/attachments/{digest}"
    for request in requests:
        if (not isinstance(request, dict) or request.get("purpose") != "agent" or "error" not in request
                or request["error"] is not None or type(request.get("elapsed_seconds")) not in (int, float)
                or request["elapsed_seconds"] < 0):
            continue
        context = request.get("context")
        messages = context.get("messages") if isinstance(context, dict) else None
        if not isinstance(messages, list):
            continue
        seen_receipt = False
        for message in messages:
            if not isinstance(message, dict):
                continue
            if message.get("role") == "tool" and message.get("tool_call_id") == call["tool_call_id"]:
                try:
                    content = message.get("content")
                    content = json.loads(content) if isinstance(content, str) else content
                    attachment = content["attachment"]
                    seen_receipt = attachment.get("id") == digest and attachment.get("url") == expected_url
                except (ValueError, TypeError, KeyError, AttributeError):
                    seen_receipt = False
            elif seen_receipt and message.get("role") == "user" and isinstance(message.get("content"), list):
                for part in message["content"]:
                    if not isinstance(part, dict) or part.get("type") != "image_ref":
                        continue
                    attachment = part.get("attachment")
                    if isinstance(attachment, dict) and attachment.get("id") == digest and attachment.get("url") == expected_url:
                        return True
    return False


def collect_visual_evidence(root, case, evidence):
    """Return image identities and validated paths for the supported scenarios."""
    root = Path(root).resolve()
    result = {"version": VERSION, "status": "needs_review", "bindings": [], "errors": []}
    cid = case.get("id")
    try:
        variables, before, after = evidence["variables"], evidence["before"], evidence["after"]
        files = before["files"] if cid in {"B29", "B44", "B45"} else after["files"]
        bindings = []
        def add(role, path, sha, **identity):
            if files.get(path) != sha:
                raise UnavailableVisualEvidence("Media does not match the captured file snapshot")
            _read(root, path, sha)
            bindings.append({"id": f"image-{len(bindings)+1}", "role": role, "path": path,
                             "sha256": sha, **identity})

        if cid == "B29":
            family = variables["skill_a"]
            skill_path = evidence["skill_paths"].get(family)
            if skill_path != f"world-producer-skills/working/{family}":
                raise UnavailableVisualEvidence("Skill working identity unavailable")
            path = skill_path + "/assets/reference-01.png"
            add("skill_reference", path, files.get(path), skill_id=family, asset="assets/reference-01.png")
        if cid in {"B44", "B45"}:
            sha = _uploaded_digest(evidence.get("browser"), before)
            add("user_attachment", "inputs/reference.png", sha, attachment_id=sha)
        if cid in {"B41", "B42", "B45", "B49"}:
            state = before if cid == "B45" else after
            db = state["database"]
            old_ids = {row["id"] for row in before["database"]["content_runs"]}
            if cid == "B45":
                runs = [row for row in db["content_runs"] if row.get("id") == variables["run_a"]]
            else:
                runs = [row for row in db["content_runs"] if row.get("id") not in old_ids
                        and row.get("topic_id") == variables["topic_a"]]
            expected_runs = {"B41": 1, "B42": 3, "B45": 1, "B49": 2}[cid]
            if len(runs) != expected_runs or len({row.get("id") for row in runs}) != expected_runs:
                raise UnavailableVisualEvidence("Run identity/count incomplete")
            task_rows = db.get("production_tasks")
            if not isinstance(task_rows, list) or any(not isinstance(row, dict) or not row.get("id") for row in task_rows):
                raise UnavailableVisualEvidence("Production task inventory unavailable")
            tasks = {row["id"]: row for row in task_rows}
            if len(tasks) != len(task_rows):
                raise UnavailableVisualEvidence("Production task identities are ambiguous")
            if cid in {"B41", "B42", "B49"}:
                task_ids = [run.get("task_id") for run in runs]
                if any(not isinstance(key, str) or not key for key in task_ids) or len(set(task_ids)) != len(task_ids):
                    raise UnavailableVisualEvidence("Each Run requires its own distinct production task")
            for run in runs:
                snapshots = [row for row in db["content_revisions"] if row.get("content_run_id") == run["id"]
                             and row.get("revision_number") == run.get("active_revision_number")]
                if len(snapshots) != 1:
                    raise UnavailableVisualEvidence("Unique active revision unavailable")
                revision = snapshots[0]
                frozen = revision.get("production_input_json")
                frozen = json.loads(frozen) if isinstance(frozen, str) else frozen
                if not isinstance(frozen, dict) or frozen.get("creator_id") != variables["creator_a"]:
                    raise UnavailableVisualEvidence("Run creator identity unavailable or out of scope")
                validation = revision.get("validation_json")
                validation = json.loads(validation) if isinstance(validation, str) else validation
                media = validation.get("images") if isinstance(validation, dict) else None
                expected_images = 2 if cid == "B45" else 1
                if not isinstance(media, list) or len(media) != expected_images:
                    raise UnavailableVisualEvidence("Expected image inventory unavailable")
                if cid == "B45":
                    # The reference is a separate role. Present the two
                    # results in their verified revision order, never in raw
                    # JSON/file order; duplicate or missing positions are
                    # ambiguous and must not reach visual scoring.
                    orders = [image.get("order") if isinstance(image, dict) else None for image in media]
                    if (any(type(order) is not int for order in orders)
                            or sorted(orders) != list(range(1, expected_images + 1))):
                        raise UnavailableVisualEvidence("Run image order is missing, duplicate or invalid")
                    media = sorted(media, key=lambda image: image["order"])
                artifact_dir = Path(revision["artifact_directory"])
                if not artifact_dir.is_absolute() or not artifact_dir.is_relative_to(root):
                    raise UnavailableVisualEvidence("Artifact directory outside trial")
                task_id = run.get("task_id")
                if cid in {"B41", "B42", "B49"}:
                    task = tasks.get(task_id)
                    definition = task.get("definition_json") if task else None
                    definition = json.loads(definition) if isinstance(definition, str) else definition
                    if (not task or not isinstance(definition, dict) or frozen.get("task_id") != task_id
                            or run.get("topic_id") != variables["topic_a"] or frozen.get("topic_id") != run.get("topic_id")
                            or task.get("topic_id") != run.get("topic_id")
                            or definition.get("creator_id") != variables["creator_a"]
                            or not frozen.get("series_id") or frozen.get("series_id") != definition.get("series_id")
                            or not isinstance(frozen.get("topic_title"), str) or not frozen["topic_title"]
                            or not isinstance(frozen.get("topic_brief"), str) or not frozen["topic_brief"]):
                        raise UnavailableVisualEvidence("Run-to-task topic/creator/series binding unavailable")
                for image in media:
                    if not isinstance(image, dict) or not isinstance(image.get("path"), str):
                        raise UnavailableVisualEvidence("Image manifest malformed")
                    path = (artifact_dir / image["path"]).relative_to(root).as_posix()
                    add("run_image", path, image.get("sha256"), run_id=run["id"], revision_id=revision["id"],
                        image_order=image.get("order"), task_id=task_id,
                        task_title=frozen.get("topic_title"), task_brief=frozen.get("topic_brief"))
        if not bindings or cid not in {"B29", "B41", "B42", "B44", "B45", "B49"}:
            raise UnavailableVisualEvidence("No supported visual evidence")
        read_verified = None
        if cid in {"B29", "B45"}:
            calls = evidence.get("calls")
            if isinstance(calls, list):
                observed = set()
                for call in calls:
                    if not isinstance(call, dict) or call.get("tool") != "read_image" or call.get("is_error") is not False:
                        continue
                    try:
                        args = call.get("arguments")
                        args = json.loads(args) if isinstance(args, str) else args
                        raw = call.get("raw_result")
                        raw = json.loads(raw) if isinstance(raw, str) else raw
                        actual_sha = raw["attachment"]["id"]
                        for binding in bindings:
                            if binding["role"] == "skill_reference" and args.get("skill_id") == binding["skill_id"] and args.get("path") == binding["asset"] and actual_sha == binding["sha256"] and _sent_to_agent_after_receipt(evidence, call, actual_sha):
                                observed.add(binding["id"])
                            if binding["role"] == "run_image" and args.get("run_id") == binding["run_id"] and args.get("revision_id") == binding["revision_id"] and args.get("image_index") == binding["image_order"] and actual_sha == binding["sha256"] and _sent_to_agent_after_receipt(evidence, call, actual_sha):
                                observed.add(binding["id"])
                    except (KeyError, TypeError, ValueError, AttributeError):
                        continue
                expected = {row["id"] for row in bindings if row["role"] in {"skill_reference", "run_image"}}
                read_verified = bool(expected and expected <= observed)
        result.update(status="complete", case_id=cid, bindings=bindings, agent_read_verified=read_verified)
    except (KeyError, TypeError, ValueError, OSError, UnavailableVisualEvidence) as error:
        result["errors"].append(str(error))
    return result


def load_visual_attachments(root, manifest):
    """Load exact verified bytes immediately before constructing the Judge request."""
    if not isinstance(manifest, dict) or manifest.get("version") != VERSION or manifest.get("status") != "complete":
        raise UnavailableVisualEvidence("Visual manifest incomplete")
    items = manifest.get("bindings")
    if not isinstance(items, list) or not items or len({row.get("id") for row in items}) != len(items):
        raise UnavailableVisualEvidence("Ambiguous image bindings")
    return [(row, _read(root, row["path"], row["sha256"])) for row in items]
