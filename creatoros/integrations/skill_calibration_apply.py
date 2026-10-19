"""Recoverable, explicit directory publication for a single calibrated Skill."""
from __future__ import annotations

import re
from pathlib import Path

from .producer_skills import _SKILL_EDIT_LOCK, _digest, _write, freeze_skill
from .skill_extraction import ExtractionError


def _directory(service, identifier, request_key):
    if any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)
           for value in (identifier, request_key)):
        raise ExtractionError("校准发布记录标识无效。", 409)
    root = service.catalog.root
    target = root
    for part in ("calibration-applications", identifier, request_key):
        target = target / part
        if service.catalog._is_link_or_reparse(target) or (target.exists() and not target.is_dir()):
            raise ExtractionError("校准备份目录无效，不会写入共享 Skill。", 409)
        if not target.resolve().is_relative_to(root.resolve()):
            raise ExtractionError("校准备份目录越界。", 409)
        target.mkdir(exist_ok=True)
    return target


def _working(service, row):
    root = service.catalog.root / "working"
    target = root / row.skill_id
    if (service.catalog._is_link_or_reparse(root) or service.catalog._is_link_or_reparse(target)
            or target.parent != root or not target.resolve().is_relative_to(root.resolve())):
        raise ExtractionError("共享 Skill 工作目录无效。", 409)
    return target


def _maybe_digest(path):
    if (path.is_dir() and not path.is_symlink() and not getattr(path, "is_junction", lambda: False)()
            and not (getattr(path.lstat(), "st_file_attributes", 0) & 0x400)):
        return _digest(path)
    return None


def _finish(service, row, receipt):
    receipt = {**receipt, "phase": "applied"}
    service._update(row.id, status="applied", apply_json=receipt,
                    applied_digest=receipt["candidate_digest"], last_error=None)
    # The database receipt is authoritative; get() projects saved even if this
    # non-authoritative UI update fails. No later read error can roll it back.
    try:
        service.extractions._update(row.job_id, status="saved", operation=None,
            saved_skills=[service.catalog.describe(row.skill_id)], error=None)
    except (OSError, ValueError, KeyError):
        # Losing a UI projection must not roll back a committed shared Skill.
        pass


def recover(service, identifier):
    row = service._row(identifier)
    receipt = row.apply_json
    if row.status != "applying" or not receipt:
        return
    directory = _directory(service, row.id, receipt["request_key"])
    source, backup = _working(service, row), directory / "backup"
    try:
        current, previous = _maybe_digest(source), _maybe_digest(backup)
        if current == receipt["candidate_digest"] and previous == row.base_skill_digest:
            _finish(service, row, receipt)
            return
        if current is None and previous == row.base_skill_digest:
            freeze_skill(backup, source, row.base_skill_digest)
        elif current not in {row.base_skill_digest, receipt["candidate_digest"]}:
            raise ExtractionError("共享 Skill 出现其他编辑，未自动恢复或覆盖；请检查校准备份。", 409)
        elif current == receipt["candidate_digest"]:
            raise ExtractionError("新 Skill 已出现但旧版本备份缺失，未自动确认；请检查本地记录。", 409)
        service._update(row.id, status="draft", apply_json={**receipt, "phase": "rolled_back"},
                        last_error="上次保存未完成，原 Skill 已保留或恢复；请检查后重新确认。")
    except (OSError, ValueError, ExtractionError) as error:
        # Keep applying to prevent a blind second publication over an uncertain state.
        service._update(row.id, last_error="保存结果尚未核实，未再次覆盖 Skill；请检查校准备份。")
        if isinstance(error, ExtractionError):
            return


