from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
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
    receipt: ProductionReceipt
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


def parse_codex_jsonl(stdout: str, *, fallback_thread_id: str = "") -> CodexRun:
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
    if not thread_id or not final_text:
        raise CodexProducerError("Codex 未返回 thread_id 或最终生产回执。", error_type="codex_protocol_error")
    try:
        receipt = ProductionReceipt.model_validate_json(final_text)
    except Exception as error:
        raise CodexProducerError(
            f"Codex 生产回执不符合约定：{error}", error_type="invalid_production_receipt"
        ) from error
    return CodexRun(thread_id, receipt, usage)


class CodexProducer:
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

    @classmethod
    def from_defaults(cls) -> "CodexProducer":
        from ..config import CODEX_PRODUCER_TIMEOUT_SECONDS, PROJECT_ROOT

        codex_home = Path(os.getenv("CODEX_HOME") or Path.home() / ".codex")
        return cls(
            project_root=PROJECT_ROOT,
            generated_images_root=codex_home / "generated_images",
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
        thread_id: str | None = None,
        revision_instruction: str | None = None,
        on_thread_started: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
        on_process_started: Callable[[dict], None] | None = None,
        on_process_stopped: Callable[[], None] | None = None,
    ) -> ProducedPack:
        now = datetime.now().astimezone()
        directory = Path(directory).resolve()
        directory.mkdir(parents=True, exist_ok=False)
        prompt = self._build_prompt(
            creator_id,
            series_id,
            topic_id,
            topic_title,
            topic_brief=topic_brief,
            series_description=series_description,
            audience=audience,
            revision_instruction=revision_instruction,
        )
        try:
            run = self._execute(
                prompt,
                directory,
                thread_id=thread_id,
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
        )
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
        on_thread_started: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
        on_process_started: Callable[[dict], None] | None = None,
        on_process_stopped: Callable[[], None] | None = None,
    ) -> CodexRun:
        with TemporaryDirectory(prefix="creatoros-codex-schema-") as temporary:
            schema_path = Path(temporary) / "production-receipt.schema.json"
            schema_path.write_text(
                json.dumps(ProductionReceipt.model_json_schema(), ensure_ascii=False, indent=2),
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
        return parse_codex_jsonl(stdout, fallback_thread_id=thread_id or "")

    def _command(
        self,
        schema_path: Path,
        working_directory: Path,
        thread_id: str | None,
    ) -> list[str]:
        if thread_id:
            return [
                self.executable, "-a", "never", "exec", "resume", "--json",
                "--skip-git-repo-check", "--output-schema", str(schema_path), thread_id, "-",
            ]
        return [
            self.executable, "-a", "never", "exec", "--json", "--sandbox", "read-only",
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
        revision_instruction: str | None = None,
    ) -> str:
        skill_dir = self.project_root / "creatoros" / "skills" / "knowledge-to-carousel"
        skill = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        contract = (skill_dir / "references" / "social-content-pack.md").read_text(
            encoding="utf-8"
        )
        revision = (
            f"\n本次返工要求：{revision_instruction.strip()}\n"
            if revision_instruction and revision_instruction.strip()
            else ""
        )
        return (
            f"{skill}\n\n{contract}\n\n"
            "你处于 CreatorOS receipt mode。请完成整篇图片轮播并真实调用图片生成能力。"
            "不要写最终 Manifest，也不要复制图片；最终只返回 output schema 要求的 JSON。"
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
    ) -> SocialContentPack:
        allowed_root = (self.generated_images_root / run.thread_id).resolve()
        images_dir = directory / "images"
        images_dir.mkdir()
        cards = []
        for card in run.receipt.cards:
            source = Path(card.source_image_path).resolve()
            try:
                source.relative_to(allowed_root)
            except ValueError as error:
                raise CodexProducerError(
                    "Codex 返回了当前 thread 之外的图片路径。",
                    error_type="unsafe_generated_image_path",
                ) from error
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
            skill_name="knowledge-to-carousel",
            generated_at=generated_at,
            content_summary=run.receipt.content_summary,
            cards=cards,
            publish_copy=PublicationCopy.model_validate(run.receipt.publish_copy.model_dump()),
            sources=[SourceRef.model_validate(item.model_dump()) for item in run.receipt.sources],
        )
        (directory / MANIFEST_FILENAME).write_text(pack.model_dump_json(indent=2), encoding="utf-8")
        return SocialContentPack.load(directory)
