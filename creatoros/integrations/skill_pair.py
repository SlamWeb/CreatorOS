"""The first explicit content -> presentation -> carousel adapter, not a merger."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .codex import ProductionModel, ProductionReceipt
from .producer_skills import ProducerSkillCatalog, _digest

EVIDENCE_FILE = "production_evidence.json"


class SkillVersion(ProductionModel):
    id: str
    name: str
    github_url: str
    commit: str
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class SkillPair(ProductionModel):
    adapter: Literal["pagespec-xiaobai-carousel-v1"] = "pagespec-xiaobai-carousel-v1"
    mind: SkillVersion
    production: SkillVersion


class PageEvidence(ProductionModel):
    order: int = Field(ge=1)
    page_spec: str = Field(min_length=1, description="完整的上游 PageSpec，含本页技术事实来源，不是事后概述")
    image_prompt: str = Field(min_length=1, description="本页实际提交生图工具的完整 Prompt；不可事后编造")
    reference_assets: list[str] = Field(min_length=1, description="制作 Skill 内实际使用的相对资源路径，例如 assets/character.png")


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
    records = {item["id"]: item for item in catalog.list()}
    mind, production = records.get(mind_id), records.get(production_id)
    if not mind or not production:
        raise ValueError("双 Skill 未安装或记录缺失。")
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
    return SkillPair(**{role: SkillVersion(**{key: record[key] for key in SkillVersion.model_fields})
                        for role, record in (("mind", mind), ("production", production))})


def freeze_pair(catalog: ProducerSkillCatalog, pair: SkillPair, directory: Path) -> list[tuple[str, Path]]:
    current = snapshot_pair(catalog, pair.mind.id, pair.production.id)
    if current != pair:
        raise ValueError("双 Skill 注册版本与 Run 输入快照不一致，拒绝静默升级。")
    refs = []
    for role in ("mind", "production"):
        version = getattr(pair, role)
        target = directory / "skills" / role
        shutil.copytree(catalog.locate(version.id), target)
        if _digest(target) != version.digest:
            raise ValueError("冻结 Skill 时文件发生变化。")
        refs.append((version.name, target / "SKILL.md"))
    return refs


def pair_prompt(pair: SkillPair, refs: list[tuple[str, Path]]) -> str:
    return (
        "你处于 CreatorOS 双 Skill receipt mode。本次为一篇完整图片轮播，不是安装/修改 Skill。\n"
        f"1. 使用 ${pair.mind.name}（{refs[0][1]}），先研究权威来源，设计完整逐页 PageSpec。\n"
        f"2. 使用 ${pair.production.name}（{refs[1][1]}），将既定 PageSpec 转成逐页生图 Prompt。\n"
        "必须读取两个完整 SKILL.md，依阶段遵守各自约束，相对路径分别基于对应 Skill 目录。\n"
        "3. 下游 Skill 的终点是 Prompt，但本次任务还有显式第三步：真实调用图像生成工具，"
        "每页分别生图，把 skills/production/assets/character.png 实际作为参考图片输入。"
        "先查看参考图；不能只在 Prompt 中写路径却不传参考图。禁止用代码/HTML截图冒充生图。\n"
        "图片使用不透明白色/暖白背景，调用生图工具时显式设置 transparent_background=false；不是透明贴纸。\n"
        "默认 6–12 页，首次尽量 6 页；选题过宽时聚焦受众必需的核心机制与工程边界，"
        "不要为了少字丢失事实条件。页数在内容阶段确定，制作阶段不随意增删。\n"
        "receipt.pages 逐页保留原 PageSpec、真正提交工具的 image_prompt、实际参考资源相对路径。"
        "这些字段必须来自生产过程，不能在生图后编造。如果工具内部重写 Prompt，不猜测重写内容。\n"
        "保留 Research Brief 与因果学习链。只有全部图片完成后才返回最终 JSON；"
        "cards.source_image_path 必须是本次 thread 图片工具返回的真实绝对路径。"
        "不要返回制作中或占位图片；不要发布。只读工作区，不写最终 Manifest，宿主会保存回执和产物。\n"
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
