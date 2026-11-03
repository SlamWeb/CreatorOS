"""Immutable Skill editions, clean trials, and explicit per-column activation."""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from time import sleep

from sqlalchemy import select, text, update

from ..storage import Series, SkillSwitchIntent, WriteReceipt
from .producer_skills import (_SKILL_EDIT_LOCK, SkillWriteOutcomeUnknown, _digest, _write,
                              freeze_skill, inherit_copy_permissions)
from .skill_extraction import ExtractionError, now
from .skill_authority import account_skill_scope


def signature(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class SkillSwitchOutcomeUnknown(SkillWriteOutcomeUnknown):
    """File publication or the final series transaction must be inspected."""

    def __init__(self, series_id, skill_id, request_id):
        super().__init__(skill_id)
        self.series_id, self.request_id = series_id, request_id
        self.args = ("切版结果待核对；候选可能已保存，栏目可能已切换。请先查看同一请求的状态。",)

    def response(self):
        return {**super().response(), "series_id": self.series_id, "request_id": self.request_id}


class SkillVersionService:
    def __init__(self, database, extractions):
        self.database, self.extractions = database, extractions
        self.catalog = extractions.catalog
        from .skill_library import SkillLibraryService
        self.library = SkillLibraryService(database, self.catalog)

    _account_scope = staticmethod(account_skill_scope)

    @staticmethod
    def _bound(series):
        return ({"single": series.skill_name} if series.skill_name else
                {"mind": series.mind_skill_id, "production": series.production_skill_id})

    def _binding(self, series, skill_id):
        matches = [(role, current) for role, current in self._bound(series).items()
                   if self.catalog.same_skill_family(current, skill_id)]
        if len(matches) != 1:
            raise ExtractionError("请选择绑定了这份 Skill 的栏目。", 409)
        return matches[0]

    def history(self, skill_id):
        result = self.catalog.list_skill_versions(skill_id)
        for item in result["items"]:
            pointer = self.catalog.root / "version-trials" / f"{item['id']}.json"
            if (pointer.is_file() and not self.catalog._is_link_or_reparse(pointer.parent)
                    and not self.catalog._is_link_or_reparse(pointer)):
                try:
                    identifier = json.loads(pointer.read_text(encoding="utf-8"))["job_id"]
                    if isinstance(identifier, str) and len(identifier) == 64 and all(c in "0123456789abcdef" for c in identifier):
                        item["version_trial_job_id"] = identifier
                except (OSError, ValueError, KeyError, TypeError):
                    item["version_trial_error"] = "试用索引不可读，版本文件仍保留。"
        with self.database.session() as session:
            rows = list(session.scalars(select(Series).where(Series.is_active.is_(True))))
            result["bindings"] = []
            for series in rows:
                for role, bound in self._bound(series).items():
                    try:
                        matches = bound and self.catalog.same_skill_family(bound, skill_id)
                    except (ValueError, OSError, KeyError):
                        # An unrelated old column with a missing Skill cannot break healthy histories.
                        matches = False
                    if matches:
                        result["bindings"].append(dict(series_id=series.id, series_name=series.name,
                            role=role, skill_id=bound, revision=series.revision))
        return result

    def _publish_trial_directory(self, staging, directory, job_id, inputs):
        """Publish once; retry only bounded Windows sharing/access failures."""
        def accept_existing():
            files = (directory, directory / "job.json", directory / "request.json")
            if any(self.catalog._is_link_or_reparse(path) for path in files):
                raise ExtractionError("试用目录不安全，旧文件保留，未覆盖。", 409)
            try:
                job = json.loads((directory / "job.json").read_text(encoding="utf-8"))
                request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
                matches = (job.get("id") == job_id and job.get("input_digest") == signature(inputs)
                           and request == inputs)
            except (OSError, ValueError, TypeError, AttributeError):
                matches = False
            if not matches:
                raise ExtractionError("已有试用目录与本次完整输入不一致，旧文件保留，未覆盖。", 409)
        delays = (.05, .1, .2)
        for attempt in range(len(delays) + 1):
            if directory.exists() or self.catalog._is_link_or_reparse(directory):
                accept_existing()
                return
            try:
                staging.rename(directory)
                return
            except OSError as error:
                # The rename might have committed before the caller observed
                # an error. Reconcile exact input on the next loop, never
                # overwrite an existing directory or resubmit a claimed trial.
                if directory.exists():
                    accept_existing()
                    return
                if getattr(error, "winerror", None) not in {5, 32, 33} or attempt == len(delays):
                    raise
                sleep(delays[attempt])
        raise ExtractionError("试用目录发布结果待核对，旧文件保留，未提交新生产。", 409)

    def trial(self, skill_id, expected_digest, request_id, topic, series_id=None, *, task_references=None):
        service = self.extractions
        inputs = dict(skill_id=skill_id, expected_digest=expected_digest, topic=topic, series_id=series_id)
        job_id = signature({"kind": "skill-version-trial", "request_id": request_id})
        if task_references is not None:
            from .topic_references import TaskReferences, TopicReferenceStore
            task_references = TaskReferences.model_validate(task_references)
            if task_references.series_id != job_id:
                raise ExtractionError("试用参考不属于当前请求。", 409)
            TopicReferenceStore(self.database).verify(task_references)
            inputs["task_references"] = task_references.model_dump(mode="json")
        with service.lock, _SKILL_EDIT_LOCK:
            directory = service._path("jobs", job_id)
            if (directory / "job.json").is_file():
                job = service._read(job_id)
                if job["input_digest"] != signature(inputs):
                    raise ExtractionError("同一试用请求的输入已变化，请重新提交。", 409)
            else:
                if service.closed:
                    raise ExtractionError("服务正在关闭，请稍后试用。", 409)
                item = self.catalog.describe(skill_id)
                if item["version_status"] != "candidate" or item["digest"] != expected_digest:
                    raise ExtractionError("候选版本已变化，请刷新后试用。", 409)
                role = item["role"]
                if role not in {"mind", "production", "legacy_end_to_end"}:
                    raise ExtractionError("请先确定 Skill 的内容或呈现角色。")
                companion, bound_id, series_revision = None, None, None
                if series_id:
                    with self.database.session() as session:
                        series = session.get(Series, series_id)
                        if series is None or not series.is_active:
                            raise ExtractionError("栏目不存在或已移除。", 404)
                        binding_role, bound_id = self._binding(series, skill_id)
                        series_revision = series.revision
                        companion_id = next((value for key, value in self._bound(series).items()
                                             if key != binding_role), None)
                    if companion_id:
                        companion = self.catalog.describe(companion_id)
                if role == "mind" and companion is None:
                    raise ExtractionError("请先选择栏目，用已绑定的呈现 Skill 一起试用。", 409)
                mode = {"mind": "mind", "production": "visual", "legacy_end_to_end": "single"}[role]
                jobs_root = directory.parent
                jobs_root.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(prefix=".skill-version-", dir=jobs_root) as temporary:
                    staging = Path(temporary)
                    draft = staging / "versions" / "v001"
                    freeze_skill(self.catalog.locate(skill_id), draft / role, expected_digest)
                    if companion:
                        freeze_skill(self.catalog.locate(companion["id"]), staging / "base" / "companion",
                                     companion["digest"])
                    job = dict(id=job_id, request_id=request_id, task_kind="skill_version",
                        input_digest=signature(inputs), mode=mode, instruction="", source_text="",
                        status="ready", operation=None, created_at=now(), updated_at=now(), thread_id=None,
                        error=None, note="候选试用；不会修改栏目。", revision=1, digest=_digest(draft),
                        draft_directory="versions/v001", saved_skills=[], cancel_requested=False,
                        trials=[], actions={}, uploads=[], skills=[dict(name=item["name"], role=role,
                            skill_md=(draft / role / "SKILL.md").read_text(encoding="utf-8"),
                            output_kind="text" if role == "mind" else "image-carousel")],
                        version_candidate_id=skill_id, version_candidate_digest=expected_digest,
                        version_series_id=series_id, version_bound_id=bound_id,
                        version_series_revision=series_revision)
                    if companion:
                        job["calibration_companion"] = {key: companion[key] for key in ("id", "name", "role", "digest")}
                    if task_references is not None:
                        job["task_references"] = task_references.model_dump(mode="json")
                    (staging / "references").mkdir()
                    _write(staging / "request.json", inputs)
                    _write(staging / "job.json", job)
                    inherit_copy_permissions(staging)
                    self._publish_trial_directory(staging, directory, job_id, inputs)
            # Same authorization/request can recover a pre-queue crash, never repeat a claimed trial.
            result = service.trial(job_id, request_id, job["digest"], topic)
            pointer = self.catalog.root / "version-trials"
            if self.catalog._is_link_or_reparse(pointer):
                raise ExtractionError("试用索引目录无效；任务已保留。", 409)
            _write(pointer / f"{skill_id}.json", {"job_id": job_id})
            return result

    def checked_trial(self, skill_id, expected_digest, job_id, trial_id, series=None):
        from .skill_workbench import trial_cards, trial_directory
        from .skill_pair import SkillPair
        from creatoros.delivery import validate_artifact
        job = self.extractions._read(job_id)
        if (job.get("task_kind") != "skill_version" or job.get("version_candidate_id") != skill_id
                or job.get("version_candidate_digest") != expected_digest):
            raise ExtractionError("试用不属于当前候选版本，请重新试用。", 409)
        role = job["skills"][0]["role"]
        if _digest(self.extractions._draft_root(job) / role) != expected_digest:
            raise ExtractionError("试用输入已变化，请重新试用。", 409)
        trial = next((item for item in job.get("trials", []) if item["id"] == trial_id), None)
        if (not trial or trial["status"] != "completed" or trial["digest"] != job["digest"]
                or not trial.get("thread_id") or not trial.get("artifact_digest")):
            raise ExtractionError("这次试用未成功完成或缺少产物凭据，不能作为有效试用记录。", 409)
        directory = trial_directory(self.extractions, job_id, trial_id)
        trial_cards(directory, job_id, trial_id, complete=True)
        composition = SkillPair.model_validate(trial["composition"]) if trial.get("composition") else None
        checked = validate_artifact(directory, composition=composition, production_protocol="native-v1")
        if checked.artifact_digest != trial["artifact_digest"]:
            raise ExtractionError("试用产物已变化，请重新试用。", 409)
        if series is not None:
            _, current = self._binding(series, skill_id)
            if (job.get("version_series_id") != series.id or job.get("version_bound_id") != current
                    or job.get("version_series_revision") != series.revision):
                raise ExtractionError("栏目配置在试用后已变化，请重新试用。", 409)
            companion = job.get("calibration_companion")
            if companion:
                current_item = self.catalog.describe(companion["id"])
                if (companion["id"] not in self._bound(series).values()
                        or current_item["digest"] != companion["digest"]):
                    raise ExtractionError("试用搭档已变化，请重新试用。", 409)
        return job

    def publish(self, skill_id, expected_digest, trial_job_id=None, trial_id=None, request_id=None):
        with self.extractions.lock, _SKILL_EDIT_LOCK:
            item = self.catalog.describe(skill_id)
            if item["digest"] != expected_digest:
                raise ExtractionError("候选版本已变化，请刷新后确认。", 409)
            # Publishing is the user's decision to keep this exact full-folder
            # version, not a claim that a model trial or aesthetic judge passed.
            # Legacy trial fields remain accepted but do not gate availability.
            return self.catalog.publish_skill_candidate(skill_id, expected_digest)

    def switch(self, series_id, skill_id, expected_revision, request_id, trial_job_id=None, trial_id=None,
               *, validated_calibration=False, expected_digest=None, expected_creator_id=None,
               expected_account_scope=None):
        inputs = dict(series_id=series_id, skill_id=skill_id, expected_revision=expected_revision,
                      trial_job_id=trial_job_id, trial_id=trial_id)
        if expected_digest is not None:
            inputs["expected_digest"] = expected_digest
        if expected_creator_id is not None:
            inputs["expected_creator_id"] = expected_creator_id
        if expected_account_scope is not None:
            inputs["expected_account_scope"] = expected_account_scope
        receipt_id = signature({"kind": "switch-skill-version", "request_id": request_id})
        input_signature = signature(inputs)
        fields = {"single": "skill_name", "mind": "mind_skill_id", "production": "production_skill_id"}

        def existing_receipt(session):
            receipt = session.get(WriteReceipt, receipt_id)
            if receipt:
                if (receipt.operation != "update_composition" or receipt.resource_id != series_id
                        or receipt.response_json.get("signature") != input_signature):
                    raise ExtractionError("同一切换请求的输入已变化。", 409)
                return receipt.response_json["result"]
            return None

        def settled_or(error):
            # A different caller may have committed this exact request after
            # the first read. Never rely on that earlier session's snapshot.
            with self.database.session() as fresh:
                previous = existing_receipt(fresh)
            if previous is not None:
                return previous
            raise error

        preflight_error = None
        with self.database.session() as session:
            previous = existing_receipt(session)
            if previous is not None:
                return previous
            intent = session.get(SkillSwitchIntent, receipt_id)
            if intent and (intent.signature != input_signature or intent.series_id != series_id):
                raise ExtractionError("同一切换请求的输入已变化。", 409)
            series = session.get(Series, series_id)
            if series is None or not series.is_active:
                preflight_error = ExtractionError("栏目不存在或已移除。", 404)
            elif series.revision != expected_revision:
                preflight_error = ExtractionError("栏目配置已变化，请刷新后确认。", 409)
            else:
                scope = self._account_scope(session, series.creator_id)
                if ((expected_creator_id is not None and series.creator_id != expected_creator_id)
                        or (expected_account_scope is not None and scope != expected_account_scope)):
                    preflight_error = SkillSwitchOutcomeUnknown(series_id, skill_id, request_id)
        if preflight_error is not None:
            return settled_or(preflight_error)
        if intent is None:
            # Resolve the file-backed family before taking a SQLite write lock.
            with _SKILL_EDIT_LOCK:
                role, previous_skill_id = self._binding(series, skill_id)
                item = self.catalog.describe(skill_id)
                if expected_digest is not None and item["digest"] != expected_digest:
                    raise ExtractionError("Skill版本已变化，请刷新后确认。", 409)
                frozen_digest = item["digest"]
                source_scope = {"family_id": item["family_id"], "owner": self.library._source_creator(skill_id)}
            if series.creator_id and (not scope["active"] or not self.library.can_use(series.creator_id, skill_id)):
                raise ExtractionError("当前账号不能使用此 Skill 版本。", 403)
            # Write the intent before publication. No Skill lock or file copy is
            # held during this short database transaction.
            with self.database.session() as session:
                if self.database.engine.dialect.name == "sqlite":
                    session.execute(text("BEGIN IMMEDIATE"))
                previous = existing_receipt(session)
                if previous is not None:
                    return previous
                intent = session.get(SkillSwitchIntent, receipt_id)
                current = session.get(Series, series_id)
                if (current is None or not current.is_active or current.revision != expected_revision
                        or current.creator_id != series.creator_id
                        or getattr(current, fields[role]) != previous_skill_id
                        or self._account_scope(session, current.creator_id) != scope):
                    raise ExtractionError("栏目配置已变化，请刷新后确认。", 409)
                if intent is None:
                    intent = SkillSwitchIntent(id=receipt_id, signature=input_signature, series_id=series_id,
                        creator_id=current.creator_id, skill_id=skill_id, expected_revision=expected_revision,
                        expected_digest=frozen_digest, previous_skill_id=previous_skill_id, role=role,
                        scope_json={"account": scope, "source": source_scope}, phase="prepared")
                    session.add(intent)
                    session.flush()
                elif intent.signature != input_signature or intent.series_id != series_id:
                    raise ExtractionError("同一切换请求的输入已变化。", 409)

        # A change since the intent may already have raced an earlier publish.
        # Inspect the retained intent instead of claiming there was no effect.
        with self.database.session() as session:
            current = session.get(Series, series_id)
            scope_changed = (current is None or not current.is_active or current.creator_id != intent.creator_id
                    or current.revision != intent.expected_revision
                    or getattr(current, fields[intent.role]) != intent.previous_skill_id
                    or self._account_scope(session, intent.creator_id) != intent.scope_json["account"])
        if scope_changed:
            return settled_or(SkillSwitchOutcomeUnknown(series_id, skill_id, request_id))
        if intent.creator_id and not self.library.can_use(intent.creator_id, skill_id):
            return settled_or(SkillSwitchOutcomeUnknown(series_id, skill_id, request_id))

        # An explicit same-request replay first checks the exact intent and
        # file state. A matching released version is never published again.
        with _SKILL_EDIT_LOCK:
            item = self.catalog.describe(skill_id)
            source_scope = {"family_id": item["family_id"], "owner": self.library._source_creator(skill_id)}
            if source_scope != intent.scope_json["source"]:
                raise SkillSwitchOutcomeUnknown(series_id, skill_id, request_id)
            if item["digest"] != intent.expected_digest:
                raise ExtractionError("Skill版本已变化，请刷新后确认。", 409)
            if item["version_status"] == "candidate":
                try:
                    self.catalog.publish_skill_candidate(skill_id, intent.expected_digest)
                except Exception as error:
                    raise SkillSwitchOutcomeUnknown(series_id, skill_id, request_id) from error
            elif item["version_status"] != "released":
                raise ExtractionError("Skill版本不可用于切换。", 409)

        try:
            with self.database.session() as session:
                if self.database.engine.dialect.name == "sqlite":
                    session.execute(text("BEGIN IMMEDIATE"))
                previous = existing_receipt(session)
                if previous is not None:
                    return previous
                saved = session.get(SkillSwitchIntent, receipt_id)
                current = session.get(Series, series_id)
                if (saved is None or saved.signature != input_signature or current is None or not current.is_active
                        or current.creator_id != saved.creator_id or current.revision != saved.expected_revision
                        or getattr(current, fields[saved.role]) != saved.previous_skill_id
                        or self._account_scope(session, saved.creator_id) != saved.scope_json["account"]):
                    raise SkillSwitchOutcomeUnknown(series_id, skill_id, request_id)
                field = fields[saved.role]
                changed = session.execute(update(Series).where(Series.id == series_id,
                    Series.revision == expected_revision, Series.creator_id == saved.creator_id)
                    .values(**{field: skill_id, "revision": expected_revision + 1}))
                if changed.rowcount != 1:
                    raise SkillSwitchOutcomeUnknown(series_id, skill_id, request_id)
                result = dict(series_id=series_id, skill_id=skill_id, revision=expected_revision + 1)
                session.add(WriteReceipt(request_id=receipt_id, operation="update_composition", resource_id=series_id,
                    origin="web", response_json={"kind": "skill_version", "signature": input_signature,
                        "previous_skill_id": saved.previous_skill_id, "result": result}))
                saved.phase = "committed"
            return result
        except SkillSwitchOutcomeUnknown:
            raise
        except Exception as error:
            raise SkillSwitchOutcomeUnknown(series_id, skill_id, request_id) from error

    def inspect_switch(self, series_id, request_id, *, creator_id=None):
        """Read a switch's DB receipt and exact file state without resuming it."""
        identity = signature({"kind": "switch-skill-version", "request_id": request_id})
        with self.database.session() as session:
            series = session.get(Series, series_id)
            intent = session.get(SkillSwitchIntent, identity)
            receipt = session.get(WriteReceipt, identity)
            if (series is None or (creator_id is not None and series.creator_id != creator_id)
                    or (intent is not None and (intent.series_id != series_id
                        or (creator_id is not None and intent.creator_id != creator_id)))):
                raise PermissionError("当前账号不能查看此切版请求。")
            if intent is None:
                raise ExtractionError("切版请求不存在。", 404)
            role_field = {"single": "skill_name", "mind": "mind_skill_id", "production": "production_skill_id"}[intent.role]
            current_id, current_revision = getattr(series, role_field), series.revision
            scope = self._account_scope(session, intent.creator_id)
        if receipt is not None and receipt.response_json.get("signature") == intent.signature:
            state = "committed"
        elif current_id != intent.previous_skill_id or current_revision != intent.expected_revision:
            state = "binding_changed"
        elif scope != intent.scope_json["account"]:
            state = "authorization_changed"
        else:
            try:
                with _SKILL_EDIT_LOCK:
                    item = self.catalog.describe(intent.skill_id)
                    source = {"family_id": item["family_id"], "owner": self.library._source_creator(intent.skill_id)}
                    can_use = not intent.creator_id or self.library.can_use(intent.creator_id, intent.skill_id)
                if source != intent.scope_json.get("source") or not can_use:
                    state = "authorization_changed"
                elif item["digest"] != intent.expected_digest:
                    state = "skill_changed"
                elif item["version_status"] == "released":
                    state = "published_not_switched"
                else:
                    state = "prepared"
            except (ValueError, OSError, KeyError, TypeError, AttributeError):
                state = "unknown"
        return {"status": state, "series_id": series_id, "skill_id": intent.skill_id,
                "digest": intent.expected_digest, "expected_revision": intent.expected_revision,
                "current_skill_id": current_id, "current_revision": current_revision}
