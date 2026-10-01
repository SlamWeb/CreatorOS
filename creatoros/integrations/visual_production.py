"""Single visual conversation, durable page receipts, no automatic paid retries."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Literal

from PIL import Image
from pydantic import Field

from .codex import ProductionModel
from .skill_pair import PairReceipt, StoryboardReceipt, VisualPage


class VisualPlan(ProductionModel):
    pages: list[VisualPage] = Field(min_length=1)


class RenderedPage(VisualPage):
    source_image_path: str = Field(min_length=1)
    warnings: list[str]


class SavedPage(ProductionModel):
    order: int = Field(ge=1)
    image_path: str
    source_image_path: str
    source_thread_id: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    image_prompt: str = Field(min_length=1)
    reference_assets: list[str] = Field(min_length=1)
    warnings: list[str]
    attempts: int = Field(ge=1, le=2)


class VisualCheckpoint(ProductionModel):
    schema_version: Literal[1] = 1
    input_digest: str
    storyboard: StoryboardReceipt
    plan: VisualPlan
    visual_thread_id: str
    pages: list[SavedPage]


def atomic_json(path: Path, value: ProductionModel) -> None:
    temporary = path.with_suffix(".json.tmp")
    if path.is_symlink() or temporary.is_symlink():
        raise ValueError("checkpoint 写入路径不能是符号链接。")
    temporary.write_text(value.model_dump_json(indent=2), encoding="utf-8")
    os.replace(temporary, path)


def input_digest(prompt: str, skills: list[tuple[str, Path]]) -> str:
    from .producer_skills import _digest
    payload = [prompt, *[_digest(path.parent) for _, path in skills]]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()


def normalize_refs(refs: list[str], skill_root: Path) -> list[str]:
    """Accept only two explicit bases, proving each refers to a frozen asset."""
    normalized = []
    for ref in refs:
        ref = ref.replace("\\", "/")
        if ref.startswith("skills/production/"):
            ref = ref.removeprefix("skills/production/")
        path = Path(ref)
        target = skill_root / path
        if (path.is_absolute() or ".." in path.parts or ":" in ref or target.is_symlink()
                or not target.resolve().is_relative_to(skill_root.resolve()) or not target.is_file()):
            raise ValueError("角色参考资源缺失或越界。")
        normalized.append(path.as_posix())
    if "assets/character.png" not in normalized:
        raise ValueError("小白角色参考资源缺失。")
    return normalized


def validate_plan(plan: VisualPlan, storyboard: StoryboardReceipt, skill_root: Path) -> VisualPlan:
    if [p.order for p in plan.pages] != [p.order for p in storyboard.pages]:
        raise ValueError("视觉 Prompt 计划必须与内容页一一对应。")
    return VisualPlan(pages=[p.model_copy(update={"reference_assets": normalize_refs(p.reference_assets, skill_root)})
                             for p in plan.pages])


def verified_pages(directory: Path, checkpoint: VisualCheckpoint) -> list[SavedPage]:
    orders = [p.order for p in checkpoint.pages]
    if orders != sorted(set(orders)) or any(n > len(checkpoint.storyboard.pages) for n in orders):
        raise ValueError("逐页 checkpoint 页码重复或越界。")
    root = (directory / "partial-images").resolve()
    if (directory / "partial-images").is_symlink():
        raise ValueError("逐页图片目录为符号链接。")
    if len({p.sha256 for p in checkpoint.pages}) != len(checkpoint.pages):
        raise ValueError("多个页面重复使用同一图片。")
    for page in checkpoint.pages:
        path = Path(page.image_path)
        if (not path.is_absolute() or path.is_symlink() or not path.resolve().is_relative_to(root)
                or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != page.sha256):
            raise ValueError("逐页图片缺失、变化或越界。")
        with Image.open(path) as image:
            image.verify()
    return checkpoint.pages


def load_checkpoint(directory: Path, digest: str) -> VisualCheckpoint | None:
    path = directory / "visual_checkpoint.json"
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size > 4_000_000:
        raise ValueError("checkpoint 无效。")
    value = VisualCheckpoint.model_validate_json(path.read_text(encoding="utf-8"))
    if value.input_digest != digest:
        raise ValueError("恢复输入或 Skill 已变化。")
    original = directory / "storyboard.json"
    plan = directory / "visual_plan.json"
    if (original.is_symlink() or plan.is_symlink()
            or StoryboardReceipt.model_validate_json(original.read_text(encoding="utf-8")) != value.storyboard
            or VisualPlan.model_validate_json(plan.read_text(encoding="utf-8")) != value.plan):
        raise ValueError("checkpoint 的内容或视觉计划与保存原件不一致。")
    markdown = directory / "storyboard.md"
    if (markdown.is_symlink() or markdown.read_text(encoding="utf-8") !=
            "\n\n---\n\n".join(p.page_spec for p in value.storyboard.pages)):
        raise ValueError("checkpoint 的可读内容稿发生变化。")
    verified_pages(directory, value)
    return value


def recover_checkpoint(directory: Path, digest: str) -> VisualCheckpoint | None:
    """Only earlier sibling Attempts in the SAME Revision; never scan other Runs."""
    if not re.fullmatch(r"attempt-\d{3}", directory.name):
        return None
    previous = sorted(directory.parent.glob("attempt-[0-9][0-9][0-9]"), reverse=True)
    for source in previous:
        if source.name >= directory.name or source.is_symlink():
            continue
        value = load_checkpoint(source, digest)
        if value is None:
            continue
        target_root = directory / "partial-images"
        if target_root.is_symlink() or not target_root.resolve().is_relative_to(directory.resolve()):
            raise ValueError("恢复图片目录越界。")
        target_root.mkdir(exist_ok=True)
        copied = []
        for page in value.pages:
            target = target_root / f"{page.order:02d}{Path(page.image_path).suffix.lower()}"
            if target.is_symlink() or not target.resolve().is_relative_to(directory.resolve()):
                raise ValueError("恢复图片文件越界。")
            shutil.copy2(page.image_path, target)
            copied.append(page.model_copy(update={"image_path": str(target.resolve())}))
        value = value.model_copy(update={"pages": copied})
        atomic_json(directory / "visual_plan.json", value.plan)
        atomic_json(directory / "visual_checkpoint.json", value)
        return value
    return None


def save_page(directory: Path, checkpoint: VisualCheckpoint, rendered: RenderedPage,
              generated_root: Path, skill_root: Path, attempts: int) -> SavedPage:
    if rendered.order in {p.order for p in checkpoint.pages}:
        raise ValueError("已保存页面不允许被隐式覆盖。")
    if rendered.order not in {p.order for p in checkpoint.plan.pages}:
        raise ValueError("页面不属于本次计划。")
    path = Path(rendered.source_image_path)
    root = generated_root / checkpoint.visual_thread_id
    if (not path.is_absolute() or path.is_symlink() or root.is_symlink()
            or not path.resolve().is_relative_to(root.resolve()) or not path.is_file()
            or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}):
        raise ValueError("生图源文件不存在或不在本视觉 thread 内。")
    refs = normalize_refs(rendered.reference_assets, skill_root)
    if path.resolve() in {Path(p.source_image_path).resolve() for p in checkpoint.pages}:
        raise ValueError("不同页面不能重复引用同一图片。")
    with Image.open(path) as image:
        image.verify()
    folder = directory / "partial-images"
    if folder.is_symlink() or not folder.resolve().is_relative_to(directory.resolve()):
        raise ValueError("逐页图片目录越界。")
    folder.mkdir(exist_ok=True)
    target = folder / f"{rendered.order:02d}{path.suffix.lower()}"
    if target.is_symlink() or not target.resolve().is_relative_to(folder.resolve()):
        raise ValueError("逐页图片文件越界。")
    shutil.copy2(path, target)
    value = SavedPage(order=rendered.order, image_path=str(target.resolve()),
                      source_image_path=str(path.resolve()), source_thread_id=checkpoint.visual_thread_id,
                      sha256=hashlib.sha256(target.read_bytes()).hexdigest(), image_prompt=rendered.image_prompt,
                      reference_assets=refs, warnings=rendered.warnings, attempts=attempts)
    checkpoint.pages.append(value)
    checkpoint.pages.sort(key=lambda p: p.order)
    atomic_json(directory / "visual_checkpoint.json", checkpoint)
    page_folder = directory / "pages" / f"{value.order:02d}"
    if any(p.is_symlink() for p in (directory / "pages", page_folder)):
        raise ValueError("逐页证据目录越界。")
    page_folder.mkdir(parents=True, exist_ok=True)
    (page_folder / "prompt.txt").write_text(value.image_prompt, encoding="utf-8")
    (page_folder / "content.md").write_text(checkpoint.storyboard.pages[value.order - 1].page_spec, encoding="utf-8")
    return value


def build_receipt(storyboard: StoryboardReceipt, rendered_pages: list[dict], topic_title: str) -> PairReceipt:
    """Display text derives from canonical content, never from image tool receipts."""
    if [p["order"] for p in rendered_pages] != [p.order for p in storyboard.pages]:
        raise ValueError("图片尚未全部交付。")
    cards = []
    for content, rendered in zip(storyboard.pages, rendered_pages):
        match = re.search(r"(?:标题|headline)\s*[：:]\s*([^\n]+)", content.page_spec)
        headline = match.group(1).strip() if match else f"第 {content.order} 页"
        role = re.search(r"(?:角色|role)\s*[：:]\s*(cover|content|synthesis)", content.page_spec)
        kind = {"cover": "cover", "content": "content", "synthesis": "summary"}.get(role.group(1)) if role else None
        cards.append(dict(order=content.order, kind=kind or ("cover" if content.order == 1 else
                          "summary" if content.order == len(storyboard.pages) else "content"),
                          section=None, headline=headline or f"第 {content.order} 页", body=content.page_spec,
                          highlights=[], visual_brief=None, source_image_path=rendered["source_image_path"]))
    sources = []
    combined = storyboard.research_brief + "\n" + "\n".join(p.page_spec for p in storyboard.pages)
    urls = re.findall(r"https?://[^\s\]\)\}>\"，。；]+", combined)
    for url in dict.fromkeys(urls):
        sources.append(dict(source_id=f"source-{len(sources)+1}", title=url, url=url,
                            note="来自内容阶段原始来源，未重新调研。"))
    return PairReceipt.model_validate(dict(
        content_summary=storyboard.research_brief, cards=cards,
        publish_copy=dict(title=topic_title.strip() or "内容草稿",
                          body="发布文案草稿（待人工编辑）\n" + storyboard.research_brief, hashtags=[]),
        sources=sources, research_brief=storyboard.research_brief, causal_chain=storyboard.causal_chain,
        pages=[{**{k: r[k] for k in ("order", "image_prompt", "reference_assets")}, "page_spec": p.page_spec}
               for p, r in zip(storyboard.pages, rendered_pages)],
    ))


def plan_prompt(name: str, storyboard: StoryboardReceipt) -> str:
    return (f"@{name} 用这个视觉 Skill 把下面整套 pages 可视化。先完整读取指定 Skill 并查看角色参考。"
            "本 turn 只定稿整套生图 Prompt，不生图。保持原内容、页序和跨页连续性。"
            "每页返回 order/image_prompt/reference_assets；资源路径以 Skill 文件夹为基准，例如 assets/character.png。"
            "不要填卡片标题、发布文案或再抄 page_spec。稍后会在同一会话按页生成。\n"
            + storyboard.model_dump_json(indent=2))


def render_prompt(page: VisualPage, total: int) -> str:
    return (f"现在仅生成第 {page.order}/{total} 页，整篇关系和风格沿用本会话。"
            "使用下面的定稿 Prompt，实际传入角色参考图，生成不透明图片。只调用一次生图，不为审美自行重画；"
            "有质量疑虑写到 warnings，由用户决定是否返工。不要改其他页，不调研，不写 Manifest。"
            "完成后立刻返回这一页实际的 image_prompt、角色资源相对路径、工具给出的绝对图片路径及 warnings。"
            "不返回标题、发布文案或 page_spec。\n" + page.model_dump_json(indent=2))
