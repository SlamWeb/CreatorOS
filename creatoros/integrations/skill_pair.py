"""The first explicit content -> presentation -> carousel adapter, not a merger."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from pydantic import Field, model_validator

from .codex import ProductionModel, ProductionReceipt
from .producer_skills import ProducerSkillCatalog, _digest, freeze_skill, inherit_copy_permissions

EVIDENCE_FILE = "production_evidence.json"


class SkillVersion(ProductionModel):
    id: str
    name: str
    github_url: str
    commit: str
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    local_path: str | None = None


class SkillPair(ProductionModel):
    adapter: Literal["pagespec-xiaobai-carousel-v1"] = "pagespec-xiaobai-carousel-v1"
    mind: SkillVersion
    production: SkillVersion


class PageEvidence(ProductionModel):
    order: int = Field(ge=1)
    page_spec: str = Field(min_length=1, description="完整的上游 PageSpec，含本页技术事实来源，不是事后概述")
    image_prompt: str = Field(min_length=1, description="本页实际提交生图工具的完整 Prompt；不可事后编造")
    reference_assets: list[str] = Field(min_length=1, description="制作 Skill 内实际使用的相对资源路径，例如 assets/character.png")


class ContentPage(ProductionModel):
    order: int = Field(ge=1)
    page_spec: str = Field(min_length=1, description="本页定稿内容，包含实际读者能看到的文案和来源")


class StoryboardReceipt(ProductionModel):
    research_brief: str = Field(min_length=1)
    causal_chain: str = Field(min_length=1)
    pages: list[ContentPage] = Field(min_length=1)

    @model_validator(mode="after")
    def ordered(self):
        if [p.order for p in self.pages] != list(range(1, len(self.pages) + 1)):
            raise ValueError("内容 pages 必须从 1 连续编号。")
        return self


class PairReceipt(ProductionReceipt):
    research_brief: str = Field(min_length=1)
    causal_chain: str = Field(min_length=1)
    pages: list[PageEvidence] = Field(min_length=1)

    @model_validator(mode="after")
    def matched_pages(self):
        if [p.order for p in self.pages] != [c.order for c in self.cards]:
            raise ValueError("PageSpec、Prompt 与图片必须逐页对应，连续编号。")
        if len({c.source_image_path for c in self.cards}) != len(self.cards):
            raise ValueError("每一页必须是独立的生成图片，不能重复使用同一图片路径。")
        for page in self.pages:
            if "assets/character.png" not in page.reference_assets:
                raise ValueError("小白每页必须提供角色参考图。")
        return self


class PairEvidence(ProductionModel):
    composition: SkillPair
    prompt_provenance: Literal["producer_reported_not_image_service_verified"]
    research_brief: str = Field(min_length=1)
    causal_chain: str = Field(min_length=1)
    pages: list[PageEvidence] = Field(min_length=1)


def snapshot_pair(catalog: ProducerSkillCatalog, mind_id: str, production_id: str) -> SkillPair:
    mind, production = catalog.describe(mind_id), catalog.describe(production_id)
    if mind["role"] != "mind" or production["role"] != "production":
        raise ValueError("双 Skill 的内容/制作角色不匹配。")
    # Only audited source families have an adapter. Arbitrary pairs remain installable.
    if (mind["name"] not in {"knowledge-to-storyboard", "knowledge-to-storyboard-deep"}
            or production["name"] != "xiaobai"
            or "/".join(mind["github_url"].split("/")[:5]).removesuffix(".git").lower()
            != "https://github.com/slamweb/knowledge-to-storyboard"
            or "/".join(production["github_url"].split("/")[:5]).removesuffix(".git").lower()
            != "https://github.com/slamweb/creatoros-ip-skills"):
        raise ValueError("此双 Skill 组合尚无生产适配器；当前支持 knowledge-to-storyboard + xiaobai。")
    for record in (mind, production):
        catalog.locate(record["id"])
    if not (catalog.locate(production_id) / "assets" / "character.png").is_file():
        raise ValueError("小白角色参考图缺失。")
    return SkillPair(**{role: SkillVersion(**{key: record.get(key) for key in SkillVersion.model_fields})
                        for role, record in (("mind", mind), ("production", production))})


def freeze_pair(catalog: ProducerSkillCatalog, pair: SkillPair, directory: Path,
                *, source_root: Path | None = None) -> list[tuple[str, Path]]:
    if source_root is None and snapshot_pair(catalog, pair.mind.id, pair.production.id) != pair:
        raise ValueError("本地 Skill 已变化，请重新取得本次调用快照。")
    refs = []
    for role in ("mind", "production"):
        version = getattr(pair, role)
        target = directory / "skills" / role
        source = source_root / "skills" / role if source_root else catalog.locate(version.id)
        freeze_skill(source, target, version.digest)
        refs.append((version.name, target / "SKILL.md"))
    return refs


def prepare_run_pair(catalog: ProducerSkillCatalog, pair: SkillPair, run_root: Path,
                     previous_directories: list[Path], *, first_start: bool) -> SkillPair:
    """Freeze current local files once, then resume only from that Run's files."""
    target = run_root / "skill-snapshot"
    metadata = target / "pair.json"
    if target.exists():
        frozen = SkillPair.model_validate_json(metadata.read_text(encoding="utf-8"))
        if (frozen.mind.id, frozen.production.id) != (pair.mind.id, pair.production.id):
            raise ValueError("Run 的本地 Skill 绑定与冻结文件不一致。")
        if not first_start and frozen != pair:
            raise ValueError("Run 的 Skill 快照记录已变化。")
        for role in ("mind", "production"):
            folder = target / "skills" / role
            if folder.is_symlink() or _digest(folder) != getattr(frozen, role).digest:
                raise ValueError("Run 的冻结 Skill 文件已变化。")
        return frozen

    source_root = None
    if first_start and pair.mind.local_path and pair.production.local_path:
        pair = snapshot_pair(catalog, pair.mind.local_path, pair.production.local_path)
    else:
        # Historical attempts already contain the actual Skill bytes used by Codex.
        for previous in previous_directories:
            if all((previous / "skills" / role).is_dir()
                   and _digest(previous / "skills" / role) == getattr(pair, role).digest
                   for role in ("mind", "production")):
                source_root = previous
                break
    run_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".run-skills-", dir=run_root) as temporary:
        staging = Path(temporary) / "snapshot"
        staging.mkdir()
        if source_root is not None or (first_start and pair.mind.local_path and pair.production.local_path):
            freeze_pair(catalog, pair, staging, source_root=source_root)
        else:
            for role in ("mind", "production"):
                version = getattr(pair, role)
                freeze_skill(catalog.original(version.id, version.digest),
                             staging / "skills" / role, version.digest)
        (staging / "pair.json").write_text(pair.model_dump_json(indent=2), encoding="utf-8")
        staging.rename(target)
        inherit_copy_permissions(target)
    return pair


