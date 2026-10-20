"""Draft editing and explicit trial runs, independent of operational accounts."""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from pathlib import Path
from uuid import uuid4

from PIL import Image

from .producer_skills import _digest, _write, freeze_skill
from .skill_extraction import ExtractionError, ExtractionResult, now


def require_draft(service, job, digest):
    if job.get("task_kind") == "calibrate":
        calibration = getattr(service, "calibration_service", None)
        if calibration is None or calibration._row(job["calibration_id"]).status != "draft":
            raise ExtractionError("这份校准已保存或正在保存，不能继续编辑或试用。", 409)
    if (job["status"] != "ready" or job.get("operation")
            or job.get("queue", {}).get("state") in {"queued", "running"}):
        raise ExtractionError("请等待当前操作结束，或选择未入库草稿。", 409)
    if not digest or digest != job["digest"] or _digest(service._draft_root(job)) != digest:
        raise ExtractionError("草稿版本已变化，请重新检查。", 409)
    if service.closed:
        raise ExtractionError("服务正在关闭，请稍后提交。", 409)


def references(service, job):
    return sorted((service._path("jobs", job["id"]) / "references").glob("reference-*"))


def edit(service, job_id, expected_digest, skills):
    with service.lock:
        job = service._read(job_id)
        # A lost response can retry the exact same edit without losing a version.
        signature = hashlib.sha256(json.dumps(skills, sort_keys=True).encode()).hexdigest()
        previous = job.get("last_edit", {})
        if (previous.get("base") == expected_digest and previous.get("signature") == signature
                and previous.get("result") == job["digest"]):
            return service.get(job_id)
        require_draft(service, job, expected_digest)
        service._publish(job_id, ExtractionResult(skills=skills, note=job["note"],
            suggested_topic=job.get("suggested_topic", "沿用参考作品的内容试做，保留内容与呈现特点。")), references(service, job))
        service._update(job_id, last_edit={"base": expected_digest, "signature": signature,
                                         "result": service._read(job_id)["digest"]})
        return service.get(job_id)


def begin(service, job_id, request_id, digest, operation, value):
    job = service._read(job_id)
    key = hashlib.sha256(request_id.encode()).hexdigest()
    signature = {"operation": operation, "digest": digest, "value": value}
    old = job.get("actions", {}).get(key)
    if old is not None:
        if old != signature:
            raise ExtractionError("同一请求的参数已变化，请使用新的请求 ID。", 409)
        from .skill_job_queue import kick
        kick(service)
        return job, key, False
    require_draft(service, job, digest)
    if not request_id.strip() or len(request_id) > 128 or not value.strip() or len(value) > 4000:
        raise ExtractionError("请填写有效的要求或选题。")
    job.setdefault("actions", {})[key] = signature
    return job, key, True


def launch(service, job, operation, key, directory):
    job.update(operation=operation, error=None, error_type=None, cancel_requested=False,
               progress_directory=directory.relative_to(service._path("jobs", job["id"])).as_posix(),
               updated_at=now())
    from .skill_job_queue import enqueue
    enqueue(service, job, operation, key)


def prepared_operation(directory, request):
    """A failed queue commit may retry frozen preparation, never claimed work."""
    marker = directory / "preparation.json"
    if marker.is_file():
        if json.loads(marker.read_text(encoding="utf-8")) != request:
            raise ExtractionError("同一操作请求的输入已变化，请使用新的请求 ID。", 409)
        return True
    if directory.exists():
        # Preserve a partial, never-authorized copy instead of overwriting it.
        directory.rename(directory.with_name(directory.name + "-incomplete-" + uuid4().hex))
    return False


def revise(service, job_id, request_id, expected_digest, instruction):
    with service.lock:
        job, key, fresh = begin(service, job_id, request_id, expected_digest, "revise", instruction)
        if not fresh:
            return service.get(job_id)
        directory = service._path("jobs", job_id) / "revisions" / key
        request = {"request_id": request_id, "digest": expected_digest,
                   "instruction": instruction, "skills": job["skills"]}
        if prepared_operation(directory, request):
            launch(service, job, "revise", key, directory)
            return service.get(job_id)
        directory.mkdir(parents=True)
        _write(directory / "request.json", request)
        if job.get("source_text"):
            (directory / "source.txt").write_text(job["source_text"], encoding="utf-8")
        from .skill_draft_files import seed_file_drafts
        seed_file_drafts(directory, job["mode"], {s["role"]: service._draft_root(job) / s["role"]
                                                for s in job["skills"]})
        if job.get("task_kind") == "calibrate":
            shutil.copyfile(service._path("jobs", job_id) / "calibration_context.json",
                            directory / "calibration_context.json")
        if job.get("task_kind") == "merge":
            shutil.copyfile(service._path("jobs", job_id) / "merge_context.json",
                            directory / "merge_context.json")
            from .skill_merge import seed_model_sources
            seed_model_sources(service._path("jobs", job_id), directory / "draft")

        _write(directory / "preparation.json", request)
        launch(service, job, "revise", key, directory)
        return service.get(job_id)


