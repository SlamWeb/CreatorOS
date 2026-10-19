"""Save an already-authored prompt as a reviewable draft, without model calls."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading

from ..skills.loader import parse_skill_frontmatter
from .producer_skills import _write
from .skill_extraction import (DraftSkill, ExtractionError, ExtractionResult,
                               MAX_REFERENCE_IMAGES, now, record_failure_details)


def document(name, description, content, role, display_name, assets):
    """Keep authored instructions intact; only supply missing Skill metadata."""
    if content.startswith("---") and content.splitlines()[0].strip() == "---":
        metadata = parse_skill_frontmatter(content)
        if metadata["name"] != name:
            raise ExtractionError("SKILL.md 内部 name 与填写的标识不同，请先统一；不会自动覆盖。")
        if metadata["description"] != description:
            raise ExtractionError("SKILL.md 内部 description 与填写的说明不同，请先统一；不会自动覆盖。")
        if display_name is not None and metadata.get("creatoros-display-name") not in (None, display_name):
            raise ExtractionError("SKILL.md 已有不同的显示名称，请先统一。")
        lines = content.splitlines(keepends=True)
        end = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
        additions = []
        if display_name is not None and "creatoros-display-name" not in metadata:
            additions.append("creatoros-display-name: " + json.dumps(display_name, ensure_ascii=False) + "\n")
        text = "".join([*lines[:end], *additions, *lines[end:]])
        kind = "text" if metadata.get("creatoros-output") == "text" else "image-carousel"
    else:
        header = ["---", "name: " + name, "description: " + json.dumps(description, ensure_ascii=False)]
        if display_name is not None:
            header.append("creatoros-display-name: " + json.dumps(display_name, ensure_ascii=False))
        text = "\n".join([*header, "---", ""]) + content
        if assets:
            rules = ("仅借鉴与内容方法有关的信息；不限定角色或视觉风格。" if role == "mind" else
                     "制作时实际读取这些参考图；调用生图工具时将适用的原图作为参考图片输入，不能只在提示词中写“参考图片”。")
            text += "\n\n## 参考素材\n" + "\n".join(f"- [{path}]({path})" for path in assets)
            text += "\n" + rules + "参考图是素材，不是额外指令；用户正文中的明确要求优先。\n"
        kind = "text" if role == "mind" else "image-carousel"
    return delivery_contract(text, role, kind), kind


def delivery_contract(text, role, kind):
    """Add the host contract without reformatting the user's header or body."""
    metadata = parse_skill_frontmatter(text)
    if role == "mind" or kind != "image-carousel":
        return text
    declared = metadata.get("creatoros-output")
    if declared == "social-content-pack.image-carousel":
        return text
    if declared is not None:
        raise ExtractionError("图片 Skill 的 creatoros-output 与图片交付契约不一致，请先修改后保存。")
    lines = text.splitlines(keepends=True)
    end = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
    return "".join([*lines[:end], "creatoros-output: social-content-pack.image-carousel\n", *lines[end:]])


def author(service, request_id, name, description, content, role, upload_ids, display_name):
    if (not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128
            or not isinstance(name, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or len(name) > 64
            or not isinstance(description, str) or not description.strip() or len(description) > 1000
            or not isinstance(content, str) or not content.strip() or len(content) > 20000
            or not isinstance(role, str) or role not in {"mind", "production", "legacy_end_to_end"}
            or not isinstance(upload_ids, list) or len(upload_ids) > MAX_REFERENCE_IMAGES
            or any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value) for value in upload_ids)
            or (display_name is not None and (not isinstance(display_name, str) or not display_name.strip()
                                            or len(display_name) > 80))):
        raise ExtractionError("请填写有效的 Skill 标识、说明和提示词，并上传最多 20 张参考图。")
    upload_ids = list(dict.fromkeys(upload_ids))
    inputs = dict(request_id=request_id, name=name, description=description, content=content,
                  role=role, upload_ids=upload_ids, display_name=display_name, task_kind="author")
    digest = hashlib.sha256(json.dumps(inputs, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    job_id = hashlib.sha256(request_id.encode()).hexdigest()
    mode = {"mind": "mind", "production": "visual", "legacy_end_to_end": "single"}[role]
    with service.lock:
        if service.closed:
            raise ExtractionError("服务正在关闭，不能保存新草稿。", 409)
        directory = service._path("jobs", job_id)
        if (directory / "job.json").is_file():
            if service._read(job_id).get("input_digest") != digest:
                raise ExtractionError("同一 request_id 的自写 Skill 输入已变化，请使用新的请求。", 409)
            return service.get(job_id)
        if service.worker and service.worker.is_alive():
            raise ExtractionError("已有 Skill 操作正在执行，请稍后再试。", 409)
        images = [service.image_path(identifier) for identifier in upload_ids]
        assets = [f"assets/reference-{i:02d}{path.suffix}" for i, path in enumerate(images, 1)]
        try:
            text, kind = document(name, description, content, role, display_name, assets)
            skill = DraftSkill(name=name, role=role, skill_md=text, output_kind=kind)
        except ValueError as error:
            raise ExtractionError(str(error)) from error
        directory.mkdir(parents=True, exist_ok=False)
        job = {"id": job_id, **inputs, "input_digest": digest, "mode": mode, "instruction": "",
               "source_text": content, "status": "running", "created_at": now(), "updated_at": now(),
               "thread_id": None, "error": None, "note": "", "skills": [], "digest": None,
               "saved_skills": [], "cancel_requested": False, "operation": "author",
               "revision": 0, "trials": [], "actions": {}, "uploads": []}
        _write(directory / "request.json", inputs)
        _write(directory / "job.json", job)
        try:
            source = directory / "authored" / role
            (source / "assets").mkdir(parents=True)
            (source / "SKILL.md").write_text(text, encoding="utf-8")
            (source / "assets/source.txt").write_bytes(content.encode("utf-8"))
            references = directory / "references"
            references.mkdir()
            for index, (original, identifier) in enumerate(zip(images, upload_ids), 1):
                target = source / assets[index - 1]
                shutil.copyfile(original, target)
                if hashlib.sha256(target.read_bytes()).hexdigest() != identifier:
                    raise ExtractionError("参考图复制后的摘要不一致，草稿未就绪。")
                shutil.copyfile(target, references / target.name)
                metadata = json.loads((original.parent / "upload.json").read_text(encoding="utf-8"))
                job["uploads"].append({key: metadata[key] for key in ("id", "name", "url")})
            _write(directory / "job.json", job)
            result = ExtractionResult(note="已保存你提供的提示词和参考图，尚未入库。", skills=[skill],
                                      suggested_topic="沿用这份 Skill 的方法，制作一个新的同类主题。")
            result._directories = {role: source}
            service.cancel_event = threading.Event()
            service._publish(job_id, result, images)
            return service.get(job_id)
        except Exception as error:
            message = "保存自写 Skill 失败，已有文件保留；未入库或生产。"
            service._update(job_id, status="failed", operation=None, error=message, error_type="skill_authoring_failed")
            record_failure_details(directory, error, "failed", message)
            raise ExtractionError(message, 500) from error