def mind_prompt(name: str, request: str) -> str:
    return (
        f"@{name} 请调研并生成本次主题的完整内容 pages，读取并使用这个内容 Skill。\n"
        "本次选题说明的受众与范围优先于栏目默认值。页数按内容需要决定，保持整篇递进连贯。"
        "只交付内容，不设计 IP、画风或生图 Prompt，不调用生图。返回约定 JSON。\n"
        + request
    )


def visual_prompt(name: str, storyboard: StoryboardReceipt) -> str:
    return (
        f"@{name} 用这个视觉 Skill 把下面整套 pages 可视化，读取并使用这个 Skill。\n"
        "保持内容、页序和跨页连续性，不重新调研或改写教学逻辑。"
        "实际查看并传入 assets/character.png 作为每页生图参考，transparent_background=false。\n"
        "真实调用生图工具完成全部图片；回执逐页保留原 page_spec、真正提交工具的 image_prompt、"
        "相对 reference_assets 和工具返回的 source_image_path。不要发布，不写最终 Manifest。"
        "只有全部图片完成才返回约定 JSON；不能用占位图、代码绘图或 HTML 截图代替生图。\n"
        + storyboard.model_dump_json(indent=2)
    )


def write_evidence(directory: Path, pair: SkillPair, receipt: PairReceipt) -> None:
    evidence = {
        "composition": pair.model_dump(mode="json"),
        "prompt_provenance": "producer_reported_not_image_service_verified",
        "research_brief": receipt.research_brief,
        "causal_chain": receipt.causal_chain,
        "pages": [page.model_dump(mode="json") for page in receipt.pages],
    }
    (directory / EVIDENCE_FILE).write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    for page in receipt.pages:
        folder = directory / "pages" / f"{page.order:02d}"
        folder.mkdir(parents=True)
        (folder / "content.md").write_text(page.page_spec, encoding="utf-8")
        (folder / "prompt.txt").write_text(page.image_prompt, encoding="utf-8")


def evidence_files(root: Path, pair: SkillPair, orders: list[int]) -> list[Path]:
    """Validate handoff + frozen resources; return exact additional digest inputs."""
    def safe(path: Path) -> Path:
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("生产证据路径越界。")
        return path

    evidence = PairEvidence.model_validate_json(safe(root / EVIDENCE_FILE).read_text(encoding="utf-8"))
    if evidence.composition != pair:
        raise ValueError("产物 Skill 版本与 Run 不匹配。")
    pages = evidence.pages
    if [p.order for p in pages] != orders:
        raise ValueError("生产证据与图片页数/顺序不一致。")
    paths = [root / EVIDENCE_FILE]
    if (root / "storyboard.json").exists() or (root / "storyboard.md").exists():
        storyboard = StoryboardReceipt.model_validate_json(safe(root / "storyboard.json").read_text(encoding="utf-8"))
        if ([(p.order, p.page_spec) for p in storyboard.pages] != [(p.order, p.page_spec) for p in pages]
                or storyboard.research_brief != evidence.research_brief or storyboard.causal_chain != evidence.causal_chain):
            raise ValueError("内容阶段 handoff 与最终生产证据不一致。")
        markdown = safe(root / "storyboard.md")
        if markdown.read_text(encoding="utf-8") != "\n\n---\n\n".join(p.page_spec for p in storyboard.pages):
            raise ValueError("可读内容稿与 handoff 不一致。")
        paths.extend([root / "storyboard.json", markdown])
    for role in ("mind", "production"):
        folder = safe(root / "skills" / role)
        if _digest(folder) != getattr(pair, role).digest:
            raise ValueError("冻结的 Skill 或参考资产发生变化。")
        paths.extend(sorted(p for p in folder.rglob("*") if p.is_file()))
    for page in pages:
        if "assets/character.png" not in page.reference_assets:
            raise ValueError("缺少小白角色引用。")
        for ref in page.reference_assets:
            target = safe(root / "skills" / "production" / ref)
            if not target.resolve().is_relative_to((root / "skills" / "production").resolve()) or not target.is_file():
                raise ValueError("参考资源缺失或越界。")
        for name, text in (("content.md", page.page_spec), ("prompt.txt", page.image_prompt)):
            path = safe(root / "pages" / f"{page.order:02d}" / name)
            if path.read_text(encoding="utf-8") != text:
                raise ValueError("逐页内容/Prompt 与生产回执不一致。")
            paths.append(path)
    return [safe(path) for path in paths]
