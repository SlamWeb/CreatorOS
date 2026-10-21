"""Bounded durable calibration around existing Skill-version trials.

Waiting never holds a model call. All drafts stay independent; this service has
no operation that publishes a Skill or changes a column binding.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import re
from threading import Event, RLock, Thread
from uuid import uuid4

from PIL import Image
from sqlalchemy import select

from ..storage import Creator, Series, SkillIteration, Topic, TopicRemoval
from .producer_skills import _SKILL_EDIT_LOCK, _digest, _write, freeze_skill
from .skill_extraction import ExtractionError
from .skill_iteration_judge import REPLIES, default_provider, evaluate, score_result

log = logging.getLogger(__name__)
ACTIVE = {"queued", "running", "waiting_production"}
MIME = {"PNG": ("image/png", ".png"), "JPEG": ("image/jpeg", ".jpg"), "WEBP": ("image/webp", ".webp")}


def stamp():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class SkillIterationService:
    def __init__(self, database, versions, chat, *, provider_factory=default_provider, evaluator=None):
        self.database, self.versions, self.chat = database, versions, chat
        self.catalog, self.extractions = versions.catalog, versions.extractions
        self.provider_factory = provider_factory
        self.evaluator = evaluator or evaluate
        self.root = self.catalog.root / "iterations"
        self.lock, self.stopping, self.wake = RLock(), Event(), Event()
        self.worker = None
        self.owner = uuid4().hex

    def _path(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{64}", identifier):
            raise ExtractionError("迭代任务不存在。", 404)
        path = self.root / identifier[:16]
        for part in (path, *path.parents):
            if self.catalog._is_link_or_reparse(part):
                raise ExtractionError("迭代文件路径无效。", 409)
            if part == self.catalog.root:
                break
        manifest = path / "identity.json"
        if manifest.exists() and json.loads(manifest.read_text(encoding="utf-8"))["id"] != identifier:
            raise ExtractionError("任务目录标识冲突，原文件保留。", 409)
        return path

    def _row(self, identifier):
        self._path(identifier)
        with self.database.session() as db:
            row = db.get(SkillIteration, identifier)
            if row is None:
                raise ExtractionError("迭代任务不存在。", 404)
            return row

    def _file_path(self, identifier, *parts):
        root = self._path(identifier)
        path = root.joinpath(*parts)
        if not path.is_relative_to(root):
            raise ExtractionError("迭代文件路径无效。", 409)
        for parent in (path, *path.parents):
            if self.catalog._is_link_or_reparse(parent):
                raise ExtractionError("迭代文件路径无效。", 409)
            if parent == root:
                break
        return path

    def _update(self, identifier, action):
        with self.lock, self.database.session() as db:
            row = db.get(SkillIteration, identifier)
            if row is None:
                raise ExtractionError("迭代任务不存在。", 404)
            state = deepcopy(row.state_json)
            before = (row.status, row.last_error, deepcopy(row.state_json))
            action(row, state)
            if before == (row.status, row.last_error, state):
                return
            row.state_json = state
            row.revision += 1

    @staticmethod
    def _event(state, kind, message, **details):
        state.setdefault("events", []).append(dict(time=stamp(), kind=kind, message=message,
                                                   **({"details": details} if details else {})))

    def _scope(self, config):
        with self.database.session() as db:
            series, topic = db.get(Series, config["series_id"]), db.get(Topic, config["topic_id"])
            creator = db.get(Creator, config["creator_id"])
            if (series is None or not series.is_active or series.creator_id != config["creator_id"]
                    or creator is None or not creator.is_active
                    or topic is None or topic.series_id != series.id or db.get(TopicRemoval, topic.id)):
                raise ExtractionError("账号、栏目或选题归属已变化，迭代已停止。", 409)
            if series.revision != config["series_revision"]:
                raise ExtractionError("栏目配置已变化，请核对后新建迭代；不会偷偷换搭档。", 409)
        companion = config.get("companion")
        if companion and self.catalog.describe(companion["id"])["digest"] != companion["digest"]:
            raise ExtractionError("搭档 Skill 已变化，迭代已停止。", 409)

    def create(self, *, series_id, skill_id, topic_id, instructions="", attachment_ids=None,
               reference_paths=None, max_rounds=3, request_id, origin_session_id=None):
        if type(max_rounds) is not int or not 1 <= max_rounds <= 3:
            raise ExtractionError("首次迭代支持1～3轮，默认3轮。", 422)
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise ExtractionError("请提供有效的迭代请求标识。", 422)
        inputs = dict(series_id=series_id, skill_id=skill_id, topic_id=topic_id,
            instructions=instructions, attachment_ids=attachment_ids or [], reference_paths=reference_paths,
            max_rounds=max_rounds, origin_session_id=origin_session_id)
        identity = digest({"kind": "skill-iteration", "session": origin_session_id, "request_id": request_id})
        signature = digest(inputs)
        with self.lock, _SKILL_EDIT_LOCK:
            with self.database.session() as db:
                old = db.get(SkillIteration, identity)
                if old:
                    if old.input_digest != signature:
                        raise ExtractionError("同一迭代请求的输入已变化。", 409)
                    return self.get(identity)
                series, topic = db.get(Series, series_id), db.get(Topic, topic_id)
                if (series is None or not series.is_active or not series.creator_id or topic is None
                        or topic.series_id != series_id or db.get(TopicRemoval, topic_id)):
                    raise ExtractionError("请选择同一账号栏目中的有效选题。", 404)
                creator = db.get(Creator, series.creator_id)
                if creator is None or not creator.is_active:
                    raise ExtractionError("账号已停用，不能开始迭代。", 409)
                item = self.catalog.describe(skill_id)
                role, bound_id = self.versions._binding(series, skill_id)
                companion_id = next((v for k, v in self.versions._bound(series).items() if k != role), None)
                config = dict(series_id=series_id, series_name=series.name, creator_id=series.creator_id,
                    series_revision=series.revision, skill_id=skill_id, skill_name=item.get("display_name") or item["name"],
                    base_digest=item["digest"], skill_role=item["role"], topic_id=topic_id, topic_title=topic.title,
                    topic_brief=topic.brief, instructions=instructions, origin_session_id=origin_session_id,
                    companion=self.catalog.describe(companion_id) if companion_id else None)
            directory = self._path(identity)
            directory.mkdir(parents=True, exist_ok=True)
            manifest = directory / "identity.json"
            if manifest.exists() and json.loads(manifest.read_text(encoding="utf-8")).get("signature") != signature:
                raise ExtractionError("同一准备请求的输入已变化，原文件保留。", 409)
            _write(directory / "identity.json", {"id": identity, "signature": signature})
            freeze_skill(self.catalog.locate(skill_id), directory / "base", item["digest"])
            pool = self._freeze_references(identity, skill_id, attachment_ids or [], reference_paths, origin_session_id)
            if not pool:
                raise ExtractionError("这份Skill没有可用参考图，请上传优秀作品后再开始RSI。", 422)
            config["references"] = pool
            baseline = self.catalog.create_skill_candidate(skill_id, item["digest"], f"rsi-{identity}-1")
            config["baseline_candidate_id"] = baseline["id"]
            state = dict(phase="rubric", max_rounds=max_rounds, rounds=[], calls={},
                judge={"version": 0, "threshold": 80, "critical_floor": 60, "criteria": [], "history": []},
                usage={"input_tokens": 0, "output_tokens": 0, "calls": 0}, events=[], actions={},
                next_parent_id=skill_id, next_parent_digest=item["digest"])
            self._event(state, "created", "RSI已排队；最多%d轮，首轮保留原版作为基线，不自动切栏目。" % max_rounds)
            with self.database.session() as db:
                db.add(SkillIteration(id=identity, request_id=identity, input_digest=signature,
                    creator_id=config["creator_id"], series_id=series_id, skill_id=skill_id, topic_id=topic_id,
                    status="queued", revision=1, config_json=config, state_json=state))
        self.wake.set()
        return self.get(identity)

    def _freeze_references(self, identifier, skill_id, attachments, selected, sid):
        listing = self.catalog.list_skill_files(skill_id)
        paths = [f["path"] for f in listing["files"] if f["kind"] == "image"]
        if selected is not None:
            if len(set(selected)) != len(selected) or any(path not in paths for path in selected):
                raise ExtractionError("请从这份Skill的文件列表选择参考图片。", 422)
            paths = selected
        sources = [(path, self.catalog.locate(skill_id) / path) for path in paths]
        if attachments:
            if not sid:
                raise ExtractionError("会话附件需要当前对话身份。", 403)
            for key in dict.fromkeys(attachments):
                meta = self.chat.attachments.get(sid, key)
                sources.append((meta["name"], self.chat.attachments.path(sid, key)))
        if len(sources) > 20:
            raise ExtractionError("参考超过20张，请用reference_paths选出本次需要的作品。", 422)
        result, total, seen = [], 0, set()
        for name, path in sources:
            raw = path.read_bytes()
            sha = hashlib.sha256(raw).hexdigest()
            if sha in seen:
                continue
            seen.add(sha)
            total += len(raw)
            if len(raw) > 4 * 1024 * 1024 or total > 20 * 1024 * 1024:
                raise ExtractionError("参考图单张最多4MiB，总计最多20MiB。", 422)
            with Image.open(path) as image:
                mime, suffix = MIME[image.format]
                image.verify()
            key = "ref_" + sha[:16]
            target = self._file_path(identifier, "references", key + suffix)
            target.parent.mkdir(exist_ok=True)
            if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != sha:
                raise ExtractionError("已冻结参考被修改，原任务保留。", 409)
            if not target.exists():
                target.write_bytes(raw)
            result.append(dict(key=key, name=name, sha256=sha, file=target.name, mime_type=mime))
        return result

    def get(self, identifier):
        row = self._row(identifier)
        c, s = deepcopy(row.config_json), deepcopy(row.state_json)
        rounds = s.get("rounds", [])
        for round_ in rounds:
            round_.pop("files", None)
        note = s.get("note", "")
        return dict(id=row.id, creator_id=row.creator_id, series_id=row.series_id, series_name=c["series_name"],
            skill_id=row.skill_id, skill_name=c["skill_name"], topic_id=row.topic_id, topic_title=c["topic_title"],
            status=row.status, stage=s["phase"], revision=row.revision, round_number=len(rounds),
            round_count=len(rounds), current_round=len(rounds), max_rounds=s["max_rounds"],
            judge=s["judge"], rounds=rounds, best_skill_id=s.get("best_skill_id"),
            best_candidate_id=s.get("best_skill_id"), best_round=s.get("best_round"), best_score=s.get("best_score"),
            error=row.last_error, note=note, usage=s["usage"], events=s["events"],
            references=[{k: v for k, v in p.items() if k != "file"} | {
                "url": f"/api/skill-iterations/{identifier}/files/{p['key']}"} for p in c["references"]],
            can_pause=row.status in ACTIVE, can_resume=row.status in {"paused", "interrupted"},
            can_continue=row.status in {"exhausted", "failed"},
            allowed_actions=(["pause"] if row.status in ACTIVE else ["resume"] if row.status in {"paused", "interrupted"}
                             else ["continue"] if row.status in {"exhausted", "failed"} else []),
            url=f"/skills?iteration={identifier}", created_at=row.created_at.isoformat(), updated_at=row.updated_at.isoformat())

    def list(self, *, creator_id=None, series_id=None, limit=20):
        with self.database.session() as db:
            statement = select(SkillIteration).order_by(SkillIteration.created_at.desc())
            if creator_id:
                statement = statement.join(Series, Series.id == SkillIteration.series_id).where(
                    SkillIteration.creator_id == creator_id, Series.creator_id == creator_id,
                    Series.is_active.is_(True))
            if series_id:
                statement = statement.where(SkillIteration.series_id == series_id)
            identities = [r.id for r in db.scalars(statement.limit(min(max(limit, 1), 100)))]
        return {"items": [self.get(key) for key in identities]}

    def file(self, identifier, key):
        row = self._row(identifier)
        if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", key):
            raise ExtractionError("迭代图片不存在。", 404)
        for item in row.config_json["references"]:
            if item["key"] == key:
                path = self._file_path(identifier, "references", item["file"])
                if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                    raise ExtractionError("参考图片已变化。", 409)
                return path
        for round_ in row.state_json["rounds"]:
            if key in round_.get("files", {}):
                item = round_["files"][key]
                # A producer can never choose an arbitrary read path here.
                from .skill_workbench import trial_image
                path = trial_image(self.extractions, round_["trial_job_id"], round_["trial_id"], item["order"])
                if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                    raise ExtractionError("试用图片已变化。", 409)
                return path
        raise ExtractionError("迭代图片不存在。", 404)

    def action(self, identifier, *, action, expected_revision, request_id):
        inputs = dict(action=action, expected_revision=expected_revision)
        def change(row, state):
            receipts = state["actions"]
            if request_id in receipts:
                if receipts[request_id] != inputs:
                    raise ExtractionError("同一操作请求的输入已变化。", 409)
                return
            if row.revision != expected_revision:
                raise ExtractionError("迭代状态已变化，请刷新后操作。", 409)
            if action == "pause" and row.status in ACTIVE:
                row.status = "paused"
                self._event(state, "paused", "已暂停后续步骤；已提交Codex的任务不取消、不重复提交。")
            elif action == "resume" and row.status in {"paused", "interrupted"}:
                self._scope(row.config_json)
                for call in state["calls"].values():
                    if (call["status"] in {"unknown", "failed"} or
                            call["status"] == "inflight" and call.get("owner") != self.owner):
                        call["status"] = "retry_authorized"
                row.status, row.last_error = "queued", None
                self._event(state, "resumed", "人工恢复同一迭代任务；先核对已有试用，再继续。")
            elif action == "continue" and row.status in {"exhausted", "failed"}:
                self._scope(row.config_json)
                if state["max_rounds"] >= 30:
                    raise ExtractionError("本任务已到30轮，请保留证据并新建任务。", 409)
                state["max_rounds"] = min(30, state["max_rounds"] + 3)
                if row.status == "failed":
                    if state["phase"] == "waiting":
                        last = state["rounds"][-1]
                        # No receipt means submission may already have landed.
                        # Reconcile the same persistent identity before deciding
                        # to spend another round; only a known failed trial can
                        # authorize a new production on manual continuation.
                        if last.get("trial_job_id"):
                            state["phase"] = "prepare"
                            state["next_parent_id"], state["next_parent_digest"] = last["skill_id"], last["digest"]
                    else:
                        for call in state["calls"].values():
                            if call["status"] in {"unknown", "failed", "inflight"}:
                                call["status"] = "retry_authorized"
                elif state["phase"] == "finished":
                    state["phase"] = "revise"
                row.status, row.last_error = "queued", None
                self._event(state, "continued", "人工追加最多3轮，旧产物和Judge历史保留。")
            else:
                raise ExtractionError("当前状态不能执行该操作。", 409)
            receipts[request_id] = inputs
        self._update(identifier, change)
        self.wake.set()
        return self.get(identifier)

    def start(self):
        with self.lock:
            if self.worker and self.worker.is_alive():
                return
            self.stopping.clear()
            with self.database.session() as db:
                for row in db.scalars(select(SkillIteration).where(SkillIteration.status.in_(ACTIVE))):
                    state = deepcopy(row.state_json)
                    unknown = [c for c in state["calls"].values() if c["status"] == "inflight"]
                    if unknown:
                        for call in unknown:
                            call["status"] = "unknown"
                        row.status, row.last_error = "interrupted", "上次DS请求结果未知；人工恢复后才重试，未重新生图。"
                        self._event(state, "interrupted", row.last_error)
                        row.state_json, row.revision = state, row.revision + 1
            self.worker = Thread(target=self._loop, name="skill-iteration", daemon=True)
            self.worker.start()

    def shutdown(self):
        self.stopping.set()
        self.wake.set()
        with self.lock, self.database.session() as db:
            for row in db.scalars(select(SkillIteration).where(SkillIteration.status.in_(ACTIVE))):
                state = deepcopy(row.state_json)
                inflight = [c for c in state["calls"].values() if c["status"] == "inflight"]
                if inflight:
                    for call in inflight:
                        call["status"] = "unknown"
                    row.status, row.last_error = "interrupted", "服务关闭，DS请求结果待核对；未重新提交Codex。"
                    row.state_json, row.revision = state, row.revision + 1
        if self.worker:
            self.worker.join(10)

    def _loop(self):
        while not self.stopping.is_set():
            try:
                with self.database.session() as db:
                    ids = list(db.scalars(select(SkillIteration.id).where(SkillIteration.status.in_(ACTIVE))
                                          .order_by(SkillIteration.created_at)))
                for identifier in ids:
                    if self.stopping.is_set():
                        break
                    self.step(identifier)
            except Exception:
                log.exception("Could not advance Skill iteration; no model task was resubmitted")
            self.wake.wait(.75)
            self.wake.clear()

    def step(self, identifier):
        row = self._row(identifier)
        if row.status not in ACTIVE or self.stopping.is_set():
            return
        try:
            self._scope(row.config_json)
            getattr(self, "_" + row.state_json["phase"])(row)
        except Exception as error:
            def fail(current, state):
                if current.status not in ACTIVE:
                    self._event(state, "error", "暂停时收到错误，原产物保留。", reason=str(error)[:2000])
                    return
                current.status = "failed"
                current.last_error = str(error)[:2000] or "迭代未完成，原版本和产物保留。"
                self._event(state, "error", current.last_error)
            self._update(identifier, fail)

    def _images(self, row, round_=None):
        result = [("优秀参考：" + p["name"], self.file(row.id, p["key"])) for p in row.config_json["references"]]
        if round_:
            result += [(f"当前试产第{item['order']}页", self.file(row.id, item["key"])) for item in round_["images"]]
        return result

    def _model(self, row, kind, key, payload, images):
        current = self._row(row.id)
        old = current.state_json["calls"].get(key)
        manifest = {"kind": kind, "input": payload, "images": [
            {"name": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for name, path in images]}
        signature = digest(manifest)
        if old and old.get("input_digest") not in {None, signature}:
            raise ExtractionError("同一步评审的输入已变化，旧响应保留，不能复用。", 409)
        if old and old["status"] == "completed":
            return old["result"]
        if old and old["status"] in {"inflight", "unknown"}:
            raise ExtractionError("这次DS请求结果未知，请人工恢复后核对；不会自动重新生图。", 409)
        if current.status not in ACTIVE or self.stopping.is_set():
            raise ExtractionError("迭代已暂停。", 409)
        # A reply may have reached disk immediately before a shutdown/SQLite
        # failure. Only an explicit resume can adopt it, without another bill.
        if old and old["status"] == "retry_authorized":
            previous = self._file_path(row.id, "model", key + "-" + str(old["attempt"]))
            if (previous / "response.json").is_file():
                saved_request = json.loads((previous / "request.json").read_text(encoding="utf-8"))
                if saved_request != manifest:
                    raise ExtractionError("已保存评审响应的输入不一致，未再次调用DS。", 409)
                saved = json.loads((previous / "response.json").read_text(encoding="utf-8"))
                result = REPLIES[kind].model_validate(saved["result"]).model_dump()
                usage = saved.get("usage")
                def recover(record, state):
                    call = state["calls"][key]
                    if record.status not in ACTIVE or call["status"] != "retry_authorized":
                        raise ExtractionError("迭代状态已变化，未采用旧响应。", 409)
                    call.update(status="completed", result=result, usage=usage, input_digest=signature)
                    for metric in ("input_tokens", "output_tokens"):
                        state["usage"][metric] += int((usage or {}).get(metric) or 0)
                    if not usage:
                        state["usage"]["has_unknown_usage"] = True
                    self._event(state, "model_recovered", "已恢复磁盘上的DS响应，没有重新请求模型。", call=key)
                self._update(row.id, recover)
                return result
        if current.state_json["usage"]["calls"] >= 100 or current.state_json["usage"]["input_tokens"] >= 5_000_000:
            raise ExtractionError("本任务达到模型调用/Token预算，请核对后新建任务。", 409)
        attempt = (old or {}).get("attempt", 0) + 1
        directory = self._file_path(row.id, "model", key + "-" + str(attempt))
        directory.mkdir(parents=True, exist_ok=True)
        _write(directory / "request.json", manifest)
        def begin(record, state):
            if record.status not in ACTIVE or self.stopping.is_set():
                raise ExtractionError("迭代已暂停，本步没有再次调用DS。", 409)
            state["calls"][key] = {"status": "inflight", "kind": kind, "attempt": attempt,
                                   "owner": self.owner, "input_digest": signature}
            state["usage"]["calls"] += 1
            record.status = "running"
            self._event(state, "model_start", {"rubric": "DS正在建立基础Judge", "judge": "DS正在看图评审",
                                               "revise": "DS正在修改独立候选"}[kind], call=key, attempt=attempt)
        self._update(row.id, begin)
        try:
            result, usage = self.evaluator(kind, payload, images, self.provider_factory)
            result = REPLIES[kind].model_validate(result).model_dump()
            _write(directory / "response.json", {"result": result, "usage": usage})
        except Exception as error:
            _write(directory / "error.json", {"error": str(error)[:4000], "time": stamp()})
            def failed(record, state):
                call = state["calls"][key]
                if call.get("owner") == self.owner and call["status"] == "inflight":
                    call["status"] = "failed"
            self._update(row.id, failed)
            raise
        def finish(record, state):
            call = state["calls"][key]
            if call.get("owner") != self.owner or call["status"] != "inflight":
                # The raw response is kept on disk for explicit recovery; an
                # obsolete process must not overwrite another host's state.
                return
            state["calls"][key].update(status="completed", result=result, usage=usage)
            for metric in ("input_tokens", "output_tokens"):
                state["usage"][metric] += int((usage or {}).get(metric) or 0)
            if not usage:
                state["usage"]["has_unknown_usage"] = True
            self._event(state, "model_completed", "DS本步结果已保存。", call=key, usage=usage)
        self._update(row.id, finish)
        if self._row(row.id).state_json["calls"][key]["status"] != "completed":
            raise ExtractionError("服务已停止，本次响应保存在本地，待人工恢复核对。", 409)
        return result

    def _advance(self, identifier, phase, change=None, status="queued"):
        def apply(row, state):
            state["phase"] = phase
            if change:
                change(state)
            if row.status in ACTIVE:
                row.status = status
        self._update(identifier, apply)

    def _rubric(self, row):
        c = row.config_json
        result = self._model(row, "rubric", "rubric", dict(instructions=c["instructions"],
            topic=c["topic_title"], role=c["skill_role"]), self._images(row))
        def save(state):
            criteria = [{"id": f"c{index}", **item} for index, item in enumerate(result["criteria"], 1)]
            state["judge"].update(version=1, criteria=criteria,
                history=[dict(version=1, added_ids=[c["id"] for c in criteria], reason="依据冻结优秀参考建立", at=stamp())])
            self._event(state, "judge_created", "基础Judge已固定；后续只能追加，不能放宽或删除。")
        self._advance(row.id, "prepare", save)

    def _prepare(self, row):
        # Files/queue publication is short and serialized against pause. A pause
        # that has returned can never be followed by a new production submit.
        with self.lock:
            row = self._row(row.id)
            if row.status not in ACTIVE or self.stopping.is_set():
                return
            return self._prepare_locked(row)

    def _prepare_locked(self, row):
        s = row.state_json
        if len(s["rounds"]) >= s["max_rounds"]:
            self._finish(row.id)
            return
        number = len(s["rounds"]) + 1
        candidate = (self.catalog.describe(row.config_json["baseline_candidate_id"]) if number == 1 else
            self.catalog.create_skill_candidate(s["next_parent_id"], s["next_parent_digest"], f"rsi-{row.id}-{number}"))
        if number == 1 and candidate["digest"] != row.config_json["base_digest"]:
            raise ExtractionError("基线候选已被其他编辑修改，原任务保留。", 409)
        if s.get("next_content"):
            edited = self.catalog.update_skill_file(candidate["id"], "SKILL.md", s["next_content"], candidate["digest"])
            candidate = self.catalog.describe(edited["skill_id"])
        round_ = dict(number=number, skill_id=candidate["id"], version_number=candidate["version_number"],
            digest=candidate["digest"], status="queued", images=[], files={}, score=None, passed=False,
            scores=[], changes=s.get("next_changes", "原版副本基线；未修改共享Skill"))
        def save(state):
            state["rounds"].append(round_)
            state.pop("next_content", None)
            state.pop("next_changes", None)
            self._event(state, "candidate", f"第{number}轮候选已保存。", skill_id=candidate["id"], digest=candidate["digest"])
        self._advance(row.id, "waiting", save, "waiting_production")

    def _waiting(self, row):
        with self.lock:
            row = self._row(row.id)
            if row.status not in ACTIVE or self.stopping.is_set():
                return
            return self._waiting_locked(row)

    def _waiting_locked(self, row):
        from .skill_version_service import signature
        from .topic_references import TopicReferenceStore
        from .skill_workbench import trial_image, trial_directory
        c, s = row.config_json, row.state_json
        round_ = s["rounds"][-1]
        if not round_.get("trial_job_id"):
            request_id = f"rsi-{row.id}-{round_['number']}"
            trial_job_id = signature({"kind": "skill-version-trial", "request_id": request_id})
            references = TopicReferenceStore(self.database)
            image_ids = [references.upload(trial_job_id, ref["name"], base64.b64encode(
                self.file(row.id, ref["key"]).read_bytes()).decode())["id"] for ref in c["references"]]
            frozen = references.freeze(trial_job_id, image_ids,
                "本次完整要求见source_user_text。", True, source_user_text=c["instructions"])
            topic = c["topic_title"] + "\n" + (c["topic_brief"] or "") + "\n本次要求：" + c["instructions"]
            result = self.versions.trial(round_["skill_id"], round_["digest"], request_id,
                                         topic, c["series_id"], task_references=frozen)
            trial = result["trials"][-1]
            def submitted(state):
                current = state["rounds"][-1]
                current.update(trial_job_id=result["id"], trial_id=trial["id"],
                    trial_url=f"/skills?extraction={result['id']}", status="running")
                self._event(state, "production_submitted", f"第{round_['number']}轮已交给Codex；等待期间不调用DS。",
                            job_id=result["id"], trial_id=trial["id"])
            self._advance(row.id, "waiting", submitted, "waiting_production")
            return
        job = self.extractions.get(round_["trial_job_id"])
        trial = next(t for t in job["trials"] if t["id"] == round_["trial_id"])
        if trial["status"] in {"queued", "running"}:
            return
        if trial["status"] != "completed":
            self._update(row.id, lambda record, state: state["rounds"][-1].update(status=trial["status"]))
            raise ExtractionError(trial.get("error") or "Codex试用未完成，已停止；原任务和文件保留。", 409)
        self.versions.checked_trial(round_["skill_id"], round_["digest"], job["id"], trial["id"])
        files, images = {}, []
        for card in trial.get("cards", []):
            path = trial_image(self.extractions, job["id"], trial["id"], card["order"])
            key = f"round_{round_['number']}_{card['order']}"
            files[key] = dict(order=card["order"], sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            images.append(dict(order=card["order"], key=key,
                url=f"/api/skill-iterations/{row.id}/files/{key}", alt=f"第{round_['number']}轮第{card['order']}张"))
        directory = trial_directory(self.extractions, job["id"], trial["id"])
        from ..content import SocialContentPack
        pack = SocialContentPack.load(directory)
        def completed(state):
            state["rounds"][-1].update(status="completed", images=images, files=files,
                production_text=pack.publish_copy.title + "\n" + pack.publish_copy.body)
            state["allow_additions"] = True
            self._event(state, "production_completed", f"第{round_['number']}轮图片已验收，准备DS评审。")
        self._advance(row.id, "judge", completed)

    def _judge(self, row):
        s, c = row.state_json, row.config_json
        judge = s["judge"]
        pending = next((r for r in s["rounds"] if r["status"] == "completed" and r.get("judge_version") != judge["version"]), None)
        if pending:
            allow = s.get("allow_additions", False) and pending["number"] == s["rounds"][-1]["number"] and len(judge["criteria"]) < 12
            payload = dict(criteria=judge["criteria"], allow_additions=allow, instructions=c["instructions"],
                topic=c["topic_title"], production_text=pending.get("production_text", ""))
            result = self._model(row, "judge", f"judge-{pending['number']}-{judge['version']}", payload, self._images(row, pending))
            if result.get("new_criteria") and not allow:
                raise ExtractionError("Judge试图在固定重评阶段改标准，已停止。", 409)
            # Validate every old criterion before considering append-only proposals.
            score, passed = score_result(judge["criteria"], result)
            additions = result.get("new_criteria", [])
            def save(state):
                current = next(r for r in state["rounds"] if r["number"] == pending["number"])
                current.update(score=score, passed=passed, scores=result["scores"],
                    feedback=result["feedback"], judge_version=judge["version"])
                current.setdefault("evaluations", []).append(dict(judge_version=judge["version"],
                    score=score, passed=passed, scores=result["scores"], feedback=result["feedback"], time=stamp()))
                state["note"] = result["feedback"]
                if additions:
                    active = state["judge"]
                    unique = [a for a in additions if a["description"] not in {v["description"] for v in active["criteria"]}]
                    unique = unique[:12 - len(active["criteria"])]
                    if unique:
                        new = [{"id": f"c{len(active['criteria']) + i}", **item} for i, item in enumerate(unique, 1)]
                        active["criteria"].extend(new)
                        active["version"] += 1
                        for key in ("best_skill_id", "best_round", "best_score"):
                            state.pop(key, None)
                        active["history"].append(dict(version=active["version"], added_ids=[a["id"] for a in new],
                            reason="DS依据冻结优秀参考发现遗漏，基线与全部候选将统一重评", at=stamp()))
                        self._event(state, "judge_appended", "新增准则已留档；旧评分作废，统一重评，不放宽旧标准。")
                    state["allow_additions"] = False
                self._event(state, "judged", f"第{pending['number']}轮完成Judge v{judge['version']}评审。", score=score, passed=passed)
            self._advance(row.id, "judge", save)
            return
        rated = [r for r in s["rounds"] if r.get("judge_version") == judge["version"] and r["score"] is not None]
        if not rated:
            raise ExtractionError("没有可核对的评审结果。", 409)
        best = max(rated, key=lambda r: (r["passed"], r["score"], -r["number"]))
        def save(state):
            state.update(best_skill_id=best["skill_id"], best_round=best["number"], best_score=best["score"])
        def decide(record, state):
            save(state)
            done = best["passed"] or len(state["rounds"]) >= state["max_rounds"]
            state["phase"] = "finished" if done else "revise"
            if record.status in ACTIVE:
                record.status = ("completed" if best["passed"] else "exhausted") if done else "queued"
            if done:
                self._event(state, "finished", "候选达到本版Judge门槛，等待人工确认。" if best["passed"]
                    else "轮次已用完，最佳候选与全部证据保留，未切栏目。")
        with self.lock, _SKILL_EDIT_LOCK:
            if self.catalog.describe(best["skill_id"])["digest"] != best["digest"]:
                raise ExtractionError("被评审候选已被其他编辑修改，评分与当前文件不一致，已停止而不覆盖。", 409)
            self._update(row.id, decide)

    def _revise(self, row):
        s, c = row.state_json, row.config_json
        best = next((r for r in s["rounds"] if r["number"] == s.get("best_round")), s["rounds"][-1])
        if best.get("passed") and best.get("judge_version") == s["judge"]["version"]:
            self._finish(row.id, passed=True)
            return
        if len(s["rounds"]) >= s["max_rounds"]:
            self._finish(row.id)
            return
        item = self.catalog.read_skill_file(best["skill_id"], "SKILL.md", text_only=True)
        if item["digest"] != best["digest"]:
            raise ExtractionError("候选被其他编辑修改，迭代已停止，不覆盖你的修改。", 409)
        result = self._model(row, "revise", f"revise-{len(s['rounds'])}-{s['judge']['version']}",
            dict(skill_md=item["content"], files=self.catalog.list_skill_files(best["skill_id"])["files"],
                 judge=s["judge"], scores=best["scores"], feedback=best.get("feedback"),
                 instructions=c["instructions"], topic=c["topic_title"]), self._images(row, best))
        def save(state):
            state.update(next_parent_id=best["skill_id"], next_parent_digest=best["digest"],
                         next_content=result["skill_md"], next_changes=result["changes"])
        self._advance(row.id, "prepare", save)

    def _finish(self, identifier, passed=False):
        def finish(row, state):
            state["phase"] = "finished"
            if row.status in ACTIVE:
                row.status = "completed" if passed else "exhausted"
            self._event(state, "finished", "候选达到本版Judge门槛，等待人工检查和切栏目。" if passed
                        else "本轮额度用完，已保留最佳候选、评分与全部失败；未切换栏目。")
        self._update(identifier, finish)

    def _finished(self, row):
        # Pause may land between the final evaluation and its phase advance.
        # Explicit resume must finalize, not dispatch a nonexistent stage or
        # create another paid production.
        state = row.state_json
        best = next((r for r in state["rounds"] if r["number"] == state.get("best_round")), None)
        with self.lock, _SKILL_EDIT_LOCK:
            if best and self.catalog.describe(best["skill_id"])["digest"] != best["digest"]:
                raise ExtractionError("被评审候选已变化，不能沿用旧评分。", 409)
            self._finish(row.id, passed=bool(best and best.get("passed")
                and best.get("judge_version") == state["judge"]["version"]))