def run_revision(service, job_id, key):
    job = service._read(job_id)
    directory = service._path("jobs", job_id) / "revisions" / key
    request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
    try:
        result = asyncio.run(service.reviser(directory, references(service, job), job["mode"],
            request["instruction"], service.cancel_event,
            lambda value: service._update(job_id, thread_id=value), request["skills"]))
        with service.lock:
            if service.cancel_event.is_set():
                raise ExtractionError("改稿已取消。")
            if job.get("task_kind") == "merge":
                from .skill_merge import retain_source_material
                retain_source_material(service._path("jobs", job_id), result)
            service._publish(job_id, result, references(service, job))
    except Exception as error:
        from .skill_extraction import extraction_failure, record_failure_details
        kind, message = extraction_failure(error, service.cancel_event.is_set())
        service._update(job_id, operation=None, cancel_requested=False,
            error="改稿未完成，原草稿保留。" + message, error_type=kind)
        record_failure_details(directory, error, "interrupted" if kind == "codex_interrupted" else "failed", message)


def trial(service, job_id, request_id, expected_digest, topic):
    from .skill_pair import SkillVersion
    with service.lock:
        job, key, fresh = begin(service, job_id, request_id, expected_digest, "trial", topic)
        if not fresh:
            return service.get(job_id)
        companion = job.get("calibration_companion") if job.get("task_kind") == "calibrate" else None
        if (job["mode"] == "mind" and not companion) or any(s.get("output_kind", "image-carousel") != "image-carousel"
                                        for s in job["skills"] if s["role"] != "mind"):
            raise ExtractionError("当前草稿不包含图片制作能力；可入库后在栏目中组合使用。")
        root = service._path("jobs", job_id) / "trials" / key
        snapshot = root / "skill-snapshot" / "skills"
        source = service._draft_root(job)
        request = {"request_id": request_id, "digest": expected_digest, "topic": topic,
                   "mode": job["mode"], "skills": job["skills"], "companion": companion}
        if not prepared_operation(root, request):
            versions = {}
            for skill in job["skills"]:
                role = skill["role"]
                freeze_skill(source / role, snapshot / role, _digest(source / role))
                versions[role] = SkillVersion(id=skill["name"], name=skill["name"],
                                              digest=_digest(source / role), local_path=str(snapshot / role))
            if companion:
                role = companion["role"]
                frozen = service._path("jobs", job_id) / "base" / "companion"
                freeze_skill(frozen, snapshot / role, companion["digest"])
                versions[role] = SkillVersion(id=companion["id"], name=companion["name"],
                                             digest=companion["digest"], local_path=str(snapshot / role))
            _write(root / "request.json", {"topic": topic, "mode": job["mode"],
                "skills": job["skills"], "companion": bool(companion),
                "versions": {role: version.model_dump() for role, version in versions.items()}})
            _write(root / "preparation.json", request)
        directory = root / "revision-001" / "attempt-001"
        record = {"id": key, "status": "queued", "digest": expected_digest, "topic": topic,
                  "error": None, "thread_id": None, "created_at": now()}
        job.setdefault("trials", []).append(record)
        # Directory does not yet exist: native Producer owns its creation.
        launch(service, job, "trial", key, directory)
        return service.get(job_id)


