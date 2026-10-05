from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import threading
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory, TemporaryFile
from time import monotonic
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..content import CarouselCard, PublicationCopy, SocialContentPack, SourceRef
from ..content.models import MANIFEST_FILENAME
from .process_tree import ProcessTree

SESSION_FILENAME = "production_session.json"
CODEX_MODEL = "gpt-6-luna"
CODEX_EFFORT = "xhigh"
PRODUCTION_CONFIG = (
    "memories.use_memories=false",
    "memories.generate_memories=false",
    "project_doc_max_bytes=0",
)
PRODUCTION_RULES = (
    "本次是独立内容生产。仅使用当前请求、当前阶段 Skill、其资源及本次明确提供的内容。"
    "不要读取全局记忆、其他任务的会话/产物或项目开发资料。"
)
CardKind = Literal["cover", "content", "summary", "sources", "cta"]


class ProductionModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)


class ProducedCard(ProductionModel):
    order: int = Field(ge=1)
    kind: CardKind
    section: str | None
    headline: str = Field(min_length=1)
    body: str | None
    highlights: list[str]
    visual_brief: str | None
    source_image_path: str = Field(min_length=1)


class ProductionCopy(ProductionModel):
    title: str = Field(min_length=1)
    body: str = Field(min_length=1)
    hashtags: list[str]


class ProductionSource(ProductionModel):
    source_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    url: str | None
    note: str | None


class ProductionReceipt(ProductionModel):
    content_summary: str = Field(min_length=1)
    cards: list[ProducedCard] = Field(min_length=1)
    publish_copy: ProductionCopy
    sources: list[ProductionSource]

    @model_validator(mode="after")
    def validate_card_order(self) -> "ProductionReceipt":
        if [card.order for card in self.cards] != list(range(1, len(self.cards) + 1)):
            raise ValueError("cards 必须按列表顺序从 1 连续编号。")
        return self


