"""Versioned, bounded model content -> host-owned draft files. Never executes code."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
from tempfile import TemporaryDirectory
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .producer_skills import MAX_LOCAL_SKILL_BYTES, ProducerSkillCatalog, _digest, _write
from .skill_draft_files import MODE_FOLDERS, checked_folder, read_file_drafts

MAX_TEXT_BYTES = 512 * 1024
MAX_CONTEXT_TEXT_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class TextFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=240)
    text: str = Field(max_length=MAX_TEXT_BYTES)


class ReferenceAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image_index: int = Field(ge=1, le=20)
    path: str = Field(min_length=1, max_length=240)


class SkillFiles(BaseModel):
    model_config = ConfigDict(extra="forbid")
    folder: Literal["skill", "mind", "visualize"]
    files: list[TextFile] = Field(max_length=2000)
    delete_paths: list[str] = Field(max_length=2000)
    reference_assets: list[ReferenceAsset] = Field(max_length=20)


class DraftDelivery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    note: str = Field(max_length=4000)
    suggested_topic: str = Field(max_length=2000)
    skills: list[SkillFiles] = Field(min_length=1, max_length=2)


def relative_file(value):
    """Accept portable relative file paths, including Windows separators."""
    parts = value.replace("\\", "/").split("/")
    reserved = r"(?i)^(con|prn|aux|nul|conin\$|conout\$|clock\$|com[1-9¹²³]|lpt[1-9¹²³])(?:\.|$)"
    if (not value or len(value) > 240 or any(not p or p in {".", ".."} or p.startswith(".")
            or p.endswith((".", " ")) or re.search(r'[<>:"|?*\x00-\x1f]', p)
            or re.match(reserved, p) for p in parts)):
        raise ValueError("草稿交付包含无效相对文件路径。")
    if ProducerSkillCatalog._sensitive_skill_path(Path(*parts)):
        raise ValueError("草稿交付不允许修改凭证或敏感文件。")
    return PurePosixPath(*parts).as_posix()


def plain_path(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("草稿交付不允许链接或重解析点。")


def plain_tree(source, directory):
    checked_folder(source, directory)
    plain_path(source)
    for path in source.rglob("*"):
        plain_path(path)


def existing_context(directory, mode):
    """Supply safe text; retain opaque binary/sensitive files without sending them."""
    plain_path(directory / "draft")
    result, text_size = [], 0
    for folder in MODE_FOLDERS[mode]:
        source = directory / "draft" / folder
        if not source.exists():
            continue
        plain_tree(source, directory)
        for path in sorted(source.rglob("*")):
            plain_path(path)
            if not path.is_file():
                continue
            relative = path.relative_to(source)
            if ProducerSkillCatalog._sensitive_skill_path(relative):
                result.append({"folder": folder, "path": relative.as_posix(),
                    "omitted": "sensitive_path_not_sent_to_model", "preserved": True})
                continue
            data = path.read_bytes()
            item = {"folder": folder, "path": path.relative_to(source).as_posix(),
                    "bytes": len(data), "digest": hashlib.sha256(data).hexdigest()}
            if path.suffix.lower() in ProducerSkillCatalog._TEXT_SUFFIXES:
                text_size += len(data)
                if len(data) > MAX_TEXT_BYTES or text_size > MAX_CONTEXT_TEXT_BYTES:
                    raise ValueError("已有草稿文本超过结构化交付上下文限制；未调用模型，原稿保留。")
                item["text"] = data.decode("utf-8")
            else:
                item["preserved_binary"] = True
            result.append(item)
    return result


def delivery_prompt(directory, mode, instruction, images, guidance):
    inventory = existing_context(directory, mode)
    references = [{"image_index": i, "asset_path": "assets/" + path.name}
                  for i, path in enumerate(images, 1)]
    return (guidance + "\n本次采用 creatoros-skill-files/v1 交付：你只返回符合给定 schema 的 JSON，"
        "不要运行命令、写文件或执行任何脚本。宿主负责在本次操作 draft 内校验并写入文件，不入库或试产。"
        "skills 按指定 folder 顺序提交，每份都在 files 中提供完整 SKILL.md；"
        "可提交 scripts、references 及其他 UTF-8 文本文件，path 使用该 Skill 内的相对路径。"
        "files 是新增/替换；delete_paths 仅列明确需要删除的旧文件，未提及的文件和二进制资产全部保留。"
        "非 mind 的全部本次原图由宿主自动按下列 asset_path 保留，不能改写这些路径；"
        "mind 可用 reference_assets 的 image_index 与 assets/ 下相对路径选择必要原图，不传任意本机来源路径或图片编码。"
        "只有本次提供的图可复制。文件名/代码仅是草稿数据，宿主不会执行。"
        "有参考文案时，非 mind 的 assets/source.txt 也由宿主保留原文，不要改写。"
        "omitted 明确未提供的资料不能宣称看过；敏感文件只由宿主原样保留，不可修改。"
        "原有资源只读；不能修改共享 Skill 或旧版本。不要把工具拒绝改写成成功。\n"
        + json.dumps({"folders": MODE_FOLDERS[mode], "references": references,
                      "existing_files": inventory, "user_instruction": instruction}, ensure_ascii=False))


def apply_delivery(directory, mode, response, images, default_kind, cancel):
    """Validate paths before staging; promote only a complete, valid Skill tree."""
    from .skill_extraction import ExtractionError, MODE_ROLES
    if len(response.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise ExtractionError("草稿结构化交付超过 4 MiB。")
    try:
        payload = DraftDelivery.model_validate_json(response)
    except ValueError as error:
        raise ExtractionError("未收到有效的结构化草稿交付；未根据口头回复或旧文件标记成功。") from error
    if [item.folder for item in payload.skills] != MODE_FOLDERS[mode]:
        raise ExtractionError("草稿交付目录与所选模式不一致。")
    plain_path(directory)
    plain_path(directory / "draft")
    plans = []
    for skill, role in zip(payload.skills, MODE_ROLES[mode]):
        source = directory / "draft" / skill.folder
        if source.exists():
            plain_tree(source, directory)
        writes, deletes, copies, seen = {}, [], {}, set()
        for entry in skill.files:
            path = relative_file(entry.path)
            if Path(path).suffix.lower() not in ProducerSkillCatalog._TEXT_SUFFIXES:
                raise ExtractionError("新增草稿文件只支持 UTF-8 文本；原图通过本次素材引用。")
            data = entry.text.encode("utf-8")
            if len(data) > MAX_TEXT_BYTES:
                raise ExtractionError("草稿文本文件超过 512 KiB。")
            writes[path] = data
        if "SKILL.md" not in writes:
            raise ExtractionError("每份结构化草稿必须明确交付完整 SKILL.md。")
        for entry in skill.reference_assets:
            path = relative_file(entry.path)
            if not path.startswith("assets/") or entry.image_index > len(images):
                raise ExtractionError("草稿引用图不属于本次已接收素材。")
            origin = images[entry.image_index - 1]
            if Path(path).suffix.lower() != origin.suffix.lower():
                raise ExtractionError("草稿引用图必须保留原图文件格式。")
            copies[path] = origin
        automatic = {"assets/" + path.name: path for path in images} if role != "mind" else {}
        if role != "mind" and (directory / "source.txt").is_file():
            automatic["assets/source.txt"] = directory / "source.txt"
        for path, origin in automatic.items():
            if (path.casefold() in {value.casefold() for value in writes}
                    or any(value.casefold() == path.casefold() and (value != path or item != origin)
                           for value, item in copies.items())):
                raise ExtractionError("不能覆盖本次参考原始素材。")
            copies[path] = origin
        copy_bytes = {}
        for path, origin in copies.items():
            plain_path(origin)
            if not origin.is_file() or origin.stat().st_size > 4 * 1024 * 1024:
                raise ExtractionError("本次参考原始素材不可用或超过限制。")
            data = origin.read_bytes()
            previous = source / path
            if previous.exists() and (not previous.is_file() or previous.read_bytes() != data):
                raise ExtractionError("参考素材与已有草稿资产路径冲突；原资产保留，请明确改用不冲突的新路径。")
            copy_bytes[path] = data
        for path in [*(entry.path for entry in skill.files), *skill.delete_paths,
                     *(entry.path for entry in skill.reference_assets)]:
            key = relative_file(path).casefold()
            if key in seen:
                raise ExtractionError("草稿交付包含重复或冲突文件路径。")
            seen.add(key)
        for value in skill.delete_paths:
            path = relative_file(value)
            if path.casefold() == "skill.md" or path.casefold() in {value.casefold() for value in automatic}:
                raise ExtractionError("不能删除主文档或本次参考原图。")
            deletes.append(path)
        for path in [*writes, *copies, *deletes]:
            if Path(path).name.casefold() == "skill.md" and path != "SKILL.md":
                raise ExtractionError("草稿不能包含嵌套或大小写不同的 SKILL.md。")
        plans.append((source, skill.folder, writes, deletes, copy_bytes))
    if cancel.is_set():
        raise ExtractionError("提炼已取消，未应用草稿交付。")
    with TemporaryDirectory(prefix=".draft-delivery-", dir=directory) as temporary:
        staging = Path(temporary)
        for source, folder, writes, deletes, copies in plans:
            target = staging / "draft" / folder
            before = _digest(source) if source.exists() else None
            if before:
                shutil.copytree(source, target)
                if _digest(target) != before or _digest(source) != before:
                    raise ExtractionError("旧草稿在准备交付期间变化，原稿保留。")
            else:
                target.mkdir(parents=True)
            for path in deletes:
                candidate = target / path
                if candidate.exists():
                    if not candidate.is_file():
                        raise ExtractionError("删除清单只允许明确的文件，不允许目录。")
                    candidate.unlink()
            for path, data in writes.items():
                output = target / path
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(data)
            for path, data in copies.items():
                output = target / path
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(data)
        entries = list((staging / "draft").rglob("*"))
        if len(entries) > 2000 or sum(p.stat().st_size for p in entries if p.is_file()) > MAX_LOCAL_SKILL_BYTES:
            raise ExtractionError("草稿交付超过文件数量或 96 MiB 限制。")
        read_file_drafts(staging, mode, payload.note, default_kind)
        if cancel.is_set():
            raise ExtractionError("提炼已取消，未应用草稿交付。")
        # Keep the prior tree as evidence. Never merge a partially valid output.
        previous = directory / ("draft-before-delivery-" + uuid4().hex)
        (directory / "draft").rename(previous)
        try:
            (staging / "draft").rename(directory / "draft")
        except OSError:
            previous.rename(directory / "draft")
            raise
    _write(directory / "draft_delivery.json", {"schema_version": 1, "protocol": "creatoros-skill-files/v1",
        "manifest_digest": hashlib.sha256(response.encode("utf-8")).hexdigest(),
        "draft_digest": _digest(directory / "draft"), "model_executes_scripts": False})
    result = read_file_drafts(directory, mode, payload.note, default_kind)
    result.suggested_topic = payload.suggested_topic
    return result