def apply(service, identifier, request_id, expected_digest, expected_skill_digest, trial_id):
    from .skill_calibration import signature
    from .skill_workbench import require_draft, trial_cards, trial_directory
    from .skill_pair import SkillPair
    from ..runs.artifacts import validate_artifact

    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
        raise ExtractionError("请提供有效的保存请求 ID。")
    inputs = dict(request_id=request_id, expected_digest=expected_digest,
                  expected_skill_digest=expected_skill_digest, trial_id=trial_id)
    request_signature = signature(inputs)
    with service.extractions.lock, _SKILL_EDIT_LOCK:
        row = service._row(identifier)
        if row.status == "applying":
            recover(service, identifier)
            row = service._row(identifier)
        previous = row.apply_json or {}
        if previous.get("request_id") == request_id:
            if previous.get("signature") != request_signature:
                raise ExtractionError("同一保存请求的参数已变化，请使用新的请求 ID。", 409)
            if row.status == "applied":
                return service.get(identifier)
            raise ExtractionError("上次保存未确认成功，请检查状态后使用新的请求重试。", 409)
        if row.status != "draft":
            raise ExtractionError("这份校准已经保存或仍在核对，不会重复应用。", 409)
        if expected_skill_digest != row.base_skill_digest:
            raise ExtractionError("保存时的 Skill 基线与创建校准时不同。", 409)
        series, _ = service._series(row.series_id, row.skill_id)
        if series.creator_id != row.creator_id:
            raise ExtractionError("栏目账号归属已变化，请重新校准。", 409)
        job = service.extractions._read(row.job_id)
        base = service.extractions._path("jobs", row.job_id) / "base" / "target"
        service._checked_skill(base)
        if _digest(base) != row.base_skill_digest:
            raise ExtractionError("校准基线文件已变化，请重新校准。", 409)
        require_draft(service.extractions, job, expected_digest)
        trial = next((item for item in job.get("trials", []) if item["id"] == trial_id), None)
        if (not trial or trial["status"] != "completed" or trial.get("digest") != expected_digest
                or not trial.get("thread_id") or not trial.get("artifact_digest")):
            raise ExtractionError("请先成功试用当前草稿，再确认保存；旧草稿的试用不能替代。", 409)
        directory = trial_directory(service.extractions, row.job_id, trial_id)
        trial_cards(directory, row.job_id, trial_id, complete=True)
        composition = SkillPair.model_validate(trial["composition"]) if trial.get("composition") else None
        checked = validate_artifact(directory, composition=composition, production_protocol="native-v1")
        if checked.artifact_digest != trial["artifact_digest"]:
            raise ExtractionError("试用文件已变化，请重新检查后试用。", 409)
        role = job["skills"][0]["role"]
        candidate = service.extractions._draft_root(job) / role
        metadata = service._checked_skill(candidate)
        if metadata["name"] != job["calibration_target_name"]:
            raise ExtractionError("校准不能改变原 Skill 身份。", 409)
        companion = job.get("calibration_companion")
        if companion:
            frozen_companion = service.extractions._path("jobs", row.job_id) / "base" / "companion"
            if _digest(frozen_companion) != companion["digest"]:
                raise ExtractionError("冻结搭档文件已变化，不能应用此次试用。", 409)
        source, item, _ = service.catalog._readable_skill_directory(row.skill_id)
        if not item["editable"] or item["role"] != role:
            raise ExtractionError("原 Skill 已只读或角色变化，不能应用。", 409)
        service._checked_skill(source)
        if _digest(source) != row.base_skill_digest:
            raise ExtractionError("共享 Skill 已被其他编辑修改，请重新校准；不会覆盖。", 409)
        current_candidate = _digest(candidate)
        request_key = signature({"request_id": request_id})
        publication = _directory(service, row.id, request_key)
        staging, backup = publication / "candidate", publication / "backup"
        if backup.exists():
            raise ExtractionError("此保存请求已有备份记录，请使用新的请求 ID。", 409)
        freeze_skill(candidate, staging, current_candidate)
        receipt = {"request_id": request_id, "signature": request_signature, "request_key": request_key,
                   "draft_digest": expected_digest, "trial_id": trial_id,
                   "candidate_digest": current_candidate, "phase": "prepared"}
        _write(publication / "receipt.json", receipt)
        service._update(row.id, status="applying", apply_json=receipt, last_error=None)
        try:
            if _digest(source) != row.base_skill_digest or _digest(staging) != current_candidate:
                raise ExtractionError("发布前文件已变化，不会覆盖。", 409)
            # Old bytes stay in backup. Existing Runs use their own frozen snapshots.
            source.rename(backup)
            staging.rename(source)
            if _digest(source) != current_candidate or _digest(backup) != row.base_skill_digest:
                raise ExtractionError("保存后的文件摘要不一致。", 409)
            _finish(service, row, receipt)
        except Exception as error:
            # A DB driver may report an error after the commit reached disk.
            committed = service._row(row.id)
            if (committed.status == "applied" and committed.apply_json
                    and committed.apply_json.get("signature") == request_signature):
                raise ExtractionError("Skill 已保存，页面读取暂时失败；请刷新查看，不要重复提交。", 409) from error
            try:
                if _maybe_digest(backup) == row.base_skill_digest:
                    current = _maybe_digest(source)
                    if current == current_candidate:
                        source.rename(publication / "failed-candidate")
                    elif current is not None:
                        raise ExtractionError("保存中发生其他编辑，未自动覆盖。", 409)
                    freeze_skill(backup, source, row.base_skill_digest)
                service._update(row.id, status="draft", apply_json={**receipt, "phase": "rolled_back"},
                                applied_digest=None, last_error="保存未完成，原 Skill 与旧产物已保留；请检查后重试。")
            except Exception:
                # Do not claim rollback while publication state is uncertain.
                service._update(row.id, last_error="保存结果待核实，已阻止再次覆盖；请检查校准备份。")
            if isinstance(error, ExtractionError):
                raise
            raise ExtractionError("保存未完成；旧版本与校准备份保留，不会重新生产。", 409) from error
        # Rendering/projection is outside the publication rollback boundary.
        return service.get(identifier)
