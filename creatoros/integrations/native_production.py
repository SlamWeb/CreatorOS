"""One selected-Skill conversation; host-owned, validated file deliveries.

The model chooses intermediate content formats. Only the final file index is a
contract; it is not a second teaching schema or an image-quality grader.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Literal

from PIL import Image
from pydantic import Field

from .codex import (CODEX_EFFORT, CODEX_MODEL, PRODUCTION_RULES, SESSION_FILENAME,
                    CodexProducerError, CodexUsage, ProducedPack, ProductionModel,
                    ProductionSession, _bounded_sdk, _production_client)
from .producer_skills import ProducerSkillCatalog, _digest, freeze_skill
from .production_progress import ProgressWriter, collect_observed_turn
from .codex_turn_guard import require_completed_turn
from .visual_production import atomic_json, input_digest
from .worker_protocol import record_task, record_thread, completed_delivery_turn

CHECKPOINT = "native_checkpoint.json"

COMPOSITION_RULES = (
    "Mind 负责内容范围、事实、解释与必须保留的教学关系；制作 Skill 负责 IP、画风与呈现方法。"
    "内容单元不等于图片页数。分页、分格和文字密度综合内容、视觉与栏目约定决定。"
    "当前请求和最新返工的明确要求优先于 Skill 默认偏好；默认六格等可适配，"
    "不能为了版式静默删内容、改变含义或丢掉双语等明确要求。"
    "不要把所有 Skill 条款降格为偏好；两个未获用户覆盖的硬要求确实互斥时才提问。"
    "职责边界不等于全流程禁令：Mind 自选 PageSpec 合法，宿主只是不强制；"
    "制作 Skill 若只负责 Prompt，可由当前生产任务接着生图，除非用户或 Skill 明确禁止本次生图。"
)


class CompositionReview(ProductionModel):
    status: Literal["ready", "needs_input"]
    note: str = Field(min_length=1, max_length=3000)


class Artifact(ProductionModel):
    order: int = Field(ge=1)
    source_image_path: str = Field(min_length=1)
    image_prompt: str = Field(min_length=1)
    reference_assets: list[str] = Field(default_factory=list)
    content_file: str | None = None


class Delivery(ProductionModel):
    title: str = ""
    text: str = ""
    hashtags: list[str] = Field(default_factory=list)
    complete: bool = False
    artifacts: list[Artifact] = Field(default_factory=list)


class SavedArtifact(Artifact):
    image_path: str
    sha256: str
    content: str = ""
    warnings: list[str] = Field(default_factory=list)


class Checkpoint(ProductionModel):
    protocol: Literal["native-v1"] = "native-v1"
    input_digest: str
    thread_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    skill_digests: dict[str, str]
    pages: list[SavedArtifact] = Field(default_factory=list)
    delivery: Delivery = Field(default_factory=Delivery)
    turn_completed: bool = False
    repair_attempted: bool = False
    usage: CodexUsage = Field(default_factory=CodexUsage)
    composition_review: CompositionReview | None = None
    prompt_provenance: Literal["producer_reported_not_image_service_verified"] = "producer_reported_not_image_service_verified"


class NativeProgress(ProgressWriter):
    """SDK usage is thread-cumulative; Run accounting must be per Attempt."""
    def __init__(self, directory: Path, checkpoint: Checkpoint | None):
        super().__init__(directory, "production")
        self.checkpoint = checkpoint
        self.baseline = checkpoint.usage.model_copy() if checkpoint else CodexUsage()

    def record_usage(self, usage: dict):
        total = CodexUsage.model_validate(usage)
        self.checkpoint.usage = total
        atomic_json(self.directory / CHECKPOINT, self.checkpoint)
        super().record_usage({key: max(0, getattr(total, key) - getattr(self.baseline, key))
                              for key in CodexUsage.model_fields})


def safe_file(root: Path, path: Path, limit: int | None = None) -> Path:
    root, path = root.resolve(), path.absolute()
    if not path.resolve().is_relative_to(root) or any(p.is_symlink() for p in [path, *path.parents] if p != root):
        raise ValueError("文件越界或为链接。")
    if not path.is_file() or (limit is not None and path.stat().st_size > limit):
        raise ValueError("文件缺失或过大。")
    return path


def skill_refs(directory: Path) -> list[tuple[str, Path]]:
    return [(p.parent.name, p) for p in sorted((directory / "skills").glob("*/SKILL.md"))]


def request_digest(directory: Path) -> str:
    refs = skill_refs(directory)
    if not refs:
        raise ValueError("缺少本次冻结的 Skill。")
    request = safe_file(directory, directory / "production_request.txt", 2_000_000)
    return input_digest(request.read_text(encoding="utf-8"), refs)


def verified_pages(directory: Path, checkpoint: Checkpoint) -> list[SavedArtifact]:
    if checkpoint.composition_review and checkpoint.composition_review.status != "ready" and checkpoint.pages:
        raise ValueError("Skill 组合尚待澄清，不能接受图片交付。")
    orders = [p.order for p in checkpoint.pages]
    if orders != list(range(1, len(orders) + 1)) or len({p.sha256 for p in checkpoint.pages}) != len(orders):
        raise ValueError("图片顺序或重复内容无效。")
    for page in checkpoint.pages:
        path = safe_file(directory / "partial-images", Path(page.image_path))
        if hashlib.sha256(path.read_bytes()).hexdigest() != page.sha256:
            raise ValueError("已保存图片发生变化。")
        with Image.open(path) as image:
            image.verify()
        for ref in page.reference_assets:
            safe_file(directory / "skills", directory / ref)
    return checkpoint.pages


def load_checkpoint(directory: Path, digest: str | None = None) -> Checkpoint | None:
    try:
        path = safe_file(directory, directory / CHECKPOINT, 8_000_000)
        value = Checkpoint.model_validate_json(path.read_text(encoding="utf-8"))
        current = request_digest(directory)
        if value.input_digest != current or (digest is not None and digest != current):
            return None
        if value.skill_digests != {role: _digest(path.parent) for role, path in skill_refs(directory)}:
            return None
        verified_pages(directory, value)
        return value
    except (ValueError, OSError):
        return None


def recover_checkpoint(directory: Path, digest: str, generated_root: Path | None = None) -> Checkpoint | None:
    if not re.fullmatch(r"attempt-\d{3}", directory.name):
        return None
    for previous in sorted(directory.parent.glob("attempt-[0-9][0-9][0-9]"), reverse=True):
        if previous.name >= directory.name or not (previous / CHECKPOINT).exists():
            continue
        value = load_checkpoint(previous, digest)
        if value is None:
            raise ValueError("上次生产输入或证据变化，拒绝自动恢复。")
        (directory / "partial-images").mkdir(exist_ok=True)
        for page in value.pages:
            target = directory / "partial-images" / Path(page.image_path).name
            shutil.copy2(page.image_path, target)
            page.image_path = str(target.resolve())
        # Preserve this Revision's original working notes, not other task memory.
        if (previous / "work").is_dir():
            for source in (previous / "work").rglob("*"):
                if source.is_symlink():
                    raise ValueError("恢复工作文件不能包含链接。")
                if source.is_file():
                    safe_file(previous / "work", source, 8_000_000)
                    target = directory / "work" / source.relative_to(previous / "work")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
        # Rebase only the verified index to the new cwd; retain raw prior index
        # for a producer to recover any not-yet-verified delivery on request.
        prior_index = directory / "work/delivery.json"
        if prior_index.is_file():
            shutil.copy2(prior_index, directory / "work/previous-delivery.json")
        if value.turn_completed:
            value.delivery.artifacts = [Artifact(**p.model_dump(include=set(Artifact.model_fields))) for p in value.pages]
            atomic_json(prior_index, value.delivery)
        elif prior_index.is_file():
            # Latest work may be newer than the preview. Preserve and revalidate it,
            # rebasing only paths inside the exact prior attempt, never arbitrary text.
            try:
                latest = Delivery.model_validate_json(prior_index.read_text(encoding="utf-8-sig"))
                for item in latest.artifacts:
                    item.reference_assets = [Path(ref).relative_to(previous).as_posix()
                        if Path(ref).is_absolute() and Path(ref).is_relative_to(previous) else ref
                        for ref in item.reference_assets]
                    if item.content_file and Path(item.content_file).is_absolute():
                        item.content_file = Path(item.content_file).relative_to(previous / "work").as_posix()
                atomic_json(prior_index, latest)
                if generated_root is not None:
                    ingest(directory, value, generated_root)
                if (generated_root is not None and value.delivery.complete and value.pages
                        and completed_delivery_turn(previous, value.thread_id)):
                    value.turn_completed = True
                    shutil.copy2(previous / "worker_receipt.json", directory / "worker_receipt.json")
            except (ValueError, OSError):
                pass  # Keep the last verified preview; the worker can repair the index.
        atomic_json(directory / CHECKPOINT, value)
        return value
    return None


def ingest(directory: Path, checkpoint: Checkpoint, generated_root: Path) -> Delivery:
    path = safe_file(directory / "work", directory / "work/delivery.json", 8_000_000)
    delivery = Delivery.model_validate_json(path.read_text(encoding="utf-8-sig"))
    generated_thread = generated_root / checkpoint.thread_id
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", checkpoint.thread_id) or not generated_thread.resolve().is_relative_to(generated_root.resolve()):
        raise ValueError("生成 thread 目录越界。")
    if [p.order for p in delivery.artifacts] != list(range(1, len(delivery.artifacts) + 1)):
        raise ValueError("交付图片必须从 1 连续编号。")
    if len(delivery.artifacts) < len(checkpoint.pages):
        raise ValueError("交付不能移除已保存图片。")
    # While the worker is running these are previews, not immutable deliverables.
    # Validate the whole new index before publishing it, retaining previous images
    # by hash so a failed/partial write cannot corrupt a visible checkpoint.
    pages = []
    for item in delivery.artifacts:
        refs = []
        for ref in item.reference_assets:
            candidate = Path(ref) if Path(ref).is_absolute() else directory / ref
            refs.append(safe_file(directory / "skills", candidate).relative_to(directory).as_posix())
        content, content_file = "", None
        if item.content_file:
            file = Path(item.content_file)
            file = file if file.is_absolute() else directory / "work" / file
            file = safe_file(directory / "work", file, 2_000_000)
            content = file.read_text(encoding="utf-8-sig")
            content_file = file.relative_to(directory / "work").as_posix()
        source = safe_file(generated_thread, Path(item.source_image_path))
        if source.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise ValueError("不支持的图片格式。")
        with Image.open(source) as image:
            image.verify()
        sha = hashlib.sha256(source.read_bytes()).hexdigest()
        if sha in {p.sha256 for p in pages}:
            raise ValueError("多个页重复交付同一图片。")
        target = directory / "partial-images" / f"{item.order:02d}-{sha}{source.suffix.lower()}"
        target.parent.mkdir(exist_ok=True)
        page = SavedArtifact(**{**item.model_dump(), "reference_assets": refs, "content_file": content_file},
                             image_path=str(target.resolve()), sha256=sha, content=content)
        if item.order <= len(checkpoint.pages):
            saved = checkpoint.pages[item.order - 1]
            # Reuse previously checked local bytes when the source is unchanged.
            if page.model_dump(exclude={"image_path", "warnings"}) == saved.model_dump(exclude={"image_path", "warnings"}):
                page = saved
            elif checkpoint.turn_completed:
                raise ValueError("最终交付已冻结，不能隐式修改图片、Prompt、引用或内容。")
        if not Path(page.image_path).exists():
            shutil.copy2(source, page.image_path)
        if hashlib.sha256(Path(page.image_path).read_bytes()).hexdigest() != sha:
            raise ValueError("已保存图片发生变化。")
        pages.append(page)
    if checkpoint.turn_completed and delivery != checkpoint.delivery:
        raise ValueError("最终交付已冻结，不能修改交付索引。")
    changed = [p.order for p in pages if p.order > len(checkpoint.pages)
               or p != checkpoint.pages[p.order - 1]]
    if changed:
        with (directory / "codex_trace.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"type": "delivery.updated", "orders": changed,
                                    "at": datetime.now().astimezone().isoformat()}) + "\n")
    checkpoint.pages = pages
    checkpoint.delivery = delivery
    atomic_json(directory / CHECKPOINT, checkpoint)
    return delivery


def evidence_files(directory: Path, composition=None) -> list[Path]:
    value = load_checkpoint(directory)
    if value is None or not value.turn_completed or not value.delivery.complete or not value.pages:
        raise ValueError("原生生产证据不完整。")
    if len(value.delivery.artifacts) != len(value.pages):
        raise ValueError("最终图片与交付索引不一致。")
    if composition is not None and value.skill_digests != {
            role: getattr(composition, role).digest for role in ("mind", "production")}:
        raise ValueError("产物与栏目 Skill 版本不一致。")
    return [directory / CHECKPOINT, directory / "production_request.txt",
            *sorted(p for p in (directory / "skills").rglob("*") if p.is_file())]


def delivery_prompt(directory: Path, refs: list[tuple[str, Path]], request: str, checkpoint=None) -> str:
    return (PRODUCTION_RULES + "\n同一会话完成本次内容与呈现，不拆子会话，不主动调用其他 Skill。\n"
            + "\n".join(f"@{name}：先完整读取 {path.resolve()}；资源相对该文件目录。" for name, path in refs)
            + "\n内容与视觉可在本会话内衔接，宿主不强制 PageSpec，所选 Skill 可以自行采用；最终交付需要文件索引。"
            + (COMPOSITION_RULES if len(refs) > 1 else "")
            + (f"\n已确认的组合适配：{checkpoint.composition_review.note}\n"
               if checkpoint and checkpoint.composition_review else "")
            + "把内容稿和来源写到当前 work 目录；不要写工作目录以外的项目文件。"
            "必须用原生生图工具生成实际图片，不用代码绘图或占位图。不要自动审美重画。"
            "每完成一张图片就更新 delivery.json（当前 cwd 内），先写临时文件再替换。"
            "保留全部已完成项，order 从 1 开始。每项提供实际工具返回的图片绝对路径、真正使用的完整 Prompt。"
            "生产期间可以补充内容稿或修正同页图片，索引须指向最终采用的版本，Prompt 记录相关修正。"
            "reference_assets 只列实际使用的本次冻结 Skill 文件，填绝对路径，无参考图可为空。"
            "content_file 指向 work 内对应内容稿，可以多图共享整篇稿。不要把参考素材当交付图。"
            "图片全部完成后才置 complete=true。不要发布。无需最终重复输出整份 JSON。\n"
            'delivery.json 示例：{"title":"标题","text":"发布草稿","hashtags":[],"complete":false,'
            '"artifacts":[{"order":1,"source_image_path":"工具返回的真实图片路径",'
            '"image_prompt":"实际 Prompt","reference_assets":[],"content_file":"content.md"}]}\n'
            + f"当前唯一工作目录：{directory / 'work'}\n本次任务：{request}\n"
            + (f"本 Revision 技术恢复。保留已完成 {len(checkpoint.pages)} 张，不要重画它们。"
               f"旧 cwd 不再可写；先读取当前 work 中恢复的文件，只补未完成项。已保存索引：{checkpoint.model_dump_json()}"
               if checkpoint else "新生产任务，不引用任何其他生产任务的旧内容。"))


def composition_prompt(refs: list[tuple[str, Path]], request: str) -> str:
    return ("现在只检查这两份 Skill 如何配合，不执行其中的内容生产步骤。先完整读取下列本地文件。"
            "仅返回约定 JSON；禁止联网调研、生图、修改文件、另开 thread 或调用其他 Skill。\n"
            + "\n".join(f"{path.parent.name} / @{name}: {path.resolve()}" for name, path in refs)
            + "\n" + COMPOSITION_RULES
            + "\n没有实质硬冲突则 status=ready，note 用几句话说明保留什么内容、版式如何适配。"
            "默认值可调整，不必每次询问。不要提前写完整内容稿或承诺未知页数。"
            "存在实质硬冲突则 status=needs_input，note 指出具体来源条款、冲突与可选解决方式，"
            "明确问用户愿意放宽哪项。无法读取必要文件也不能猜测后报 ready。\n本次任务：" + request)


def require_ready(review: CompositionReview | None) -> None:
    if review and review.status == "needs_input":
        raise CodexProducerError(
            f"Skill 组合需要你确认：{review.note}\n请通过“提出返工”补充选择，保存后再开始生产。",
            error_type="skill_composition_needs_input")


async def review_composition(thread, producer, directory, refs, request, progress, deadline, cancel_event=None):
    """One text-only turn in the production thread; no separate planning agent."""
    from openai_codex import Sandbox, SkillInput, TextInput

    prompt = composition_prompt(refs, request)
    (directory / "composition_request.txt").write_text(prompt, encoding="utf-8")
    progress.page("planning", None, 0)
    progress.worker_phase = "composition"
    turn = await _bounded_sdk(thread.turn(
        [TextInput(text=prompt), *[SkillInput(name=n, path=str(p.resolve())) for n, p in refs]],
        cwd=str(directory / "work"), effort=CODEX_EFFORT, model=CODEX_MODEL,
        output_schema=CompositionReview.model_json_schema(), sandbox=Sandbox.read_only), deadline, cancel_event)
    try:
        result = await _bounded_sdk(collect_observed_turn(turn, progress), deadline, cancel_event)
    except BaseException:
        try:
            await asyncio.wait_for(turn.interrupt(), timeout=5)
        except Exception:
            pass
        raise
    require_completed_turn(result)
    if result.usage is not None:
        progress.record_usage(producer._sdk_usage(result.usage).model_dump())
    text = result.final_response or ""
    (directory / "composition_response.txt").write_text(text, encoding="utf-8")
    try:
        review = CompositionReview.model_validate_json(text)
    except ValueError as error:
        raise CodexProducerError("Skill 组合检查未返回有效结论，尚未进入生产。",
                                 error_type="skill_composition_invalid") from error
    progress.checkpoint.composition_review = review
    atomic_json(directory / CHECKPOINT, progress.checkpoint)
    (directory / "composition_review.md").write_text(f"# {review.status}\n\n{review.note}\n", encoding="utf-8")
    with (directory / "codex_trace.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"stage": "production", "type": "composition.reviewed", "status": review.status,
                                 "thread_id": thread.id, "at": datetime.now().astimezone().isoformat()}) + "\n")
    return review


async def execute(producer, directory, refs, request, checkpoint, on_thread_started, cancel_event):
    from openai_codex import ApprovalMode, Sandbox, SkillInput, TextInput
    progress = NativeProgress(directory, checkpoint)
    deadline = monotonic() + producer.timeout_seconds
    try:
        require_ready(checkpoint.composition_review if checkpoint else None)
        async with _production_client(deadline, cancel_event) as client:
            options = dict(approval_mode=ApprovalMode.deny_all, cwd=str(directory / "work"),
                           model=CODEX_MODEL, sandbox=Sandbox.workspace_write, developer_instructions=PRODUCTION_RULES)
            thread = await _bounded_sdk(client.thread_resume(checkpoint.thread_id, **options) if checkpoint else
                                        client.thread_start(**options), deadline, cancel_event)
            recovered = checkpoint
            checkpoint = checkpoint or Checkpoint(input_digest=request_digest(directory), thread_id=thread.id,
                skill_digests={role: _digest(path.parent) for role, path in skill_refs(directory)})
            progress.checkpoint = checkpoint
            record_thread(directory, thread.id)
            atomic_json(directory / CHECKPOINT, checkpoint)
            if on_thread_started:
                on_thread_started(thread.id)
            with (directory / "codex_trace.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"type": "thread.resumed" if recovered else "thread.started",
                                         "thread_id": thread.id, "model": CODEX_MODEL,
                                         "reasoning_effort": CODEX_EFFORT}) + "\n")
            # Existing image-bearing checkpoints predate this check and keep their
            # original recovery path. Otherwise freeze the decision once per Revision.
            if len(refs) > 1 and checkpoint.composition_review is None and not checkpoint.pages:
                require_ready(await review_composition(thread, producer, directory, refs, request,
                                                       progress, deadline, cancel_event))
            prompt = delivery_prompt(directory, refs, request, checkpoint if recovered else None)
            if not recovered and checkpoint.composition_review:
                prompt += f"\n已确认的组合适配：{checkpoint.composition_review.note}\n"
            progress.page("planning", None, len(checkpoint.pages))
            (directory / "production_instructions.txt").write_text(prompt, encoding="utf-8")
            for repair in (False, True):
                progress.worker_phase = "delivery_repair" if repair else "production"
                if repair:
                    if checkpoint.repair_attempted:
                        raise ValueError("交付索引的一次修复额度已用完。")
                    checkpoint.repair_attempted = True
                    atomic_json(directory / CHECKPOINT, checkpoint)
                    prompt = ("只修复当前 work/delivery.json 的文件索引，不得调用生图工具，不得重新研究或重画。"
                              "根据已经产生的真实图片和实际 Prompt 补齐索引，无法恢复则说明失败。\n" + str(delivery_error))
                inputs = [TextInput(text=prompt), *[SkillInput(name=n, path=str(p.resolve())) for n, p in refs]]
                turn = await _bounded_sdk(thread.turn(inputs, cwd=str(directory / "work"), effort=CODEX_EFFORT,
                                                       model=CODEX_MODEL, sandbox=Sandbox.workspace_write), deadline, cancel_event)
                collector = asyncio.create_task(collect_observed_turn(turn, progress))
                try:
                    while not collector.done():
                        if cancel_event is not None and cancel_event.is_set():
                            raise CodexProducerError("生产已取消，保留已保存文件。", error_type="codex_interrupted")
                        if monotonic() >= deadline:
                            raise CodexProducerError("生产超时，保留已保存文件。", error_type="codex_timeout")
                        try:
                            before = len(checkpoint.pages)
                            ingest(directory, checkpoint, producer.generated_images_root)
                            if len(checkpoint.pages) != before:
                                progress.page("rendering", None, len(checkpoint.pages))
                        except (ValueError, OSError):
                            pass  # incomplete writes are not a production failure
                        await asyncio.wait({collector}, timeout=0.5)
                    result = await collector  # SDK failed turn must never look completed
                    require_completed_turn(result)
                    if result.usage is not None:
                        progress.record_usage(producer._sdk_usage(result.usage).model_dump())
                    (directory / ("production_repair_response.txt" if repair else "production_response.txt")).write_text(
                        result.final_response or "", encoding="utf-8")
                finally:
                    if not collector.done():
                        try:
                            await asyncio.wait_for(turn.interrupt(), timeout=5)
                        except Exception:
                            pass
                        collector.cancel()
                        await asyncio.gather(collector, return_exceptions=True)
                    try:
                        ingest(directory, checkpoint, producer.generated_images_root)
                    except (ValueError, OSError):
                        pass
                try:
                    delivery = ingest(directory, checkpoint, producer.generated_images_root)
                    if not delivery.complete or not checkpoint.pages:
                        raise ValueError("图片索引未声明完整交付。")
                    checkpoint.turn_completed = True
                    atomic_json(directory / CHECKPOINT, checkpoint)
                    progress.state.total_pages = len(checkpoint.pages)
                    progress.page("assembling", None, len(checkpoint.pages))
                    progress.finish("completed")
                    return checkpoint
                except (ValueError, OSError) as error:
                    delivery_error = error
            raise ValueError(f"交付索引修复后仍无效：{delivery_error}")
    except Exception as error:
        progress.finish("interrupted" if getattr(error, "error_type", "") == "codex_interrupted" else "failed")
        if isinstance(error, CodexProducerError):
            raise
        raise CodexProducerError(f"单会话生产失败；已验收文件保留：{error}", error_type="native_delivery_failed") from error


def produce_native(producer, **request):
    from ..content import CarouselCard, PublicationCopy, SocialContentPack
    from ..content.models import MANIFEST_FILENAME
    from .skill_pair import freeze_pair
    if not producer.native_skill_inputs:
        raise CodexProducerError("原生文件生产需要 Python SDK Producer。", error_type="unsupported_native_production")
    directory = Path(request["directory"]).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "work").mkdir()
    pair = request.get("composition")
    if pair is not None:
        snapshot = directory.parent.parent / "skill-snapshot"
        refs = freeze_pair(ProducerSkillCatalog(request.get("skills_root") or producer.project_root / "data/producer-skills",
                                               producer.project_root), pair, directory,
                           source_root=snapshot if snapshot.is_dir() else None)
        name = pair.production.id
    else:
        from ..skills.loader import SkillLoader
        name = request.get("skill_name")
        source = request.get("skill_directory") or producer._resolve_skill_dir(name, request.get("skills_root"))
        freeze_skill(source, directory / "skills/single", request.get("skill_digest") or _digest(source))
        skill_file = directory / "skills/single/SKILL.md"
        selected = next(s for s in SkillLoader([skill_file.parent]).discover() if s.path == skill_file)
        refs = [(selected.name, skill_file)]
    payload = {key: request.get(key) for key in ("pack_id", "creator_id", "series_id", "topic_id", "topic_title",
               "topic_brief", "series_description", "audience", "revision_instruction", "previous_pages")}
    prompt = json.dumps(payload, ensure_ascii=False)
    (directory / "production_request.txt").write_text(prompt, encoding="utf-8")
    record_task(directory, kind="production", scope={key: payload[key] for key in
                ("pack_id", "creator_id", "series_id", "topic_id")},
                input_ref="production_request.txt", deliverable="work/delivery.json")
    checkpoint = recover_checkpoint(directory, request_digest(directory), producer.generated_images_root)
    if checkpoint is None or not (checkpoint.turn_completed and checkpoint.delivery.complete):
        checkpoint = asyncio.run(execute(producer, directory, refs, prompt, checkpoint,
                                        request.get("on_thread_started"), request.get("cancel_event")))
    elif request.get("on_thread_started"):
        request["on_thread_started"](checkpoint.thread_id)
    cancel = request.get("cancel_event")
    if cancel is not None and cancel.is_set():
        raise CodexProducerError("生产已取消。", error_type="codex_interrupted")
    verified_pages(directory, checkpoint)
    (directory / "images").mkdir()
    cards = []
    for page in checkpoint.pages:
        target = directory / "images" / f"{page.order:02d}{Path(page.image_path).suffix}"
        shutil.copy2(page.image_path, target)
        cards.append(CarouselCard(order=page.order, kind="cover" if page.order == 1 else "content",
                                  headline=checkpoint.delivery.title or payload["topic_title"], body=page.content or None,
                                  visual_brief=page.image_prompt, image_path=target.relative_to(directory).as_posix()))
    now = datetime.now().astimezone().isoformat()
    title = checkpoint.delivery.title or payload["topic_title"]
    text = checkpoint.delivery.text or f"{title}（待编辑发布文案）"
    pack = SocialContentPack(**{k: payload[k] for k in ("pack_id", "creator_id", "series_id", "topic_id", "topic_title")},
                             skill_name=name, generated_at=now, content_summary=title, cards=cards,
                             publish_copy=PublicationCopy(title=title, body=text,
                                 hashtags=[tag for tag in checkpoint.delivery.hashtags if tag.strip()]))
    (directory / MANIFEST_FILENAME).write_text(pack.model_dump_json(indent=2), encoding="utf-8")
    session = ProductionSession(thread_id=checkpoint.thread_id, pack_id=payload["pack_id"], created_at=now,
        usage=CodexUsage.model_validate_json((directory / "production_usage.json").read_text(encoding="utf-8"))
              if (directory / "production_usage.json").is_file() else CodexUsage())
    atomic_json(directory / SESSION_FILENAME, session)
    return ProducedPack(directory, pack, session)
