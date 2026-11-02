"""Freeze a complete selected-Skill/material set before queueing a task."""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from creatoros.integrations.producer_skills import _digest, freeze_skill, declared_skill_outputs
from .task_contracts import TaskExecutionSpec


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_source(path: Path, roots) -> Path:
    path = path.absolute()
    if not any(path.resolve().is_relative_to(Path(root).resolve()) for root in roots):
        raise ValueError("Skill 冻结源不属于本数据库 Skill 库或受管任务快照。")
    if any(part.is_symlink() or getattr(part, "is_junction", lambda: False)()
           for part in (path, *path.parents)):
        raise ValueError("冻结输入不允许链接或目录联接。")
    return path


def freeze_execution_spec(spec, catalog, target: Path, *, trusted_skill_roots=()) -> TaskExecutionSpec:
    spec = TaskExecutionSpec.model_validate(spec)
    target = Path(target).resolve()
    roots = [catalog.root, *trusted_skill_roots]
    skills = []
    for number, selected in enumerate(spec.skills, 1):
        if selected.path:
            source = _safe_source(Path(selected.path), roots)
        else:
            source = Path(catalog.resolve_for_task(selected.skill_id, kind=spec.kind,
                primary=selected.role == "primary", expected_digest=selected.digest))
        if _digest(source) != selected.digest:
            raise ValueError("Skill 与任务固定版本不一致。")
        if selected.role == "primary" and spec.kind not in declared_skill_outputs(source):
            raise ValueError("主 Skill 没有声明本任务需要的输出能力。")
        destination = target / "skills" / f"{number:02d}-{selected.role}"
        if source.resolve() != destination.resolve():
            freeze_skill(source, destination, selected.digest)
        skills.append(selected.model_copy(update={"path": str(destination)}))
    materials = []
    for number, item in enumerate(spec.materials, 1):
        source = Path(item.path)
        if (not source.is_file() or any(part.is_symlink() or getattr(part, "is_junction", lambda: False)()
                for part in (source, *source.parents)) or file_sha256(source) != item.sha256):
            raise ValueError("执行素材缺失或内容与固定摘要不一致。")
        destination = target / "materials" / f"{number:03d}{source.suffix.lower()}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if file_sha256(destination) != item.sha256:
                raise ValueError("冻结素材已变化。")
        else:
            shutil.copyfile(source, destination)
        if file_sha256(destination) != item.sha256 or file_sha256(source) != item.sha256:
            raise ValueError("素材复制期间发生变化。")
        materials.append(item.model_copy(update={"path": str(destination)}))
    return spec.model_copy(update={"skills": skills, "materials": materials})


def verify_execution_spec(spec: TaskExecutionSpec, root: Path, *, ready=False) -> None:
    for item in spec.skills:
        if not item.path:
            raise ValueError("任务 Skill 尚未冻结。")
        source = _safe_source(Path(item.path), [root])
        if _digest(source) != item.digest:
            raise ValueError("任务冻结 Skill 已变化。")
    for item in spec.materials:
        source = _safe_source(Path(item.path), [root])
        if not source.is_file() or file_sha256(source) != item.sha256:
            raise ValueError("任务冻结素材已变化。")
    if ready and spec.kind == "video":
        kinds = {item.kind for item in spec.materials}
        if "images_zip" not in kinds or ("audio" not in kinds and not spec.bgm_reference):
            raise ValueError("视频制作需要图片 ZIP，以及音频文件或 BGM 分享链接。")
