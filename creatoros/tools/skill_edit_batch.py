"""Sequential adapter for several existing Skill file edit operations.

The batch is deliberately not atomic: every operation uses the existing
compare-and-set endpoint and successful edits remain as a candidate if a later
operation fails.
"""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .results import ToolResult


class SkillEditChangeArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    operation: Literal["write_text", "add_image", "replace_image", "delete_image"]
    path: str = Field(min_length=1, max_length=512)
    content: str | None = Field(default=None, max_length=512 * 1024)
    attachment_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def matching_payload(self):
        if self.operation == "write_text":
            if self.content is None or self.attachment_id is not None:
                raise ValueError("改文字需要完整 content，不能同时传图片附件。")
        elif self.content is not None:
            raise ValueError("图片操作不接受文本 content。")
        elif self.operation in {"add_image", "replace_image"}:
            if not self.attachment_id:
                raise ValueError("新增或替换参考图需要当前会话 attachment_id。")
        elif self.attachment_id is not None:
            raise ValueError("删除参考图不需要上传附件。")
        return self


def apply_skill_edit_batch(skill_id: str, expected_digest: str,
                           changes: list[SkillEditChangeArgs], context=None) -> ToolResult:
    """Apply one bounded batch, stopping at the first failed or uncertain write."""
    # Imports stay local so studio.py can own the public single-edit schema and
    # delegate batch execution here without a module import cycle.
    from .studio import get_producer_skill, update_producer_skill_file

    normalized = [change if isinstance(change, SkillEditChangeArgs)
                  else SkillEditChangeArgs.model_validate(change) for change in changes]
    total = len(normalized)
    versions_result = get_producer_skill(skill_id, context=context, include_versions=True)
    if versions_result.is_error:
        return _receipt(skill_id, expected_digest, [], total, None,
                        versions_result.error_type or "skill_read_failed", outcome_unknown=False,
                        message="无法确认 Skill 家族和当前版本；没有开始批量编辑。")
    try:
        versions = json.loads(versions_result.content)
        items = versions["items"]
        family_id = versions["family_id"]
        source = next((item for item in items if item.get("id") == skill_id), None)
        if (not isinstance(source, dict) or source.get("family_id") != family_id
                or source.get("digest") != expected_digest):
            current = source.get("digest") if isinstance(source, dict) else None
            return _receipt(skill_id, current or expected_digest, [], total, None,
                            "skill_digest_conflict" if source else "skill_version_unavailable",
                            outcome_unknown=False,
                            message="Skill 家族、目标版本或摘要未通过前置核对；没有开始批量编辑。")
    except (ValueError, TypeError, KeyError, StopIteration):
        return _receipt(skill_id, expected_digest, [], total, None,
                        "skill_version_unavailable", outcome_unknown=False,
                        message="无法确认 Skill 家族和当前版本；没有开始批量编辑。")

    current_id, current_digest = skill_id, expected_digest
    applied = []
    for index, change in enumerate(normalized, start=1):
        result = update_producer_skill_file(
            skill_id=current_id,
            path=change.path,
            content=change.content,
            expected_digest=current_digest,
            context=context,
            operation=change.operation,
            attachment_id=change.attachment_id,
        )
        if result.is_error:
            error_type = result.error_type or "skill_edit_failed"
            unknown = error_type in {"studio_outcome_unknown", "studio_invalid_response"}
            try:
                detail = json.loads(result.content).get("message", "")
            except (ValueError, TypeError, AttributeError):
                detail = ""
            detail = detail if isinstance(detail, str) and len(detail) <= 300 else ""
            return _receipt(current_id, current_digest, applied, total, index, error_type,
                            outcome_unknown=unknown, failed=change,
                            message=("写入结果未知；已停止，须核对同一候选家族，不要重新提交整批。"
                                     if unknown else
                                     f"此项失败{('：' + detail) if detail else ''}；已停止。之前成功的候选保留，未执行后续项。"))
        try:
            data = json.loads(result.content)
            next_id = data["skill_id"]
            next_digest = data["digest"]
            next_family = data["family_id"]
            if (not isinstance(next_id, str) or not re.fullmatch(r"[a-z0-9-]+--[a-f0-9]{16}", next_id)
                    or not isinstance(next_digest, str) or not re.fullmatch(r"[a-f0-9]{64}", next_digest)
                    or next_family != family_id):
                raise ValueError("missing or inconsistent edit receipt")
        except (ValueError, TypeError, KeyError):
            # The edit may have happened, but without a valid identity/digest we
            # cannot safely issue another compare-and-set operation.
            return _receipt(current_id, current_digest, applied, total, index,
                            "studio_invalid_response", outcome_unknown=True, failed=change,
                            message="编辑回执缺少可核验的候选身份或摘要；已停止，须核对同一候选家族。")
        current_id, current_digest = next_id, next_digest
        applied.append({"index": index, "operation": change.operation, "path": change.path})

    return _receipt(current_id, current_digest, applied, total, None, None,
                    outcome_unknown=False, message="批量编辑全部完成；结果是候选版本，未发布或切换栏目。")


def _receipt(skill_id, digest, applied, total, failed_index, error_type, *,
             outcome_unknown: bool, message: str, failed: SkillEditChangeArgs | None = None) -> ToolResult:
    completed = len(applied) == total and failed_index is None
    partial = not completed and (bool(applied) or outcome_unknown)
    status = "completed" if completed else "outcome_unknown" if outcome_unknown else "partial" if applied else "failed"
    result = {
        "status": status,
        "partial": partial,
        "completed": completed,
        "applied_count": len(applied),
        "total_count": total,
        "outcome_unknown": outcome_unknown,
        "failed_index": failed_index,
        "skill_id": skill_id,
        "digest": digest,
        "applied": applied,
        "message": message,
    }
    if failed is not None:
        result["failed"] = {"index": failed_index, "operation": failed.operation, "path": failed.path}
    if error_type:
        result["error_type"] = error_type
    return ToolResult(content=json.dumps(result, ensure_ascii=False),
                      is_error=not completed, error_type=error_type)
