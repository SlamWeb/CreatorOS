"""Read registered Skill evidence once from its authoritative working copy."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

VERSION = "skill-working-inventory-v1"


def _linked(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def collect_skill_evidence(root: Path, catalog_root: Path, snapshot_files: dict) -> dict:
    """Read only; never call catalog.locate(), which can recreate missing files."""
    root, catalog_root = Path(root).resolve(), Path(catalog_root).absolute()
    versions, texts, paths, manifests, errors = [], {}, {}, {}, []

    def checked(path: Path) -> bytes:
        path = path.absolute()
        if (not path.is_relative_to(root) or not path.resolve().is_relative_to(root)
                or any(_linked(part)
                       for part in (path, *path.parents) if part != root)):
            raise ValueError("Skill evidence path escaped or is linked")
        raw = path.read_bytes()
        key = path.relative_to(root).as_posix()
        if snapshot_files.get(key) != hashlib.sha256(raw).hexdigest():
            raise ValueError("Skill evidence differs from the file snapshot")
        return raw

    registry = catalog_root / "registry"
    if not registry.is_dir() or _linked(registry):
        return {"skill_evidence_version": VERSION, "skills": [], "skill_texts": {}, "skill_paths": {}, "skill_manifests": {},
                "skill_inventory_errors": ["Skill registry is missing or linked"]}
    for record in sorted(registry.glob("*.json")):
        identifier = record.stem
        try:
            item = json.loads(checked(record))
            if not isinstance(item, dict) or item.get("id") != identifier:
                raise ValueError("Registered Skill identity mismatch")
            item.pop("content", None)
            working = catalog_root / "working" / identifier
            content = checked(working / "SKILL.md").decode("utf-8")
            manifest = {}
            def walk_error(error):
                raise error
            for directory, dirs, names in os.walk(working, followlinks=False, onerror=walk_error):
                for name in dirs + names:
                    path = Path(directory) / name
                    if _linked(path):
                        raise ValueError("Linked Skill working entry")
                    if name in names:
                        manifest[path.relative_to(working).as_posix()] = hashlib.sha256(checked(path)).hexdigest()
            item["content"] = content
            versions.append(item)
            texts[identifier] = content
            paths[identifier] = working.relative_to(root).as_posix()
            manifests[identifier] = manifest
        except (OSError, ValueError, UnicodeError, TypeError) as error:
            errors.append({"id": identifier, "error": str(error)})
    return {"skill_evidence_version": VERSION, "skills": versions, "skill_texts": texts,
            "skill_paths": paths, "skill_manifests": manifests, "skill_inventory_errors": errors}


def collect_skill_references(database: dict, creator_id: str, series_id: str) -> dict:
    """Project exact stored references without describe()/locate() file recovery."""
    def parsed(value):
        return json.loads(value) if isinstance(value, str) else value

    columns = [row for row in database["series"] if row.get("creator_id") == creator_id]
    def bindings(row):
        return [row[key] for key in ("skill_name", "mind_skill_id", "production_skill_id") if row.get(key)]

    library = next((row for row in database["creator_skill_refs"] if row["creator_id"] == creator_id), {})
    ids = list(dict.fromkeys([*parsed(library.get("skill_ids_json", [])),
                              *(value for row in columns for value in bindings(row))]))
    column = next((row for row in columns if row["id"] == series_id), {})
    pool = next((row for row in database["series_skill_refs"] if row["series_id"] == series_id), {})
    pool_ids = list(dict.fromkeys(parsed(pool["skill_ids_json"]) if pool else bindings(column)))
    return {"library": {"creator_id": creator_id, "items": [{"id": value} for value in ids],
                        "version": library.get("version", 0)},
            "pool": {"series_id": series_id, "items": [{"id": value} for value in pool_ids],
                     "version": pool.get("version", 0), "defaults": parsed(pool.get("defaults_json", {}))}}