def run_trial(service, job_id, key):
    from .skill_pair import SkillPair, SkillVersion
    job = service._read(job_id)
    root = service._path("jobs", job_id) / "trials" / key
    request = json.loads((root / "request.json").read_text(encoding="utf-8"))
    directory = root / "revision-001" / "attempt-001"
    snapshot = root / "skill-snapshot" / "skills"
    versions = {role: SkillVersion.model_validate(value) for role, value in request["versions"].items()}

    def update(**changes):
        with service.lock:
            current = service._read(job_id)
            item = next(t for t in current["trials"] if t["id"] == key)
            item.update(**changes)
            service._update(job_id, trials=current["trials"])

    try:
        # Recheck the frozen input before spending; never load current shared Skills.
        for role, version in versions.items():
            if _digest(snapshot / role) != version.digest:
                raise ExtractionError("试用排队期间输入快照发生变化。", 409)
        producer = service.producer_factory()
        kwargs = dict(directory=directory, pack_id=f"trial-{key}", creator_id="skill-trial",
            series_id=job_id, topic_id=key, topic_title=request["topic"], topic_brief=request["topic"],
            production_protocol="native-v1", cancel_event=service.cancel_event,
            on_thread_started=lambda value: update(thread_id=value))
        if request["mode"] == "pair" or request["companion"]:
            kwargs["composition"] = SkillPair(adapter="native-carousel-v1", **versions)
        else:
            skill = request["skills"][0]
            kwargs.update(skill_name=skill["name"], skill_directory=snapshot / skill["role"],
                          skill_digest=versions[skill["role"]].digest)
        producer.produce_to(**kwargs)
        if service.cancel_event.is_set():
            raise ExtractionError("试用已取消。")
        trial_cards(directory, job_id, key, complete=True)
        from ..runs.artifacts import validate_artifact
        composition = kwargs.get("composition")
        checked = validate_artifact(directory, composition=composition,
            production_protocol="native-v1") if job.get("task_kind") == "calibrate" else None
        update(status="completed", **({"artifact_digest": checked.artifact_digest,
            "composition": composition.model_dump() if composition else None} if checked else {}))
    except Exception as error:
        from .skill_extraction import extraction_failure, record_failure_details
        kind, _ = extraction_failure(error, service.cancel_event.is_set())
        status = "interrupted" if kind == "codex_interrupted" else "failed"
        message = "试用已停止，已保存图片保留。" if status == "interrupted" else "试用失败，草稿与已保存图片保留；详情见本地试用记录。"
        update(status=status, error=message)
        service._update(job_id, error=message, error_type=kind)
        record_failure_details(root, error, status, message)
    finally:
        service._update(job_id, operation=None, cancel_requested=False)


def trial_directory(service, job_id, key):
    if not key or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise ExtractionError("试用记录不存在。", 404)
    return service._path("jobs", job_id) / "trials" / key / "revision-001" / "attempt-001"


def trial_cards(directory, job_id, key, complete=False):
    from ..content import SocialContentPack
    from .native_production import load_checkpoint, safe_file
    cards = []
    checkpoint = load_checkpoint(directory)
    if (directory / "native_checkpoint.json").exists() and checkpoint is None:
        raise ValueError("试用检查点或其图片已变化。")
    if complete:
        pack = SocialContentPack.load(directory)
        for item in pack.cards:
            path = safe_file(directory / "images", directory / item.image_path)
            with Image.open(path) as image:
                image.verify()
            page = next((p for p in checkpoint.pages if p.order == item.order), None) if checkpoint else None
            if checkpoint and (page is None or hashlib.sha256(path.read_bytes()).hexdigest() != page.sha256):
                raise ValueError("试用交付图片与检查点不一致。")
            cards.append({"order": item.order, "url": f"/api/skill-extractions/{job_id}/trials/{key}/cards/{item.order}",
                          "image_prompt": page.image_prompt if page else item.visual_brief or ""})
    elif checkpoint:
        cards = [{"order": p.order, "url": f"/api/skill-extractions/{job_id}/trials/{key}/cards/{p.order}",
                  "image_prompt": p.image_prompt} for p in checkpoint.pages]
    return cards


def project_trial(service, job_id, trial):
    directory = trial_directory(service, job_id, trial["id"])
    result = {**trial, "progress": None, "cards": []}
    try:
        from .production_progress import ProductionProgress
        result["progress"] = ProductionProgress.model_validate_json(
            (directory / "production_progress.json").read_text(encoding="utf-8")).model_dump()
    except (OSError, ValueError):
        pass
    try:
        result["cards"] = trial_cards(directory, job_id, trial["id"], complete=trial["status"] == "completed")
    except (OSError, ValueError):
        if trial["status"] == "completed":
            result.update(status="failed", error="试用图片缺失或已变化。")
    return result


def trial_image(service, job_id, key, order):
    from ..content import SocialContentPack
    from .native_production import load_checkpoint, safe_file
    with service.lock:
        trial = next((t for t in service._read(job_id).get("trials", []) if t["id"] == key), None)
        if trial is None:
            raise ExtractionError("试用不存在。", 404)
        directory = trial_directory(service, job_id, key)
        try:
            if trial["status"] == "completed":
                trial_cards(directory, job_id, key, complete=True)
                pack = SocialContentPack.load(directory)
                item = next(c for c in pack.cards if c.order == order)
                return safe_file(directory / "images", directory / item.image_path)
            checkpoint = load_checkpoint(directory)
            item = next(p for p in checkpoint.pages if p.order == order)
            return safe_file(directory / "partial-images", Path(item.image_path))
        except (OSError, ValueError, StopIteration, AttributeError):
            raise ExtractionError("试用图片不存在或未完成保存。", 404)
