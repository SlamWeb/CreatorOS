"""P03 evidence checks. Never performs a business write or a model request."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

STAGES = ("draft_ready", "draft_edited", "adopted", "series_created")
EDIT_NAME = "reusable-word-scenes"
EDIT_MARKER = "每个单词保留英文释义和中文释义；固定2×2四格，词语后方使用白色底衬，不额外添加总标题。"


def file_hashes(directory):
    if Path(directory).is_symlink():
        raise ValueError("Skill 证据根目录不能是链接。")
    directory = Path(directory).resolve(strict=True)
    hashes = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink() or not path.resolve().is_relative_to(directory):
            raise ValueError("Skill 证据不能包含链接或越界路径。")
        if path.is_file():
            checksum = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    checksum.update(block)
            hashes[path.relative_to(directory).as_posix()] = checksum.hexdigest()
    return hashes


def global_skills_snapshot(project_root):
    # Only hashes are recorded. SDK sessions/logs are intentionally not Skill files.
    codex_root = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    roots = {"codex_skills": codex_root / "skills", "project_skills": Path(project_root) / ".agents/skills"}
    return {name: {"exists": root.exists(), "files": file_hashes(root) if root.exists() else {}}
            for name, root in roots.items()}


def _equal_database(a, b):
    normalize = lambda db: {key: sorted(json.dumps(row, sort_keys=True, default=str) for row in rows)
                            for key, rows in db.items()}
    return normalize(a) == normalize(b)


def chain_checks(evidence):
    """Every gate uses durable stage data, not a browser's self-reported success."""
    from .workbench import _rows_preserved, sdk_terminal
    identifiers = ("draft_gate", "edited_version", "explicit_adoption", "new_series_binding",
                   "reference_chain", "extraction_completed", "approved_skill_frozen", "global_skills_unchanged")
    result = dict.fromkeys(identifiers, False)
    try:
        stages = evidence["artifact_stages"]
        if [row["name"] for row in stages] != list(STAGES):
            return result
        ready, edited, adopted, created = stages
        original_catalog = evidence["before_catalog"]
        original_ids = {row["id"] for row in original_catalog}
        before_db = evidence["before"]["database"]
        jobs = [row["job"] for row in stages]
        job_id = ready["job"]["id"]
        same_job = all(job["id"] == job_id and job["mode"] == "single" for job in jobs)
        result["draft_gate"] = (same_job and all(row["catalog"] == original_catalog
            and _equal_database(row["state"]["database"], before_db)
            and row["job"]["status"] == "ready" and not row["job"].get("saved_skills")
            and not row["job"].get("trials") for row in (ready, edited)))
        text = edited["draft"]["skill_md"]
        expected = evidence["browser"]["edited_skill_md"]
        expected_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        # The editor/API exposes LF text; Windows write_text persists CRLF.
        # Compare either faithful encoding, while all resource copy hashes stay raw.
        raw_encodings = {expected_sha, hashlib.sha256(text.replace("\n", "\r\n").encode("utf-8")).hexdigest()}
        result["edited_version"] = (edited["job"]["revision"] == ready["job"]["revision"] + 1
            and edited["job"]["digest"] != ready["job"]["digest"] and text == expected
            and EDIT_MARKER in text and edited["job"]["skills"][0]["name"] == EDIT_NAME
            and edited["draft"]["files"]["SKILL.md"] in raw_encodings
            and evidence["browser"].get("edited_and_restored") is True
            and {k: v for k, v in ready["draft"]["files"].items() if k != "SKILL.md"}
                == {k: v for k, v in edited["draft"]["files"].items() if k != "SKILL.md"})
        saved = adopted["job"]["saved_skills"]
        registered = adopted["registered"]
        added = [row for row in adopted["catalog"] if row["id"] not in original_ids]
        new_id = registered["id"]
        result["explicit_adoption"] = (same_job and adopted["job"]["status"] == "saved"
            and len(saved) == len(added) == 1 and saved[0]["id"] == added[0]["id"] == new_id
            and adopted["job"]["digest"] == edited["job"]["digest"]
            and adopted["job"]["revision"] == edited["job"]["revision"]
            and registered["digest"] == registered["immutable_digest"] == edited["draft"]["digest"]
            and registered["files"] == registered["immutable_files"] == edited["draft"]["files"]
            and _equal_database(adopted["state"]["database"], before_db)
            and evidence["after_catalog"] == adopted["catalog"]
            and [row for row in adopted["catalog"] if row["id"] in original_ids] == original_catalog)
        new_series = [row for row in created["state"]["database"]["series"]
                      if row["id"] not in {s["id"] for s in before_db["series"]}]
        oracle = evidence["oracle"]
        result["new_series_binding"] = (same_job and len(new_series) == 1
            and new_series[0]["id"] == oracle["target_series_id"] == evidence["browser"]["created_series_id"]
            and new_series[0]["creator_id"] == oracle["creator_id"]
            and new_series[0]["name"] == oracle["series_name"]
            and new_series[0]["audience"] == oracle["audience"]
            and new_series[0]["skill_name"] == oracle["single_skill_id"] == new_id
            and not new_series[0].get("mind_skill_id") and not new_series[0].get("production_skill_id")
            and created["catalog"] == adopted["catalog"]
            and _rows_preserved(before_db, created["state"]["database"], {"series", "write_receipts"}))
        reference = oracle["reference_sha256"]
        result["reference_chain"] = (len(ready["job"]["uploads"]) == 1
            and ready["job"]["uploads"][0]["id"] == reference
            and all(row["reference_copies"]["uploaded"] == reference
                    and row["reference_copies"]["job_reference"] == reference
                    and reference in {v for k, v in row["draft"]["files"].items() if k.startswith("assets/")}
                    for row in stages)
            and reference in {v for k, v in registered["files"].items() if k.startswith("assets/")}
            and evidence["browser"]["reference_sha256"] == reference
            and evidence["browser"].get("reference_loaded") is True)
        prefix = "sdk-files/" + evidence["extraction_prefix"] + job_id + "/"
        result["extraction_completed"] = (same_job and len(evidence["jobs"]) == 1
            and evidence["jobs"][0]["id"] == job_id and evidence["jobs"][0]["status"] == "saved"
            and sdk_terminal(evidence["sdk"], source_prefix=prefix, thread_id=ready["job"]["thread_id"])
            and ready["job"]["thread_id"] != evidence["outputs"].get("thread_id")
            and not any(str(row.get("source", "")).startswith(prefix)
                        for row in evidence["sdk"]["image_generation_events"]))
        outputs = evidence["outputs"]
        inputs = [json.loads(row[field]) if isinstance(row[field], str) else row[field]
                  for table, field in (("content_runs", "input_snapshot_json"), ("content_revisions", "production_input_json"))
                  for row in evidence["after"]["database"][table]]
        result["approved_skill_frozen"] = (len(inputs) == 2
            and all(row.get("skill_name") == new_id and row.get("skill_digest") == registered["digest"] for row in inputs)
            and outputs.get("frozen_skill_resources") == {"single": registered["files"]}
            and outputs.get("skill_digests") == {"single": registered["digest"]}
            and all(image["sha256"] != reference for image in outputs["images"])
            and bool(outputs["images"]))
        guard = evidence["global_skills_guard"]
        result["global_skills_unchanged"] = bool(guard["before"]) and guard["before"] == guard["after"]
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        # Missing stages/evidence never become a vacuous pass.
        pass
    return result
