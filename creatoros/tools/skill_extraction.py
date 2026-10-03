"""Agent adapters for the Studio artifact-to-Skill workflow."""
import base64
from pathlib import Path
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, model_validator
from creatoros.integrations.skill_extraction import DraftSkill

from .builtins import _is_sensitive_path, _project_root
from .results import ToolResult
from .studio import _call

MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_IMAGE_COUNT = 6
_IMAGE_SIGNATURES = {
    ".jpg": ("image/jpeg", lambda data: data.startswith(b"\xff\xd8\xff")),
    ".jpeg": ("image/jpeg", lambda data: data.startswith(b"\xff\xd8\xff")),
    ".png": ("image/png", lambda data: data.startswith(b"\x89PNG\r\n\x1a\n")),
    ".webp": ("image/webp", lambda data: len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"),
}


class ExtractSkillsFromArtifactArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    image_paths: list[str] | None = Field(default=None, min_length=1, max_length=MAX_IMAGE_COUNT,
        description="用户明确提供的本地参考图路径；仅允许项目目录内非敏感的 JPEG、PNG、WebP 文件，每张最多 4 MiB。与 upload_ids 二选一。")
    upload_ids: list[str] | None = Field(default=None, min_length=1, max_length=MAX_IMAGE_COUNT,
        description="Studio 图片上传接口返回的 ID；与 image_paths 二选一，适用于浏览器已上传的附件。")
    request_id: str = Field(min_length=1, max_length=128,
        description="本次提炼的幂等 ID；若提交结果不确定，查询状态或用同一 ID 重试，不能另造 ID 重复提交。")
    mode: str = Field(default="single", pattern="^(pair|mind|visual|single)$",
        description="默认 single 提炼完整 Skill；可选 mind 内容方法、visual 呈现方法、pair 分别提炼两份。")
    source_text: str = Field(default="", max_length=20000, description="作为提炼素材的原始文案，与 instruction 用户要求分开。可单独提供，也可与图片组合。")
    instruction: str = Field(default="", max_length=4000,
        description="可选提炼要求；留空即可。")

    @model_validator(mode="after")
    def validate_image_source(self):
        if self.image_paths is not None and self.upload_ids is not None:
            raise ValueError("image_paths 与 upload_ids 不能同时提供。")
        if not (self.image_paths or self.upload_ids or self.source_text.strip()):
            raise ValueError("请提供参考图片或文案。")
        return self


class GetSkillExtractionArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    job_id: str | None = Field(default=None, min_length=1,
        description="提炼任务 ID；省略时列出任务，用于找回刷新前的任务。")


class SaveExtractedSkillsArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    job_id: str = Field(min_length=1, description="待保存提炼任务 ID。")
    expected_digest: str = Field(min_length=1, description="预览任务返回的 digest；用于确认保存的是已查看的草稿。")


class CancelSkillExtractionArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    job_id: str = Field(min_length=1, description="要取消的提炼任务 ID。")


class ReviseExtractedSkillsArgs(SaveExtractedSkillsArgs):
    request_id: str = Field(min_length=1, max_length=128)
    instruction: str = Field(min_length=1, max_length=4000, description="用户要求的草稿修改，不会自动试用或入库。")


class TrialExtractedSkillsArgs(SaveExtractedSkillsArgs):
    request_id: str = Field(min_length=1, max_length=128)
    topic: str = Field(min_length=1, max_length=4000, description="用户明确要试做的选题；会真实生成图片，结果仅在草稿区。")


class EditExtractedSkillsArgs(SaveExtractedSkillsArgs):
    skills: list[DraftSkill] = Field(min_length=1, max_length=2)


def _read_reference_images(image_paths, context=None):
    if not 1 <= len(image_paths) <= MAX_IMAGE_COUNT:
        return ToolResult("参考图数量必须为 1–6 张。", True, "invalid_arguments")
    if context is not None and context.archive_only_reads:
        return ToolResult("此 Web 会话不能读取本机项目路径；请先在 Skill 页面上传参考图，再使用 upload_ids。",
                          True, "path_out_of_scope")

    root = _project_root(context)
    images = []
    for raw_path in image_paths:
        try:
            path = (root / raw_path).resolve(strict=True)
            path.relative_to(root)
        except (OSError, RuntimeError, ValueError):
            return ToolResult("只能读取 CreatorOS 项目目录内已存在的参考图。", True, "path_out_of_scope")
        if _is_sensitive_path(path, root):
            return ToolResult("拒绝读取敏感路径作为参考图。", True, "path_out_of_scope")
        if not path.is_file():
            return ToolResult("参考图路径必须指向普通文件。", True, "invalid_image")

        suffix = path.suffix.casefold()
        image_type = _IMAGE_SIGNATURES.get(suffix)
        if image_type is None:
            return ToolResult("仅支持 JPEG、PNG、WebP 参考图。", True, "invalid_image")
        try:
            if path.stat().st_size > MAX_IMAGE_BYTES:
                return ToolResult("单张参考图不能超过 4 MiB。", True, "image_too_large")
            with path.open("rb") as stream:
                data = stream.read(MAX_IMAGE_BYTES + 1)
        except OSError:
            return ToolResult("无法读取参考图。", True, "file_read_failed")
        if len(data) > MAX_IMAGE_BYTES:
            return ToolResult("单张参考图不能超过 4 MiB。", True, "image_too_large")
        if not image_type[1](data):
            return ToolResult("参考图扩展名与图像内容不匹配，或文件不是支持的图像。", True, "invalid_image")
        images.append((path.name, image_type[0], data))
    return images


def extract_skills_from_artifact(image_paths=None, request_id=None, mode="single", instruction="", context=None,
                                 upload_ids=None, source_text=""):
    """Upload only explicitly named local images, then submit the idempotent job."""
    if image_paths is not None and upload_ids is not None:
        return ToolResult("image_paths 与 upload_ids 不能同时提供。", True, "invalid_arguments")
    if not (image_paths or upload_ids or source_text.strip()):
        return ToolResult("请提供参考图片或文案。", True, "invalid_arguments")
    local_uploads = []
    if image_paths is not None:
        images = _read_reference_images(image_paths, context)
        if isinstance(images, ToolResult):
            return images
        local_uploads = images
    elif upload_ids is not None and not 1 <= len(upload_ids) <= MAX_IMAGE_COUNT:
        return ToolResult("参考图数量必须为 1–6 张。", True, "invalid_arguments")

    def submit(client):
        resolved_upload_ids = list(upload_ids or [])
        for name, _content_type, data in local_uploads:
            uploaded = client.request("POST", "/api/skill-extractions/uploads", payload={
                "name": name,
                "data_base64": base64.b64encode(data).decode("ascii"),
            })
            resolved_upload_ids.append(uploaded["id"])
        return client.request("POST", "/api/skill-extractions", payload={
            "request_id": request_id,
            "upload_ids": resolved_upload_ids,
            "mode": mode,
            "instruction": instruction,
            "source_text": source_text,
        })

    return _call(submit, context)


def get_skill_extraction(job_id=None, context=None):
    if job_id:
        path = f"/api/skill-extractions/{quote(job_id, safe='')}"
    else:
        path = "/api/skill-extractions"
    return _call(lambda client: client.request("GET", path), context)


def save_extracted_skills(job_id, expected_digest, context=None):
    path = f"/api/skill-extractions/{quote(job_id, safe='')}/save"
    return _call(lambda client: client.request("POST", path, payload={"expected_digest": expected_digest}), context)


def cancel_skill_extraction(job_id, context=None):
    path = f"/api/skill-extractions/{quote(job_id, safe='')}/cancel"
    return _call(lambda client: client.request("POST", path, payload={}), context)


def edit_extracted_skills(job_id, expected_digest, skills, context=None):
    return _call(lambda client: client.request("POST", f"/api/skill-extractions/{quote(job_id, safe='')}/draft",
        payload={"expected_digest": expected_digest, "skills": skills}), context)


def revise_extracted_skills(job_id, expected_digest, request_id, instruction, context=None):
    return _call(lambda client: client.request("POST", f"/api/skill-extractions/{quote(job_id, safe='')}/revise",
        payload={"expected_digest": expected_digest, "request_id": request_id, "instruction": instruction}), context)


def trial_extracted_skills(job_id, expected_digest, request_id, topic, context=None):
    return _call(lambda client: client.request("POST", f"/api/skill-extractions/{quote(job_id, safe='')}/trials",
        payload={"expected_digest": expected_digest, "request_id": request_id, "topic": topic}), context)
