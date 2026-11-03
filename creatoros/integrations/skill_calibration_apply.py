"""Recoverable, explicit directory publication for a single calibrated Skill."""
from __future__ import annotations

import re
import logging
from pathlib import Path

from .producer_skills import _SKILL_EDIT_LOCK, _digest, _write, freeze_skill
from .skill_extraction import ExtractionError

logger = logging.getLogger(__name__)


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
            saved_skills=[service.catalog.describe(receipt.get("version_skill_id", row.skill_id))], error=None)
    except (OSError, ValueError, KeyError):
        # Losing a UI projection must not roll back a committed shared Skill.
        pass


def _switch_committed(service, row, receipt):
    """Check a persisted receipt only; never invoke a switch during recovery."""
    from creatoros.storage import Series, WriteReceipt
    from .skill_version_service import signature
    request_id = receipt["switch_request_id"]
    skill_id = receipt["version_skill_id"]
    revision = receipt["series_revision"]
    if not isinstance(request_id, str) or not isinstance(skill_id, str) or not isinstance(revision, int):
        raise ValueError("Invalid calibration switch receipt")
    inputs = dict(series_id=row.series_id, skill_id=skill_id, expected_revision=revision,
                  trial_job_id=None, trial_id=None)
    if receipt.get("switch_expected_digest") is not None:
        inputs["expected_digest"] = receipt["switch_expected_digest"]
    if receipt.get("creator_id") is not None:
        inputs["expected_creator_id"] = receipt["creator_id"]
    if receipt.get("account_scope") is not None:
        inputs["expected_account_scope"] = receipt["account_scope"]
    identity = signature({"kind": "switch-skill-version", "request_id": request_id})
    with service.database.session() as session:
        series = session.get(Series, row.series_id)
        saved = session.get(WriteReceipt, identity)
        return bool(series is not None and series.creator_id == row.creator_id and saved is not None
            and saved.operation == "update_composition" and saved.resource_id == row.series_id
            and isinstance(saved.response_json, dict)
            and saved.response_json.get("signature") == signature(inputs)
            and saved.response_json.get("result") == {
                "series_id": row.series_id, "skill_id": skill_id, "revision": revision + 1})


def recover(service, identifier):
    row = service._row(identifier)
    receipt = row.apply_json
    if row.status != "applying":
        return
    if not isinstance(receipt, dict):
        logger.warning("Calibration %s has an unreadable apply record; preserving it", identifier)
        return
    if receipt.get("kind") == "skill_version":
        try:
            if _switch_committed(service, row, receipt):
                _finish(service, row, receipt)
        except (OSError, ValueError, KeyError, TypeError, ExtractionError):
            logger.warning("Calibration %s switch cannot be verified; preserving it", identifier, exc_info=True)
        return
    try:
        # Historical directory-swap records keep their old rollback/reconciliation
        # behavior. New version records above never publish on service startup.
        with _SKILL_EDIT_LOCK:
            directory = _directory(service, row.id, receipt["request_key"])
            source, backup = _working(service, row), directory / "backup"
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
    except (OSError, ValueError, KeyError, TypeError, ExtractionError) as error:
        # Keep applying to prevent a blind second publication over an uncertain state.
        logger.warning("Calibration %s legacy recovery cannot be verified; preserving it", identifier, exc_info=True)


def _activate_version(service, row, receipt):
    """Perform only the explicitly authorized switch, outside the Skill lock."""
    service.versions.switch(row.series_id, receipt["version_skill_id"], receipt["series_revision"],
                            receipt["switch_request_id"], validated_calibration=True,
                            expected_digest=receipt.get("switch_expected_digest"),
                            expected_creator_id=receipt.get("creator_id"),
                            expected_account_scope=receipt.get("account_scope"))
    _finish(service, row, receipt)


def _validate_preflight(service, row, inputs, *, intent=None):
    """Check the complete trial and calculate its candidate identity without writing."""
    from .skill_calibration import signature
    from .skill_authority import account_skill_scope
    from .skill_versions import candidate_identity
    from .skill_workbench import require_draft, require_ready_draft, trial_cards, trial_directory
    from .skill_pair import SkillPair
    from creatoros.delivery import validate_artifact

    expected_digest, trial_id = inputs["expected_digest"], inputs["trial_id"]
    series, _ = service._series(row.series_id, row.skill_id)
    if series.creator_id != row.creator_id:
        raise ExtractionError("栏目账号归属已变化，请重新校准。", 409)
    with service.database.session() as session:
        scope = account_skill_scope(session, row.creator_id)
    if not scope.get("active") or not service.versions.library.can_use(row.creator_id, row.skill_id):
        raise ExtractionError("账号已停用或无权使用此 Skill。", 403)
    job = service.extractions._read(row.job_id)
    base = service.extractions._path("jobs", row.job_id) / "base" / "target"
    service._checked_skill(base)
    if _digest(base) != row.base_skill_digest:
        raise ExtractionError("校准基线文件已变化，请重新校准。", 409)
    if intent is None:
        require_draft(service.extractions, job, expected_digest)
    else:
        current = service._row(row.id)
        if current.status != "applying" or current.apply_json != intent:
            raise ExtractionError("校准意图已变化，请先核对。", 409)
        require_ready_draft(service.extractions, job, expected_digest)
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
    candidate_request_id = "calibration-" + signature(inputs)
    version_id, candidate_key = candidate_identity(service.catalog._record(row.skill_id), candidate_request_id)
    return {"candidate_path": candidate, "candidate_digest": _digest(candidate),
            "trial_artifact_digest": trial["artifact_digest"], "role": role,
            "series_revision": series.revision, "account_scope": scope,
            "source_owner": service.versions.library._source_creator(row.skill_id),
            "candidate_request_id": candidate_request_id, "candidate_request_key": candidate_key,
            "version_skill_id": version_id}


