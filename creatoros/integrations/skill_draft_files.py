"""Read native Codex-written Skill folders into the existing workbench DTO."""
from pathlib import Path

from ..skills.loader import SkillLoader
from .producer_skills import _digest, freeze_skill

MODE_FOLDERS = {"single": ["skill"], "mind": ["mind"], "visual": ["visualize"],
                "pair": ["mind", "visualize"]}


def checked_folder(path: Path, root: Path) -> Path:
    from .skill_extraction import ExtractionError
    if path.is_symlink() or not path.is_dir() or not path.resolve().is_relative_to(root.resolve()):
        raise ExtractionError("草稿 Skill 目录缺失或不在本次任务内。")
    entries, size = 0, 0
    for entry in path.rglob("*"):
        entries += 1
        if entry.is_symlink() or not entry.resolve().is_relative_to(path.resolve()):
            raise ExtractionError("Skill 不允许链接或越界文件。")
        if entry.is_file():
            size += entry.stat().st_size
        elif not entry.is_dir():
            raise ExtractionError("Skill 只允许普通文件和目录。")
        if entries > 2000 or size > 32 * 1024 * 1024:
            raise ExtractionError("草稿 Skill 超过文件数量或 32 MiB 限制。")
    return path


def read_file_drafts(directory: Path, mode: str, note: str, default_kind="image-carousel"):
    from .skill_extraction import MODE_ROLES, DraftSkill, ExtractionError, ExtractionResult
    skills, directories = [], {}
    for role, folder in zip(MODE_ROLES[mode], MODE_FOLDERS[mode]):
        path = checked_folder(directory / "draft" / folder, directory)
        document = path / "SKILL.md"
        if not document.is_file() or document.stat().st_size > 96_000:
            raise ExtractionError("草稿缺少 SKILL.md 或正文超过限制。")
        loader = SkillLoader([path])
        found = loader.discover()
        if len(found) != 1 or found[0].path != document.resolve() or loader.diagnostics:
            raise ExtractionError("草稿 SKILL.md 的 name/description 无效或包含多余 Skill。")
        body = document.read_text(encoding="utf-8")
        header = body.split("---", 2)[1]
        output_kind = default_kind
        if "creatoros-output: social-content-pack.image-carousel" in header:
            output_kind = "image-carousel"
        elif "creatoros-output: text" in header:
            output_kind = "text"
        skills.append(DraftSkill(name=found[0].name, role=role,
                                skill_md=body, output_kind=output_kind))
        directories[role] = path
    result = ExtractionResult(note=note.strip()[:4000], skills=skills)
    result._directories = directories
    return result


def seed_file_drafts(directory: Path, mode: str, sources: dict[str, Path]):
    from .skill_extraction import MODE_ROLES
    for role, folder in zip(MODE_ROLES[mode], MODE_FOLDERS[mode]):
        source = Path(sources[role])
        freeze_skill(source, directory / "draft" / folder, _digest(source))