class CodexUsage(ProductionModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    input_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_output_tokens: int = Field(default=0, ge=0)


class ProductionSession(ProductionModel):
    schema_version: Literal[1] = 1
    thread_id: str = Field(min_length=1)
    pack_id: str = Field(min_length=1)
    status: Literal["completed"] = "completed"
    created_at: str = Field(min_length=1)
    usage: CodexUsage


@dataclass(frozen=True)
class CodexRun:
    thread_id: str
    receipt: ProductionModel
    usage: CodexUsage


@dataclass(frozen=True)
class ProducedPack:
    directory: Path
    pack: SocialContentPack
    session: ProductionSession


class CodexProducerError(RuntimeError):
    def __init__(self, message: str, *, error_type: str = "codex_producer_error"):
        super().__init__(message)
        self.error_type = error_type


async def _bounded_sdk(operation, deadline: float, cancel_event=None):
    """Bound SDK startup RPCs too, not only the completed turn's stream."""
    task = asyncio.ensure_future(operation)
    try:
        while not task.done():
            if cancel_event is not None and cancel_event.is_set():
                raise CodexProducerError("本地执行器已停止生产。", error_type="codex_interrupted")
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise CodexProducerError("Codex SDK 请求超时。", error_type="codex_timeout")
            await asyncio.wait({task}, timeout=min(0.1, remaining))
        return await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@asynccontextmanager
async def _production_client(deadline: float, cancel_event=None, *,
                             config_overrides=PRODUCTION_CONFIG, codex_bin=None):
    from openai_codex import AsyncCodex, CodexConfig
    client = AsyncCodex(CodexConfig(config_overrides=config_overrides, codex_bin=codex_bin))
    try:
        await _bounded_sdk(client.__aenter__(), deadline, cancel_event)
        yield client
    finally:
        # Failed __aenter__ would not trigger a normal async-with __aexit__.
        # Close explicitly so the SDK wakes its synchronous RPC waiters.
        await asyncio.wait_for(client.__aexit__(None, None, None), timeout=5)


def parse_codex_jsonl(stdout: str, *, fallback_thread_id: str = "", receipt_model=ProductionReceipt) -> CodexRun:
    thread_id = fallback_thread_id
    final_text = ""
    usage = CodexUsage()
    failure = ""
    recoverable_errors: list[str] = []
    turn_completed = False
    for raw_line in stdout.splitlines():
        if not raw_line.strip():
            continue
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError as error:
            raise CodexProducerError(
                f"Codex JSONL 无法解析：{error}", error_type="codex_protocol_error"
            ) from error
        event_type = event.get("type")
        if event_type == "thread.started":
            thread_id = str(event.get("thread_id") or "")
        elif event_type == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message":
                final_text = str(item.get("text") or "")
        elif event_type == "turn.completed":
            usage = CodexUsage.model_validate(event.get("usage") or {})
            turn_completed = True
        elif event_type == "turn.failed":
            failure = str(event.get("message") or event.get("error") or event)
        elif event_type == "error":
            recoverable_errors.append(str(event.get("message") or event))
    if failure:
        raise CodexProducerError(f"Codex 生产失败：{failure}", error_type="codex_turn_failed")
    if not turn_completed and recoverable_errors:
        raise CodexProducerError(
            f"Codex 生产失败：{recoverable_errors[-1]}", error_type="codex_turn_failed"
        )
    if not thread_id or not final_text or (receipt_model is not ProductionReceipt and not turn_completed):
        raise CodexProducerError("Codex 未返回 thread_id 或最终生产回执。", error_type="codex_protocol_error")
    try:
        receipt = receipt_model.model_validate_json(final_text)
    except Exception as error:
        raise CodexProducerError(
            f"Codex 生产回执不符合约定：{error}", error_type="invalid_production_receipt"
        ) from error
    return CodexRun(thread_id, receipt, usage)


class CodexProducer:
    receipt_model = ProductionReceipt

    def __init__(
        self,
        *,
        project_root: Path,
        generated_images_root: Path,
        executable: str = "codex",
        timeout_seconds: float = 1_800,
    ):
        self.project_root = Path(project_root).resolve()
        self.generated_images_root = Path(generated_images_root).resolve()
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        self.native_skill_inputs = getattr(type(self), "native_skill_inputs", False)

    @classmethod
    def from_defaults(cls) -> "CodexProducer":
        from ..config import CODEX_PRODUCER_TIMEOUT_SECONDS, PROJECT_ROOT
        from .codex_executable import default_codex_executable

        codex_home = Path(os.getenv("CODEX_HOME") or Path.home() / ".codex")
        return cls(
            project_root=PROJECT_ROOT,
            generated_images_root=codex_home / "generated_images",
            executable=default_codex_executable(),
            timeout_seconds=CODEX_PRODUCER_TIMEOUT_SECONDS,
        )

    def produce(
        self,
        *,
        creator_id: str,
        series_id: str,
        topic_id: str,
        topic_title: str,
    ) -> ProducedPack:
        now = datetime.now().astimezone()
        pack_id = f"{creator_id}-{series_id}-{topic_id}-{now:%Y%m%d-%H%M%S}"
        directory = self.project_root / "outputs" / creator_id / series_id / pack_id
        return self.produce_to(
            directory=directory,
            pack_id=pack_id,
            creator_id=creator_id,
            series_id=series_id,
            topic_id=topic_id,
            topic_title=topic_title,
        )

    def produce_to(
        self,
        *,
        directory: Path,
        pack_id: str,
        creator_id: str,
        series_id: str,
        topic_id: str,
        topic_title: str,
        topic_brief: str | None = None,
        series_description: str = "",
        audience: str = "",
        skill_name: str | None = "knowledge-to-carousel",
        skill_directory: Path | None = None,
        skill_digest: str | None = None,
        composition=None,
        skills_root: Path | None = None,
        thread_id: str | None = None,
        revision_instruction: str | None = None,
        previous_pages: str | None = None,
        on_thread_started: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
        on_process_started: Callable[[dict], None] | None = None,
        on_process_stopped: Callable[[], None] | None = None,
        production_protocol: Literal["legacy", "native-v1"] = "legacy",
    ) -> ProducedPack:
        if production_protocol == "native-v1":
            from .native_production import produce_native
            return produce_native(self, directory=directory, pack_id=pack_id,
                creator_id=creator_id, series_id=series_id, topic_id=topic_id, topic_title=topic_title,
                topic_brief=topic_brief, series_description=series_description, audience=audience,
                skill_name=skill_name, skill_directory=skill_directory, skill_digest=skill_digest,
                composition=composition, skills_root=skills_root, revision_instruction=revision_instruction,
                previous_pages=previous_pages, on_thread_started=on_thread_started, cancel_event=cancel_event)
        now = datetime.now().astimezone()
        directory = Path(directory).resolve()
        directory.mkdir(parents=True, exist_ok=False)
        skill_refs = None
        if composition is not None:
            from .producer_skills import ProducerSkillCatalog
            from .skill_pair import PairReceipt, freeze_pair
            if not self.native_skill_inputs:
                raise CodexProducerError("双 Skill 生产需要 SDK Producer。", error_type="unsupported_skill_pair")
            skill_refs = freeze_pair(ProducerSkillCatalog(
                skills_root or self.project_root / "data" / "producer-skills", self.project_root), composition, directory,
                source_root=(directory.parent.parent / "skill-snapshot")
                if (directory.parent.parent / "skill-snapshot").is_dir() else None)
            self.receipt_model = PairReceipt
            skill_dir = skill_refs[1][1].parent
            skill_name = composition.production.id
            prompt = json.dumps({
                "creator_id": creator_id, "series_id": series_id, "topic_id": topic_id,
                "topic_title": topic_title, "topic_brief": topic_brief,
                "series_description": series_description, "audience": audience,
                "revision_instruction": revision_instruction,
                "previous_pages": previous_pages,
            }, ensure_ascii=False)
            (directory / "production_request.txt").write_text(prompt, encoding="utf-8")
        else:
            self.receipt_model = ProductionReceipt
            if skill_directory is not None:
                from .producer_skills import freeze_skill
                if not skill_digest:
                    raise ValueError("单 Skill 冻结副本缺少 digest。")
                skill_dir = directory / "skills" / "single"
                freeze_skill(skill_directory, skill_dir, skill_digest)
            else:
                skill_dir = self._resolve_skill_dir(skill_name, skills_root)
            prompt = self._build_prompt(
                creator_id,
                series_id,
                topic_id,
                topic_title,
                topic_brief=topic_brief,
                series_description=series_description,
                audience=audience,
                skill_name=skill_name,
                skills_root=skills_root,
                skill_dir=skill_dir,
                revision_instruction=revision_instruction,
            )
        try:
            run = self._execute(
                prompt,
                directory,
                thread_id=thread_id,
                skill_name=skill_name,
                skill_path=skill_dir / "SKILL.md",
                **({"skill_refs": skill_refs} if skill_refs else {}),
                on_thread_started=on_thread_started,
                cancel_event=cancel_event,
                on_process_started=on_process_started,
                on_process_stopped=on_process_stopped,
            )
        except Exception:
            raise
        generated_at = now.isoformat()
        if cancel_event is not None and cancel_event.is_set():
            raise CodexProducerError("本地执行器已停止生产。", error_type="codex_interrupted")
        pack = self._materialize(
            run,
            directory=directory,
            pack_id=pack_id,
            creator_id=creator_id,
            series_id=series_id,
            topic_id=topic_id,
            topic_title=topic_title,
            generated_at=generated_at,
            skill_name=skill_name,
        )
        if composition is not None:
            from .skill_pair import write_evidence
            write_evidence(directory, composition, run.receipt)
        session = ProductionSession(
            thread_id=run.thread_id,
            pack_id=pack_id,
            created_at=generated_at,
            usage=run.usage,
        )
        (directory / SESSION_FILENAME).write_text(
            session.model_dump_json(indent=2), encoding="utf-8"
        )
        return ProducedPack(directory, pack, session)

    def _execute(
        self,
        prompt: str,
        working_directory: Path,
        *,
        thread_id: str | None = None,
        skill_name: str = "knowledge-to-carousel",
        skill_path: Path | None = None,
        on_thread_started: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
        on_process_started: Callable[[dict], None] | None = None,
        on_process_stopped: Callable[[], None] | None = None,
        public_observer: Callable[[dict], None] | None = None,
    ) -> CodexRun:
        with TemporaryDirectory(prefix="creatoros-codex-schema-") as temporary:
            schema_path = Path(temporary) / "production-receipt.schema.json"
            schema_path.write_text(
                json.dumps(self.receipt_model.model_json_schema(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            command = self._command(schema_path, working_directory, thread_id)
            timed_out = threading.Event()
            finished = threading.Event()
            tree = ProcessTree()
            started = monotonic()
            cleanup_errors: list[Exception] = []

            def watch():
                while not finished.wait(0.1):
                    if monotonic() - started >= self.timeout_seconds:
                        timed_out.set()
                    if timed_out.is_set() or (cancel_event is not None and cancel_event.is_set()):
                        try:
                            tree.close()
                        except Exception as error:
                            cleanup_errors.append(error)
                        return

            watcher = threading.Thread(target=watch, daemon=True, name="codex-process-watch")
            try:
                with TemporaryFile(mode="w+", encoding="utf-8") as stderr_file:
                    process = tree.start(
                        command,
                        on_started=on_process_started,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=stderr_file,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        cwd=working_directory,
                    )
                    watcher.start()
                    lines: list[str] = []
                    try:
                        assert process.stdin is not None and process.stdout is not None
                        process.stdin.write(prompt)
                        process.stdin.close()
                        with (working_directory / "codex_trace.jsonl").open("w", encoding="utf-8") as trace:
                            for line in process.stdout:
                                trace.write(line)
                                trace.flush()
                                lines.append(line)
                                self._notify_thread_started(line, on_thread_started)
                                if public_observer is not None:
                                    try:
                                        event = json.loads(line)
                                    except json.JSONDecodeError:
                                        continue
                                    if (isinstance(event, dict) and
                                            (event.get("type") == "thread.started" or
                                             (event.get("type") in {"item.started", "item.updated", "item.completed"}
                                              and isinstance(event.get("item"), dict)
                                              and event["item"].get("type") in {"agent_message", "web_search",
                                                                                  "command_execution", "mcp_tool_call"}))):
                                        public_observer(event)
                        return_code = process.wait()
                    finally:
                        finished.set()
                    stderr_file.seek(0)
                    stderr = stderr_file.read()
                    stdout = "".join(lines)
            except FileNotFoundError as error:
                raise CodexProducerError("未找到 codex CLI。", error_type="codex_not_found") from error
            finally:
                finished.set()
                if watcher.ident is not None:
                    watcher.join(timeout=6)
                tree.close()
                if cleanup_errors:
                    raise CodexProducerError("子进程回收失败，恢复前需要核实旧执行者。", error_type="process_cleanup_failed") from cleanup_errors[0]
                if on_process_stopped is not None:
                    on_process_stopped()
        if cancel_event is not None and cancel_event.is_set():
            raise CodexProducerError("本地执行器已停止生产。", error_type="codex_interrupted")
        if timed_out.is_set():
            raise CodexProducerError("Codex 内容生产超时。", error_type="codex_timeout")
        if return_code != 0:
            detail = "\n".join(
                part
                for part in (
                    stdout.strip()[-2_000:],
                    stderr.strip()[-2_000:],
                )
                if part
            )
            raise CodexProducerError(
                f"codex exec 退出码 {return_code}：{detail}",
                error_type="codex_exec_failed",
            )
        return parse_codex_jsonl(stdout, fallback_thread_id=thread_id or "", receipt_model=self.receipt_model)

    def _resolve_skill_dir(self, skill_name: str, skills_root: Path | None) -> Path:
        from .producer_skills import ProducerSkillCatalog

        catalog = ProducerSkillCatalog(
            skills_root or self.project_root / "data" / "producer-skills",
            self.project_root,
        )
        return catalog.resolve(skill_name)

    def _command(
        self,
        schema_path: Path,
        working_directory: Path,
        thread_id: str | None,
    ) -> list[str]:
        settings = ["-c", f'model="{CODEX_MODEL}"', "-c", f'model_reasoning_effort="{CODEX_EFFORT}"']
        if thread_id:
            return [
                self.executable, *settings, "-a", "never", "exec", "resume", "--json",
                "--skip-git-repo-check", "--output-schema", str(schema_path), thread_id, "-",
            ]
        return [
            self.executable, *settings, "-a", "never", "exec", "--json", "--sandbox", "read-only",
            "--skip-git-repo-check", "-C", str(working_directory),
            "--output-schema", str(schema_path), "-",
        ]

    @staticmethod
    def _notify_thread_started(
        line: str,
        callback: Callable[[str], None] | None,
    ) -> None:
        if callback is None:
            return
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return
        if event.get("type") == "thread.started" and event.get("thread_id"):
            callback(str(event["thread_id"]))

    def _build_prompt(
        self,
        creator_id: str,
        series_id: str,
        topic_id: str,
        topic_title: str,
        *,
        topic_brief: str | None = None,
        series_description: str = "",
        audience: str = "",
        skill_name: str = "knowledge-to-carousel",
        skills_root: Path | None = None,
        skill_dir: Path | None = None,
        revision_instruction: str | None = None,
    ) -> str:
        skill_dir = skill_dir or self._resolve_skill_dir(skill_name, skills_root)
        skill = "" if self.native_skill_inputs else (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        contract = (self.project_root / "creatoros" / "skills" / "knowledge-to-carousel" / "references" / "social-content-pack.md").read_text(
            encoding="utf-8"
        )
        revision = (
            f"\n本次返工要求：{revision_instruction.strip()}\n"
            if revision_instruction and revision_instruction.strip()
            else ""
        )
        skill_block = (
            f"{skill}\n\n"
            if skill
            else "本次 Skill 将通过 Codex SDK 的原生 SkillInput 注入；请读取其完整 SKILL.md，并按其相对路径解析附属资源。\n\n"
        )
        return (
            f"{skill_block}{contract}\n\n"
            f"本次 Skill 文件：{skill_dir / 'SKILL.md'}；附属 references/assets/scripts 相对此目录解析。\n"
            "你处于 CreatorOS receipt mode。请完成整篇图片轮播并真实调用图片生成能力。"
            "不要写最终 Manifest，也不要复制图片；最终只返回 output schema 要求的 JSON。"
            "不要返回制作中、待补充或任何中间回执；只有整套叙事、全部图片和最终发布文案都完成后才能返回。"
            "每张卡片的 source_image_path 必须是图片工具返回的真实绝对路径。\n\n"
            f"creator_id: {creator_id}\nseries_id: {series_id}\n"
            f"series_description: {series_description}\naudience: {audience}\n"
            f"topic_id: {topic_id}\ntopic_title: {topic_title}\n"
            f"topic_brief: {topic_brief or ''}\n"
            f"{revision}"
        )

    def _materialize(
        self,
        run: CodexRun,
        *,
        directory: Path,
        pack_id: str,
        creator_id: str,
        series_id: str,
        topic_id: str,
        topic_title: str,
        generated_at: str,
        skill_name: str = "knowledge-to-carousel",
    ) -> SocialContentPack:
        allowed_root = (self.generated_images_root / run.thread_id).resolve()
        images_dir = directory / "images"
        images_dir.mkdir()
        cards = []
        for card in run.receipt.cards:
            source = Path(card.source_image_path).resolve()
            checkpoint_owned = False
            if not source.is_relative_to(allowed_root) and (directory / "visual_checkpoint.json").is_file():
                from .visual_production import input_digest, load_checkpoint
                checkpoint = load_checkpoint(directory, input_digest(
                    (directory / "production_request.txt").read_text(encoding="utf-8"), [
                        ("mind", directory / "skills/mind/SKILL.md"),
                        ("production", directory / "skills/production/SKILL.md")]))
                checkpoint_owned = bool(checkpoint and checkpoint.visual_thread_id == run.thread_id and any(
                    p.order == card.order and Path(p.image_path).resolve() == source
                    for p in checkpoint.pages))
            if not source.is_relative_to(allowed_root) and not checkpoint_owned:
                raise CodexProducerError(
                    "Codex 返回了当前 thread 之外的图片路径。",
                    error_type="unsafe_generated_image_path",
                )
            if not source.is_file() or source.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                raise CodexProducerError(
                    f"生成图片不存在或格式不支持：{source.name}",
                    error_type="missing_generated_image",
                )
            filename = f"{card.order:02d}-{card.kind}{source.suffix.lower()}"
            target = images_dir / filename
            shutil.copy2(source, target)
            cards.append(
                CarouselCard(
                    order=card.order,
                    kind=card.kind,
                    section=card.section,
                    headline=card.headline,
                    body=card.body,
                    highlights=card.highlights,
                    visual_brief=card.visual_brief,
                    image_path=f"images/{filename}",
                )
            )
        pack = SocialContentPack(
            pack_id=pack_id,
            creator_id=creator_id,
            series_id=series_id,
            topic_id=topic_id,
            topic_title=topic_title,
            skill_name=skill_name,
            generated_at=generated_at,
            content_summary=run.receipt.content_summary,
            cards=cards,
            publish_copy=PublicationCopy.model_validate(run.receipt.publish_copy.model_dump()),
            sources=[SourceRef.model_validate(item.model_dump()) for item in run.receipt.sources],
        )
        (directory / MANIFEST_FILENAME).write_text(pack.model_dump_json(indent=2), encoding="utf-8")
        return SocialContentPack.load(directory)


class CodexSdkProducer(CodexProducer):
    """Content producer backed by the local Codex app-server Python SDK."""

    native_skill_inputs = True
    fresh_sessions = True

    @classmethod
    def from_defaults(cls) -> "CodexSdkProducer":
        from ..config import CODEX_PRODUCER_TIMEOUT_SECONDS, PROJECT_ROOT

        codex_home = Path(os.getenv("CODEX_HOME") or Path.home() / ".codex")
        return cls(
            project_root=PROJECT_ROOT,
            generated_images_root=codex_home / "generated_images",
            timeout_seconds=CODEX_PRODUCER_TIMEOUT_SECONDS,
        )

    def _execute(
        self,
        prompt: str,
        working_directory: Path,
        *,
        thread_id: str | None = None,
        skill_name: str = "knowledge-to-carousel",
        skill_path: Path | None = None,
        skill_refs: list[tuple[str, Path]] | None = None,
        on_thread_started: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
        on_process_started: Callable[[dict], None] | None = None,
        on_process_stopped: Callable[[], None] | None = None,
    ) -> CodexRun:
        del on_process_started
        try:
            return asyncio.run(
                self._execute_async(
                    prompt,
                    working_directory,
                    thread_id=thread_id,
                    skill_name=skill_name,
                    skill_path=skill_path,
                    skill_refs=skill_refs,
                    on_thread_started=on_thread_started,
                    cancel_event=cancel_event,
                )
            )
        finally:
            if on_process_stopped is not None:
                on_process_stopped()

    async def _execute_async(
        self,
        prompt: str,
        working_directory: Path,
        *,
        thread_id: str | None,
        skill_name: str,
        skill_path: Path | None,
        skill_refs: list[tuple[str, Path]] | None = None,
        on_thread_started: Callable[[str], None] | None,
        cancel_event: threading.Event | None,
    ) -> CodexRun:
        # Session IDs are audit handles, never implicit production inputs.
        del thread_id
        if skill_refs:
            from .skill_pair import StoryboardReceipt, mind_prompt
            from .visual_production import input_digest, recover_checkpoint
            mind, visual = skill_refs
            digest = input_digest(prompt, skill_refs)
            recovered = recover_checkpoint(working_directory, digest)
            if recovered is None:
                content = await self._execute_stage_async(
                    mind_prompt(mind[0], prompt), working_directory,
                    skill_name=mind[0], skill_path=mind[1], receipt_model=StoryboardReceipt,
                    stage="mind", on_thread_started=None, cancel_event=cancel_event,
                )
                storyboard, content_usage = content.receipt, content.usage
            else:
                storyboard, content_usage = recovered.storyboard, CodexUsage()
            (working_directory / "storyboard.json").write_text(storyboard.model_dump_json(indent=2), encoding="utf-8")
            (working_directory / "storyboard.md").write_text(
                "\n\n---\n\n".join(p.page_spec for p in storyboard.pages), encoding="utf-8")
            rendered = await self._execute_visual_async(
                working_directory, storyboard, visual, digest, recovered,
                topic_title=json.loads(prompt).get("topic_title", "内容草稿"),
                on_thread_started=on_thread_started, cancel_event=cancel_event,
            )
            usage = CodexUsage(**{k: getattr(content_usage, k) + getattr(rendered.usage, k)
                                  for k in CodexUsage.model_fields})
            return CodexRun(rendered.thread_id, rendered.receipt, usage)
        return await self._execute_stage_async(
            prompt, working_directory, skill_name=skill_name, skill_path=skill_path,
            receipt_model=self.receipt_model, stage="production",
            on_thread_started=on_thread_started, cancel_event=cancel_event,
        )

    async def _execute_stage_async(
        self, prompt: str, working_directory: Path, *, skill_name: str, skill_path: Path | None,
        receipt_model: type[ProductionModel], stage: str,
        on_thread_started: Callable[[str], None] | None, cancel_event: threading.Event | None,
        total_pages: int | None = None,
        thread=None, progress=None, response_name: str | None = None,
        deadline: float | None = None, finish_stage: bool = True,
    ) -> CodexRun:
        try:
            from openai_codex import ApprovalMode, Sandbox, SkillInput, TextInput
        except ImportError as error:
            raise CodexProducerError(
                "未安装 openai-codex；请在当前 Python 环境执行 pip install openai-codex。",
                error_type="codex_sdk_not_installed",
            ) from error

        if skill_path is None or not skill_path.is_file():
            raise CodexProducerError("生产 Skill 文件不存在。", error_type="skill_not_found")

        if cancel_event is not None and cancel_event.is_set():
            raise CodexProducerError("本地执行器已停止生产。", error_type="codex_interrupted")
        if deadline is not None and monotonic() >= deadline:
            raise CodexProducerError("Codex 内容生产超时。", error_type="codex_timeout")
        prompt += (f"\n本阶段 Skill 文件：{skill_path.resolve()}。请先完整读取此文件；"
                   "相对资源路径基于它所在的目录，不要去全局目录寻找同名 Skill。")
        deadline = deadline if deadline is not None else monotonic() + self.timeout_seconds
        name = response_name or stage
        (working_directory / f"{name}_request.txt").write_text(prompt, encoding="utf-8")
        inputs = [TextInput(text=prompt), SkillInput(name=skill_name, path=str(skill_path.resolve()))]
        from .production_progress import ProgressWriter, collect_observed_turn
        progress = progress or ProgressWriter(working_directory, stage, total_pages)
        def trace(event: dict) -> None:
            with (working_directory / "codex_trace.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"stage": stage, "at": datetime.now().astimezone().isoformat(), **event}, ensure_ascii=False) + "\n")
        try:
            async with AsyncExitStack() as stack:
                if thread is None:
                    codex = await stack.enter_async_context(_production_client(deadline, cancel_event))
                    thread = await _bounded_sdk(codex.thread_start(
                        approval_mode=ApprovalMode.deny_all, cwd=str(working_directory),
                        model=CODEX_MODEL, sandbox=Sandbox.read_only,
                        developer_instructions=PRODUCTION_RULES,
                    ), deadline, cancel_event)
                    trace({"type": "thread.started", "thread_id": thread.id,
                           "backend": "python-codex-sdk", "model": CODEX_MODEL,
                           "reasoning_effort": CODEX_EFFORT, "memory_enabled": False})
                    if on_thread_started is not None:
                        on_thread_started(thread.id)

                if cancel_event is not None and cancel_event.is_set():
                    raise CodexProducerError("本地执行器已停止生产。", error_type="codex_interrupted")
                if deadline is not None and monotonic() >= deadline:
                    raise CodexProducerError("Codex 内容生产超时。", error_type="codex_timeout")
                turn_request = thread.turn(
                    inputs,
                    cwd=str(working_directory),
                    effort=CODEX_EFFORT,
                    model=CODEX_MODEL,
                    output_schema=receipt_model.model_json_schema(),
                    sandbox=Sandbox.read_only,
                )
                try:
                    turn = await _bounded_sdk(turn_request, deadline, cancel_event)
                except asyncio.TimeoutError as error:
                    raise CodexProducerError("Codex 启动本轮执行超时。", error_type="codex_timeout") from error
                task = asyncio.create_task(collect_observed_turn(turn, progress))
                started = monotonic()
                interruption: str | None = None
                while not task.done():
                    if cancel_event is not None and cancel_event.is_set():
                        interruption = "codex_interrupted"
                        try:
                            await asyncio.wait_for(turn.interrupt(), timeout=5)
                        except Exception:
                            task.cancel()
                        break
                    if monotonic() >= (deadline if deadline is not None else started + self.timeout_seconds):
                        interruption = "codex_timeout"
                        try:
                            await asyncio.wait_for(turn.interrupt(), timeout=5)
                        except Exception:
                            task.cancel()
                        break
                    await asyncio.sleep(0.1)
                if interruption is not None:
                    try:
                        await asyncio.wait_for(task, timeout=5)
                    except (Exception, asyncio.CancelledError):
                        pass
                    message = "本地执行器已停止生产。" if interruption == "codex_interrupted" else "Codex 内容生产超时。"
                    raise CodexProducerError(message, error_type=interruption)
                result = await task
        except CodexProducerError as error:
            progress.finish("interrupted" if error.error_type == "codex_interrupted" else "failed")
            trace({"type": "stage.failed", "error_type": error.error_type})
            raise
        except Exception as error:
            progress.finish("failed")
            message = str(error) or error.__class__.__name__
            error_type = "codex_usage_limit" if "usage limit" in message.lower() else "codex_sdk_failed"
            trace({"type": "stage.failed", "error_type": error_type})
            raise CodexProducerError(f"Codex SDK 执行失败：{message}", error_type=error_type) from error

        usage = self._sdk_usage(result.usage)
        trace({
            "type": "turn.completed", "thread_id": thread.id,
            "turn_id": result.id, "status": str(result.status), "usage": usage.model_dump(),
        })
        (working_directory / f"{stage}_usage.json").write_text(usage.model_dump_json(), encoding="utf-8")

        final_text = result.final_response or ""
        # Preserve the actual final answer even if schema/host validation fails.
        (working_directory / f"{name}_response.txt").write_text(final_text, encoding="utf-8")
        if not final_text:
            progress.finish("failed")
            raise CodexProducerError("Codex SDK 未返回最终生产回执。", error_type="codex_protocol_error")
        try:
            receipt = receipt_model.model_validate_json(final_text)
        except Exception as error:
            progress.finish("failed")
            raise CodexProducerError(
                f"Codex SDK 生产回执不符合约定：{error}",
                error_type="invalid_production_receipt",
            ) from error
        if finish_stage:
            progress.finish("completed")
        return CodexRun(thread.id, receipt, usage)

    async def _execute_visual_async(self, directory, storyboard, skill, digest, recovered, *,
                                    topic_title, on_thread_started, cancel_event):
        from openai_codex import ApprovalMode, Sandbox
        from .production_progress import ProgressWriter
        from .visual_production import (RenderedPage, VisualCheckpoint, VisualPlan, atomic_json, build_receipt,
                                        plan_prompt, render_prompt, save_page, validate_plan, verified_pages)
        progress = ProgressWriter(directory, "visual", len(storyboard.pages))
        deadline = monotonic() + self.timeout_seconds
        usage = CodexUsage()
        name, path = skill
        try:
            async with _production_client(deadline, cancel_event) as codex:
                thread = await _bounded_sdk(codex.thread_start(approval_mode=ApprovalMode.deny_all, cwd=str(directory),
                                                 model=CODEX_MODEL, sandbox=Sandbox.read_only,
                                                 developer_instructions=PRODUCTION_RULES), deadline, cancel_event)
                if on_thread_started:
                    on_thread_started(thread.id)
                with (directory / "codex_trace.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({"stage": "visual", "type": "thread.started", "thread_id": thread.id,
                                             "model": CODEX_MODEL, "reasoning_effort": CODEX_EFFORT}) + "\n")
                async def run(prompt, model, receipt_name):
                    return await self._execute_stage_async(
                        prompt, directory, skill_name=name, skill_path=path, receipt_model=model,
                        stage="visual", thread=thread, progress=progress, response_name=receipt_name,
                        deadline=deadline, finish_stage=False, on_thread_started=None, cancel_event=cancel_event)
                if recovered is None:
                    progress.page("planning", None, 0)
                    planned = await run(plan_prompt(name, storyboard), VisualPlan, "visual_plan")
                    plan = validate_plan(planned.receipt, storyboard, path.parent)
                    usage = planned.usage
                    checkpoint = VisualCheckpoint(input_digest=digest, storyboard=storyboard, plan=plan,
                                                  visual_thread_id=thread.id, pages=[])
                else:
                    checkpoint = recovered.model_copy(update={"visual_thread_id": thread.id})
                    plan = validate_plan(checkpoint.plan, storyboard, path.parent)
                atomic_json(directory / "visual_checkpoint.json", checkpoint)
                atomic_json(directory / "visual_plan.json", checkpoint.plan)
                for page in plan.pages:
                    if page.order in {p.order for p in checkpoint.pages}:
                        continue
                    progress.page("rendering", page.order, len(checkpoint.pages), 1)
                    context = ("恢复本次任务：以下是本篇原内容与完整视觉计划，已完成页不要重新生成。\n"
                               + storyboard.model_dump_json() + "\n" + plan.model_dump_json() + "\n"
                               if recovered is not None else "")
                    receipt_name = f"visual_page_{page.order:02d}"
                    try:
                        rendered = await run(context + render_prompt(page, len(plan.pages)), RenderedPage, receipt_name)
                        if rendered.receipt.order != page.order:
                            raise ValueError("本次 turn 返回了其他页。")
                        save_page(directory, checkpoint, rendered.receipt, self.generated_images_root, path.parent, 1)
                    except (ValueError, CodexProducerError) as error:
                        if isinstance(error, CodexProducerError) and error.error_type != "invalid_production_receipt":
                            raise
                        # One text-only repair, never restart a paid render automatically.
                        raw = directory / f"{receipt_name}_response.txt"
                        if not raw.is_file():
                            raise
                        progress.page("rendering", page.order, len(checkpoint.pages), 2)
                        rendered = await run(
                            f"第 {page.order} 页回执字段无效。仅重新提交这个页面的 JSON，严禁重新生图、编辑图片或改其他页。"
                            "保留刚才真实工具返回的路径、实际 Prompt 和参考资源；无真实图片则不要编造。原回执：\n"
                            + raw.read_text(encoding="utf-8"), RenderedPage, receipt_name + "_repair")
                        if rendered.receipt.order != page.order:
                            raise ValueError("回执修复仍返回其他页。")
                        save_page(directory, checkpoint, rendered.receipt, self.generated_images_root, path.parent, 2)
                    usage = rendered.usage  # SDK reports cumulative usage for THIS visual thread.
                    progress.page("rendering", page.order, len(checkpoint.pages))
                progress.page("assembling", None, len(checkpoint.pages))
                verified_pages(directory, checkpoint)
                receipt = build_receipt(storyboard, [dict(order=p.order, source_image_path=p.image_path,
                                                       image_prompt=p.image_prompt, reference_assets=p.reference_assets)
                                                   for p in checkpoint.pages], topic_title)
                (directory / "visual_response.txt").write_text(receipt.model_dump_json(indent=2), encoding="utf-8")
                progress.finish("completed")
                return CodexRun(thread.id, receipt, usage)
        except Exception as error:
            progress.finish("interrupted" if getattr(error, "error_type", "") == "codex_interrupted" else "failed")
            # Enable explicit same-Revision recovery only after durable plan/image checks.
            if (directory / "visual_checkpoint.json").is_file() and getattr(error, "error_type", "") != "codex_interrupted":
                from .visual_production import load_checkpoint
                try:
                    load_checkpoint(directory, digest)
                except (ValueError, OSError):
                    pass
                else:
                    raise CodexProducerError(f"逐页交付中断，已保存图片保留：{error}",
                                             error_type="visual_delivery_failed") from error
            if isinstance(error, CodexProducerError):
                raise
            raise CodexProducerError(f"逐页生产交付失败：{error}", error_type="invalid_production_receipt") from error

    @staticmethod
    def _sdk_usage(value) -> CodexUsage:
        if value is None:
            return CodexUsage()
        total = value.total
        return CodexUsage(
            input_tokens=total.input_tokens,
            cached_input_tokens=total.cached_input_tokens,
            output_tokens=total.output_tokens,
            reasoning_output_tokens=total.reasoning_output_tokens,
        )
