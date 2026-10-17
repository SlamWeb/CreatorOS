from __future__ import annotations

import re
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml


_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def parse_skill_frontmatter(content: str) -> dict:
    """Read and validate Skill metadata with the same YAML rules at every entry point."""
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("缺少 YAML frontmatter 起始标记。")
    try:
        end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration:
        raise ValueError("缺少 YAML frontmatter 结束标记。") from None
    try:
        values = yaml.safe_load("\n".join(lines[1:end]) + "\n")
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        location = f"（第 {mark.line + 2} 行，第 {mark.column + 1} 列）" if mark else ""
        raise ValueError(f"YAML frontmatter 格式无效{location}；请检查语法或不受支持的标签。") from error
    if not isinstance(values, dict):
        raise ValueError("YAML frontmatter 必须是键值对象。")
    name = values.get("name")
    if not isinstance(name, str) or not _NAME_PATTERN.fullmatch(name) or len(name) > 64:
        raise ValueError("name 必须是小写字母、数字和单连字符组成的名称，且不能超过 64 个字符；中文名称请用「改名」设置。")
    description = values.get("description")
    if not isinstance(description, str) or not description.strip() or len(description) > 1_024:
        raise ValueError("description 必须是非空字符串，且不能超过 1024 个字符。")
    if "creatoros-display-name" in values:
        display_name = values["creatoros-display-name"]
        if (
            not isinstance(display_name, str)
            or not display_name.strip()
            or len(display_name) > 80
            or any(unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} for char in display_name)
        ):
            raise ValueError("creatoros-display-name 必须是 1–80 字符的名称，不能包含换行或控制字符。")
    return values


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path
    base_dir: Path
    content: str | None = None
    display_name: str | None = None


@dataclass(frozen=True)
class SkillDiagnostic:
    path: Path
    message: str


class SkillLoader:
    """Discover Skill metadata and load full SKILL.md content on demand."""

    def __init__(self, roots: Iterable[Path], project_root: Path | None = None):
        self.roots = tuple(Path(root).resolve() for root in roots)
        self.project_root = Path(project_root).resolve() if project_root else None
        self.diagnostics: list[SkillDiagnostic] = []
        self._skills: tuple[Skill, ...] | None = None

    @classmethod
    def from_defaults(cls):
        from ..config import PROJECT_ROOT

        return cls([PROJECT_ROOT / "creatoros" / "skills"], project_root=PROJECT_ROOT)

    def reload(self) -> tuple[Skill, ...]:
        self._skills = None
        return self.discover()

    def discover(self) -> tuple[Skill, ...]:
        if self._skills is not None:
            return self._skills

        self.diagnostics = []
        discovered: dict[str, Skill] = {}
        for root in self.roots:
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("SKILL.md")):
                skill = self._read_metadata(path)
                if skill is None:
                    continue
                if skill.name in discovered:
                    self.diagnostics.append(
                        SkillDiagnostic(path, f"重复的 Skill 名称：{skill.name}，保留先发现的版本。")
                    )
                    continue
                discovered[skill.name] = skill
        self._skills = tuple(discovered.values())
        return self._skills

    def load(self, name: str) -> Skill:
        skill = next((item for item in self.discover() if item.name == name), None)
        if skill is None:
            raise KeyError(f"未发现 Skill：{name}")
        return Skill(
            name=skill.name,
            description=skill.description,
            path=skill.path,
            base_dir=skill.base_dir,
            content=skill.path.read_text(encoding="utf-8"),
            display_name=skill.display_name,
        )

    def format_available_prompt(self) -> str:
        skills = self.discover()
        if not skills:
            return ""
        lines = [
            "<available_skills>",
            "以下 Skill 可按需使用；匹配任务后，用 read_file 读取 location 的完整 SKILL.md。",
        ]
        for skill in skills:
            location = self._display_path(skill.path)
            lines.append(
                f'<skill name="{skill.name}" location="{location}">'
                f"{skill.description}</skill>"
            )
        lines.append("</available_skills>")
        return "\n".join(lines)

    def inject_available_skills(self, messages: list[dict]) -> list[dict]:
        prompt = self.format_available_prompt()
        if not prompt:
            return deepcopy(list(messages))

        enriched = deepcopy(list(messages))
        for message in enriched:
            if message.get("role") == "system":
                current = message.get("content") or ""
                message["content"] = f"{current}\n\n{prompt}"
                return enriched
        enriched.insert(0, {"role": "system", "content": prompt})
        return enriched

    def format_invocation(self, name: str, additional_instructions: str | None = None) -> str:
        skill = self.load(name)
        location = self._display_path(skill.path)
        block = (
            f'<skill name="{skill.name}" location="{location}">\n'
            f"References are relative to {self._display_path(skill.base_dir)}.\n\n"
            f"{skill.content or ''}\n</skill>"
        )
        if additional_instructions:
            return f"{block}\n\n{additional_instructions}"
        return block

    def _display_path(self, path: Path) -> str:
        if self.project_root is not None:
            try:
                return path.relative_to(self.project_root).as_posix()
            except ValueError:
                pass
        return path.as_posix()

    def _read_metadata(self, path: Path) -> Skill | None:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as error:
            self.diagnostics.append(SkillDiagnostic(path, f"读取失败：{error}"))
            return None

        try:
            values = parse_skill_frontmatter(text)
        except ValueError as error:
            self.diagnostics.append(SkillDiagnostic(path, str(error)))
            return None
        return Skill(
            values["name"], values["description"], path.resolve(), path.resolve().parent,
            display_name=values.get("creatoros-display-name"),
        )