def _save_intent(service, row, receipt):
    """Commit the immutable intent before any candidate or publication exists."""
    from sqlalchemy import text
    from creatoros.storage import Series, SkillCalibration
    from .skill_authority import account_skill_scope
    with service.database.session() as session:
        if service.database.engine.dialect.name == "sqlite":
            session.execute(text("BEGIN IMMEDIATE"))
        current = session.get(SkillCalibration, row.id)
        series = session.get(Series, row.series_id)
        if (current is None or current.status != "draft" or current.job_id != row.job_id
                or current.creator_id != row.creator_id or current.skill_id != row.skill_id
                or current.base_skill_digest != row.base_skill_digest or series is None or not series.is_active
                or series.creator_id != row.creator_id or series.revision != receipt["series_revision"]
                or row.skill_id not in (series.skill_name, series.mind_skill_id, series.production_skill_id)
                or account_skill_scope(session, row.creator_id) != receipt["account_scope"]):
            raise ExtractionError("账号或栏目已变化，请重新核对校准。", 409)
        current.status, current.apply_json, current.last_error = "applying", receipt, None


def _mark_candidate_prepared(service, row, receipt):
    from sqlalchemy import text
    from creatoros.storage import SkillCalibration
    with service.database.session() as session:
        if service.database.engine.dialect.name == "sqlite":
            session.execute(text("BEGIN IMMEDIATE"))
        current = session.get(SkillCalibration, row.id)
        if current is None or current.status != "applying" or current.apply_json != receipt:
            raise ExtractionError("校准记录已变化，请先核对保存状态。", 409)
        current.apply_json = {**receipt, "phase": "prepared"}


def apply(service, identifier, request_id, expected_digest, expected_skill_digest, trial_id):
    from .skill_calibration import signature

    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
        raise ExtractionError("请提供有效的保存请求 ID。")
    inputs = dict(request_id=request_id, expected_digest=expected_digest,
                  expected_skill_digest=expected_skill_digest, trial_id=trial_id)
    request_signature = signature(inputs)
    with service.extractions.lock:
        row = service._row(identifier)
        if row.status == "applying":
            recover(service, identifier)
            row = service._row(identifier)
        previous = row.apply_json if isinstance(row.apply_json, dict) else {}
        if previous.get("request_id") == request_id:
            if previous.get("signature") != request_signature:
                raise ExtractionError("同一保存请求的参数已变化，请使用新的请求 ID。", 409)
            if row.status == "applied":
                return service.get(identifier)
            raise ExtractionError("上次保存结果未确认，请先核对同一记录，勿重复提交。", 409)
        if row.status != "draft":
            raise ExtractionError("这份校准已经保存或仍在核对，不会重复应用。", 409)
        if expected_skill_digest != row.base_skill_digest:
            raise ExtractionError("保存时的 Skill 基线与创建校准时不同。", 409)
        with _SKILL_EDIT_LOCK:
            preflight = _validate_preflight(service, row, inputs)
        receipt = {"kind": "skill_version", "request_id": request_id, "signature": request_signature,
                   "draft_digest": expected_digest, "trial_id": trial_id, "source_skill_id": row.skill_id,
                   "source_digest": row.base_skill_digest, "creator_id": row.creator_id,
                   "phase": "preparing", "switch_request_id": "calibration-" + signature({"id": row.id, **inputs}),
                   **{key: value for key, value in preflight.items() if key != "candidate_path"},
                   "switch_expected_digest": preflight["candidate_digest"]}
        try:
            _save_intent(service, row, receipt)
        except ExtractionError:
            raise
        except Exception as error:
            raise ExtractionError("校准意图保存结果未确认，请先刷新同一记录核对。", 409) from error
        try:
            with _SKILL_EDIT_LOCK:
                current = _validate_preflight(service, row, inputs, intent=receipt)
                if current != preflight:
                    raise ExtractionError("校准来源或账号已变化，请先核对意图。", 409)
                edition = service.catalog.create_skill_candidate_from_directory(
                    row.skill_id, row.base_skill_digest, current["candidate_path"], current["candidate_request_id"])
                if edition["id"] != receipt["version_skill_id"] or edition["digest"] != receipt["candidate_digest"]:
                    raise ExtractionError("候选身份或摘要不一致，请先核对意图。", 409)
            _mark_candidate_prepared(service, row, receipt)
            receipt = {**receipt, "phase": "prepared"}
            _activate_version(service, row, receipt)
        except Exception as error:
            raise ExtractionError("校准保存结果待核实，请刷新同一记录；候选身份和已有文件均保留，勿重复提交。", 409) from error
        return service.get(identifier)
