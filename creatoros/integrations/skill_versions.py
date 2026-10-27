"""Immutable library versions; candidates are complete, independently editable copies.

Registry IDs remain production identities, so old Runs and queued snapshots keep
their original files.  This module never selects a version for a Series: that
separate, explicit human operation checks the selected version and column
revision. A production trial is optional, not a publishing prerequisite.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .atomic_file import atomic_write_text


def version_metadata(data: dict) -> dict:
    status = data.get("version_status", "released")
    number = data.get("version_number", 1)
    family = data.get("family_id", data["id"])
    parent = data.get("parent_id")
    if (status not in {"candidate", "released"} or not isinstance(number, int)
            or isinstance(number, bool) or number < 1 or not isinstance(family, str)
            or not re.fullmatch(r"[a-z0-9-]+--[a-f0-9]{16}", family)
            or (parent is not None and (not isinstance(parent, str)
                or not re.fullmatch(r"[a-z0-9-]+--[a-f0-9]{16}", parent)))):
        raise ValueError("Skill 版本登记无效。")
    return {"family_id": family, "parent_id": parent, "version_number": number,
            "version_status": status, "created_at": data.get("created_at")}


def _managed(catalog, *parts: str) -> Path:
    path = catalog.root
    for part in parts:
        path = path / part
        if catalog._is_link_or_reparse(path) or not path.resolve().is_relative_to(catalog.root):
            raise ValueError("Skill 版本目录路径无效。")
    return path


def _write_record(catalog, data: dict) -> None:
    path = _managed(catalog, "registry", data["id"] + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def _records(catalog):
    registry = _managed(catalog, "registry")
    for path in sorted(registry.glob("*.json")):
        if catalog._is_link_or_reparse(path) or not path.is_file():
            raise ValueError("Skill 版本登记路径无效。")
        yield catalog._record(path.stem)


def list_versions(catalog, skill_id: str) -> dict:
    source = catalog.describe(skill_id)
    family = source["family_id"]
    items = [catalog.describe(data["id"]) for data in _records(catalog)
             if version_metadata(data)["family_id"] == family]
    items.sort(key=lambda item: (item["version_number"], item["id"]), reverse=True)
    return {"family_id": family, "items": items}


def _check_digest(catalog, skill_id: str, expected_digest: str) -> dict:
    from .producer_skills import SkillDigestConflict
    if not isinstance(expected_digest, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_digest):
        raise ValueError("expected_digest 必须是文件列表返回的 Skill digest。")
    current = catalog.describe(skill_id)
    if current["digest"] != expected_digest:
        raise SkillDigestConflict(current["digest"])
    return current


def _draft_directory(catalog, source_directory: Path) -> tuple[Path, str]:
    from .producer_skills import MAX_LOCAL_SKILL_BYTES, _digest
    from creatoros.skills.loader import SkillLoader
    source = Path(source_directory)
    if catalog._is_link_or_reparse(source) or not source.is_dir():
        raise ValueError("候选来源必须是普通 Skill 目录。")
    source = source.resolve(strict=True)
    total = count = 0
    for path in source.rglob("*"):
        count += 1
        if (count > 2_000 or catalog._is_link_or_reparse(path)
                or not path.resolve().is_relative_to(source)):
            raise ValueError("候选目录包含链接、越界或过多文件。")
        if path.is_file():
            total += path.stat().st_size
        elif not path.is_dir():
            raise ValueError("候选目录只允许普通文件和目录。")
        if total > MAX_LOCAL_SKILL_BYTES:
            raise OverflowError("候选目录最多 96 MiB。")
    if not any(item.path == source / "SKILL.md" for item in SkillLoader([source]).discover()):
        raise ValueError("候选目录缺少有效 SKILL.md。")
    return source, _digest(source)


def create_candidate(catalog, skill_id: str, expected_digest: str, request_id: str,
                     *, source_directory: Path | None = None, creator_id: str | None = None) -> dict:
    from .producer_skills import _digest, freeze_skill
    if skill_id == catalog.BUILTIN_ID:
        raise PermissionError("内置 Skill 只读。")
    if (not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 200
            or any(ord(char) < 32 for char in request_id)):
        raise ValueError("请提供有效的候选请求标识。")
    # Replays are identified before checking current source bytes: the same
    # request always denotes the same full-copy input, not a newer edit.
    source_record = catalog._record(skill_id)
    source_id = source_record["id"]
    draft, draft_digest = (_draft_directory(catalog, source_directory) if source_directory is not None
                           else (None, expected_digest))
    identity = f"{source_id}\0{request_id}" if creator_id is None else f"{source_id}\0{request_id}\0{creator_id}"
    request_key = hashlib.sha256(identity.encode()).hexdigest()
    candidate_id = source_record["name"][:100].strip("-") + "--" + request_key[:16]
    existing_path = _managed(catalog, "registry", candidate_id + ".json")
    if existing_path.exists():
        existing = catalog._record(candidate_id)
        if (existing.get("candidate_request_key") != request_key
                or existing.get("parent_id") != source_id
                or (creator_id is not None and existing.get("creator_id") != creator_id)
                or existing.get("parent_digest") != expected_digest
                or existing.get("candidate_source_digest", expected_digest) != draft_digest):
            raise ValueError("同一候选请求的输入已改变，请使用新的请求标识。")
        return catalog.describe(candidate_id)
    source = _check_digest(catalog, source_id, expected_digest)
    family = source["family_id"]
    now = datetime.now(timezone.utc).isoformat()
    # A legacy working copy may differ from its imported original. Preserve
    # that exact v1 separately rather than replacing historical import bytes.
    if source["version_status"] == "released" and not source_record.get("release_digest"):
        baseline = _managed(catalog, "version-baselines", source_id, expected_digest)
        freeze_skill(Path(source["local_path"]), baseline, expected_digest)
        source_record = {**source_record, **version_metadata(source_record),
                         "release_digest": expected_digest,
                         "created_at": source_record.get("created_at") or now}
        _write_record(catalog, source_record)
    number = max((version_metadata(data)["version_number"] for data in _records(catalog)
                  if version_metadata(data)["family_id"] == family), default=1) + 1
    original = _managed(catalog, "versions", candidate_id)
    working = _managed(catalog, "working", candidate_id)
    freeze_skill(draft or Path(source["local_path"]), original, draft_digest)
    freeze_skill(original, working, draft_digest)
    if _digest(working) != draft_digest:
        raise ValueError("候选文件与请求输入不一致。")
    record = {**source_record, "id": candidate_id, "family_id": family,
              "parent_id": source_id, "parent_digest": expected_digest,
              "version_number": number, "version_status": "candidate",
              "created_at": now, "digest": draft_digest, "candidate_source_digest": draft_digest,
              "candidate_request_key": request_key, "source_kind": "version"}
    if creator_id is not None:
        record["creator_id"] = creator_id
        record.pop("source_creator_id", None)
    record.pop("release_digest", None)
    _write_record(catalog, record)  # visibility only after both complete copies exist
    return catalog.describe(candidate_id)


def create_candidate_from_directory(catalog, skill_id: str, expected_digest: str,
                                     source_directory: Path, request_id: str) -> dict:
    """Import a host-owned trial draft as a full version, never as a shared edit."""
    return create_candidate(catalog, skill_id, expected_digest, request_id,
                            source_directory=source_directory)


def publish_candidate(catalog, skill_id: str, expected_digest: str) -> dict:
    from .producer_skills import _digest, freeze_skill
    source = _check_digest(catalog, skill_id, expected_digest)
    record = catalog._record(skill_id)
    if source["version_status"] == "released":
        # Publishing the identical already-released version is idempotent.
        if record.get("release_digest") != expected_digest:
            raise ValueError("该 Skill 不是待发布候选。")
        return source
    original = _managed(catalog, "versions", source["id"])
    if original.exists() and _digest(original) != expected_digest:
        # Keep the candidate's starting material too. A retry after either
        # rename can finish from the still-complete working copy.
        original_digest = _digest(original)
        seed = _managed(catalog, "candidate-seeds", source["id"], original_digest + "-" + uuid4().hex)
        seed.parent.mkdir(parents=True, exist_ok=True)
        original.rename(seed)
    freeze_skill(Path(source["local_path"]), original, expected_digest)
    record = {**record, "digest": expected_digest, "release_digest": expected_digest,
              "version_status": "released", "released_at": datetime.now(timezone.utc).isoformat()}
    _write_record(catalog, record)
    return catalog.describe(source["id"])


def edit_target(catalog, skill_id: str, expected_digest: str, operation: dict,
                creator_id: str | None = None) -> tuple[str, bool]:
    """Direct legacy editor calls create candidates too; no hidden shared writes."""
    source = _check_digest(catalog, skill_id, expected_digest)
    if source["version_status"] == "candidate":
        return source["id"], False
    operation_key = hashlib.sha256(json.dumps(operation, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    candidate = create_candidate(catalog, source["id"], expected_digest, "edit-" + operation_key,
                                 creator_id=creator_id)
    return candidate["id"], (candidate["digest"] != expected_digest
                              or candidate["version_status"] == "released")


def edit_result(catalog, skill_id: str, result: dict) -> dict:
    described = catalog.describe(skill_id)
    return {**result, "skill_id": skill_id, **version_metadata(described)}


def baseline_original(catalog, skill_id: str, expected_digest: str) -> Path | None:
    from .producer_skills import _digest
    if not re.fullmatch(r"[a-f0-9]{64}", expected_digest):
        return None
    path = _managed(catalog, "version-baselines", skill_id, expected_digest)
    if path.is_dir() and _digest(path) == expected_digest:
        return path
    return None
