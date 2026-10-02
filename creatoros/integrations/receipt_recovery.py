"""Explicitly import an already completed visual receipt into a new ContentRun revision.

This module never calls a model or an image service. The supplied Codex ledger is
treated as evidence, and ``--apply`` is gated by both the observed run version and
the SHA-256 of the exact receipt JSON inspected in that ledger.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from PIL import Image

from creatoros.config import DATABASE_URL
from creatoros.integrations.codex import (
    CodexRun,
    CodexSdkProducer,
    CodexUsage,
    ProductionReceipt,
)
from creatoros.integrations.producer_skills import _digest, skills_root_for
from creatoros.integrations.skill_pair import (
    StoryboardReceipt,
    VisualReceipt,
    join_visual,
)
from creatoros.integrations.visual_production import build_receipt, normalize_refs
from creatoros.runs import ContentRunError, ContentRunService
from creatoros.runs.artifacts import validate_artifact
from creatoros.runs.models import ContentRunInput
from creatoros.storage import ContentAttemptStatus, ContentRunStatus, Database


KNOWN_ECHO_FIX = ("沿调用流程", "按调用流程")


@dataclass(frozen=True)
class VerifiedSource:
    run_id: str
    run_version: int
    revision_number: int
    attempt_id: str
    attempt_directory: Path
    run_root: Path
    thread_id: str
    ledger: Path
    receipt_hash: str
    receipt_text: str
    legacy_receipt: dict
    storyboard: StoryboardReceipt
    visual: VisualReceipt | None
    joined: ProductionReceipt
    metadata_differences: tuple[str, ...]
    input: ContentRunInput


def read_ledger_receipt(ledger: Path, thread_id: str) -> tuple[str, dict]:
    """Read the final assistant output from the Codex session ledger for this thread."""
    if ledger.is_symlink() or not ledger.is_file():
        raise ValueError("Codex ledger 缺失或为符号链接。")
    saw_thread = False
    final_text: str | None = None
    with ledger.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"Codex ledger 第 {line_number} 行不是有效 JSON。") from error
            if event.get("type") == "session_meta" and (event.get("payload") or {}).get("id") == thread_id:
                saw_thread = True
            payload = event.get("payload") or {}
            if (event.get("type") != "response_item" or payload.get("type") != "message"
                    or payload.get("role") != "assistant"):
                continue
            for part in payload.get("content", []):
                if part.get("type") != "output_text" or not isinstance(part.get("text"), str):
                    continue
                final_text = part["text"]
    if not saw_thread:
        raise ValueError("指定 ledger 中没有数据库记录的 source thread ID。")
    if final_text is None:
        raise ValueError("ledger 中没有可验证的完整视觉回执。")
    try:
        value = json.loads(final_text)
        _classify_response(value)
    except Exception as error:
        raise ValueError("ledger 最后一条 assistant response 不是受支持的完整视觉回执。") from error
    return final_text, value


def _classify_response(value: dict) -> str:
    """Recognize only either the historical echoed receipt or known visual output."""
    if not isinstance(value, dict) or not isinstance(value.get("pages"), list):
        raise ValueError("ledger response lacks pages")
    allowed_top = {"content_summary", "cards", "publish_copy", "sources", "research_brief",
                   "causal_chain", "pages"}
    if set(value) - allowed_top:
        raise ValueError("unknown top-level receipt fields")
    if value["pages"] and all(isinstance(page, dict) and "page_spec" in page for page in value["pages"]):
        _visual_from_legacy(value)
        return "legacy_echo"
    plan_page_fields = {"order", "image_prompt", "reference_assets"}
    rendered_page_fields = plan_page_fields | {"source_image_path", "warnings"}
    if (not value["pages"] or any(not isinstance(page, dict)
            or set(page) not in (plan_page_fields, rendered_page_fields) for page in value["pages"])
            or len({tuple(sorted(page)) for page in value["pages"]}) != 1):
        raise ValueError("unrecognized visual page schema")
    if not {"cards", "publish_copy"}.issubset(value):
        raise ValueError("missing legacy metadata fields needed to prove the known repair")
    return "rendered_pages"


def _visual_from_legacy(value: dict) -> VisualReceipt:
    """Discard echoed content and retain only visual-stage fields."""
    payload = {key: value[key] for key in ("content_summary", "cards", "publish_copy", "sources")}
    payload["pages"] = [
        {key: page[key] for key in ("order", "image_prompt", "reference_assets")}
        for page in value["pages"]
    ]
    return VisualReceipt.model_validate(payload)


def _build_repaired_receipt(value: dict, storyboard: StoryboardReceipt, input_data: ContentRunInput,
                            attempt_dir: Path) -> tuple[ProductionReceipt, tuple[str, ...]]:
    """Repair only the documented blank display fields and known resource base."""
    if _classify_response(value) != "rendered_pages":
        raise ValueError("response is not the recognized rendered-pages shape")
    if (set(value) - {"content_summary", "cards", "publish_copy", "sources", "research_brief",
                      "causal_chain", "pages"}):
        raise ValueError("视觉回执含未知字段，拒绝自动修复。")
    orders = list(range(1, len(storyboard.pages) + 1))
    pages = value["pages"]
    cards = value["cards"]
    publish = value["publish_copy"]
    if len(pages) != len(orders) or len(cards) != len(orders):
        raise ValueError("视觉回执页数、卡片数与原 storyboard 不一致。")
    if [page.get("order") for page in pages] != orders or [card.get("order") for card in cards] != orders:
        raise ValueError("视觉回执页序或卡片顺序与原 storyboard 不一致。")
    if not isinstance(publish, dict) or not isinstance(publish.get("title"), str) \
            or publish["title"].strip() or not isinstance(publish.get("body"), str) or publish["body"].strip():
        raise ValueError("只有已知的空白发布 title/body 才允许从原内容生成草稿。")
    if any(not isinstance(card.get("headline"), str) or card["headline"].strip() for card in cards):
        raise ValueError("卡片 headline 并非已知的空白故障，拒绝覆盖。")
    card_fields = {"order", "kind", "section", "headline", "body", "highlights", "visual_brief",
                   "source_image_path"}
    for card, page, content in zip(cards, pages, storyboard.pages):
        if set(card) != card_fields or card["kind"] not in {"cover", "content", "summary", "sources", "cta"}:
            raise ValueError(f"第 {page['order']} 页卡片结构超出已知故障范围。")
        if card["body"] not in (None, "") or card["highlights"] != []:
            raise ValueError(f"第 {page['order']} 页包含不应从视觉阶段导入的新增文字。")
        if card["section"] not in (None, "") or card["visual_brief"] not in (None, ""):
            raise ValueError(f"第 {page['order']} 页包含未核验的卡片元数据。")
        source_image_path = page.get("source_image_path", card["source_image_path"])
        if card["source_image_path"] != source_image_path:
            raise ValueError(f"第 {page['order']} 页卡片图片与 rendered page 不一致。")
        if page["image_prompt"] == "" or not isinstance(page["image_prompt"], str):
            raise ValueError(f"第 {page['order']} 页实际生图 Prompt 缺失。")
        if page.get("warnings", []) != []:
            raise ValueError(f"第 {page['order']} 页包含未审阅 warnings，拒绝自动导入。")
        if not isinstance(page["reference_assets"], list) or not page["reference_assets"]:
            raise ValueError(f"第 {page['order']} 页参考资源缺失。")
    if set(publish) != {"title", "body", "hashtags"} or publish["hashtags"] != []:
        raise ValueError("发布文案含有未核验的额外字段，拒绝自动重建。")
    if not isinstance(value.get("content_summary"), str) or value.get("sources") != []:
        raise ValueError("视觉回执的摘要/来源结构超出已知旧 schema。")
    # If the failed schema echoed research metadata, it must still exactly match Mind's source.
    for key, expected in (("research_brief", storyboard.research_brief), ("causal_chain", storyboard.causal_chain)):
        if key in value and value[key] != expected:
            raise ValueError(f"视觉回执的 {key} 与原 storyboard 不一致。")

    skill_root = attempt_dir / "skills" / "production"
    rendered = []
    differences = ["rebuilt blank card headlines and publish title/body as drafts from the frozen storyboard",
                   "replaced visual-stage content summary with the frozen Mind research brief"]
    for page, card in zip(pages, cards):
        if page.get("warnings", []) != []:
            raise ValueError(f"第 {page['order']} 页包含未审阅 warnings，拒绝自动导入。")
        source_image_path = page.get("source_image_path", card["source_image_path"])
        refs = page["reference_assets"]
        if (not isinstance(refs, list) or len(refs) != 1
                or any(ref not in {"assets/character.png", "skills/production/assets/character.png"}
                       for ref in refs)):
            raise ValueError(f"第 {page['order']} 页参考资源不是已知的角色路径。")
        normalized = normalize_refs(refs, skill_root)
        if normalized != refs:
            differences.append(f"page {page['order']}: normalized the known skills/production asset prefix")
        rendered.append({"order": page["order"], "image_prompt": page["image_prompt"],
                         "reference_assets": page["reference_assets"],
                         "source_image_path": source_image_path, "warnings": []})
        rendered[-1]["reference_assets"] = normalized
    receipt = build_receipt(storyboard, rendered, input_data.topic_title)
    return receipt, tuple(dict.fromkeys(differences))


def _check_echo(storyboard: StoryboardReceipt, legacy: dict) -> tuple[str, ...]:
    old_pages = storyboard.pages
    echoed = legacy.get("pages")
    if not isinstance(echoed, list) or len(echoed) != len(old_pages):
        raise ValueError("原回执的 PageSpec 页数与已保存 storyboard 不一致。")
    differences: list[str] = []
    for expected, actual in zip(old_pages, echoed):
        if actual.get("order") != expected.order or not isinstance(actual.get("page_spec"), str):
            raise ValueError("原回执的 PageSpec 页序或内容字段无效。")
        returned = actual["page_spec"]
        if returned == expected.page_spec:
            continue
        # The only inspected discrepancy is a one-phrase Visual Semantics echo.
        # Everything preceding that metadata field must remain byte-for-byte equal.
        if expected.order != 1:
            raise ValueError(f"第 {expected.order} 页教学/屏幕文字与原 storyboard 不一致。")
        marker = "Visual Semantics"
        if marker not in returned or marker not in expected.page_spec:
            raise ValueError(f"第 {expected.order} 页教学/屏幕文字与原 storyboard 不一致。")
        expected_prefix, expected_semantics = expected.page_spec.split(marker, 1)
        returned_prefix, returned_semantics = returned.split(marker, 1)
        if expected_prefix != returned_prefix or expected_semantics.count(KNOWN_ECHO_FIX[0]) != 1:
            raise ValueError(f"第 {expected.order} 页教学/屏幕文字与原 storyboard 不一致。")
        if expected_semantics.replace(KNOWN_ECHO_FIX[0], KNOWN_ECHO_FIX[1], 1) != returned_semantics:
            raise ValueError(f"第 {expected.order} 页在 Visual Semantics 之外存在内容差异。")
        differences.append(f"page {expected.order}: approved Visual Semantics echo wording only")
    return tuple(differences)


def _read_source(service: ContentRunService, run_id: str, ledger: Path) -> VerifiedSource:
    ledger = Path(ledger)
    if ledger.is_symlink():
        raise ValueError("Codex ledger 缺失或为符号链接。")
    run = service.get(run_id)
    if run.status is not ContentRunStatus.FAILED or run.error_type != "invalid_production_receipt":
        raise ValueError("仅允许恢复 invalid_production_receipt 的 failed Run。")
    revision = service.get_active_revision(run_id)
    input_data = ContentRunInput.model_validate(revision.production_input_json)
    if input_data.production_protocol != "legacy":
        raise ValueError("此旧回执导入器只用于 legacy Run；新协议请从本次 checkpoint 恢复。")
    if input_data.composition is None:
        raise ValueError("此恢复工具只支持冻结双 Skill 的 Run。")
    attempts = service.repository.list_attempts(revision.id)
    source = [item for item in attempts
              if item.status is ContentAttemptStatus.FAILED
              and item.error_type == "invalid_production_receipt"
              and item.producer_thread_id == run.producer_thread_id
              and item.output_directory]
    if len(source) != 1 or not run.producer_thread_id:
        raise ValueError("数据库中无法唯一确定与失败 Run 绑定的 source Attempt/thread。")
    attempt = source[0]
    attempt_path = Path(attempt.output_directory)
    output_root = service.output_root.resolve()
    expected_dir = (output_root / input_data.creator_id / input_data.series_id / run_id
                    / f"revision-{revision.revision_number:03d}"
                    / f"attempt-{attempt.attempt_number:03d}").resolve()
    if (attempt_path.is_symlink() or attempt_path.resolve() != expected_dir
            or not expected_dir.is_relative_to(output_root)):
        raise ValueError("source Attempt 路径越出 CreatorOS 输出目录。")
    attempt_dir = expected_dir
    run_root = attempt_dir.parent.parent
    frozen_root = run_root / "skill-snapshot"
    pair = input_data.composition
    for role in ("mind", "production"):
        for folder in (frozen_root / "skills" / role, attempt_dir / "skills" / role):
            if folder.is_symlink() or not folder.is_dir() or _digest(folder) != getattr(pair, role).digest:
                raise ValueError(f"source Attempt 的 {role} 冻结 Skill digest 不匹配。")
    storyboard_path = attempt_dir / "storyboard.json"
    storyboard_md = attempt_dir / "storyboard.md"
    for path in (storyboard_path, storyboard_md):
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(attempt_dir):
            raise ValueError("source Attempt 的原始 storyboard 缺失或越界。")
    storyboard = StoryboardReceipt.model_validate_json(storyboard_path.read_text(encoding="utf-8"))
    if storyboard_md.read_text(encoding="utf-8") != "\n\n---\n\n".join(p.page_spec for p in storyboard.pages):
        raise ValueError("source Attempt 的 storyboard.md 与 JSON 不一致。")

    thread_id = run.producer_thread_id
    receipt_text, legacy = read_ledger_receipt(ledger, thread_id)
    receipt_hash = hashlib.sha256(receipt_text.encode("utf-8")).hexdigest()
    response_kind = _classify_response(legacy)
    if response_kind == "legacy_echo":
        differences = _check_echo(storyboard, legacy)
        visual = _visual_from_legacy(legacy)
        joined = join_visual(storyboard, visual)
    else:
        visual = None
        joined, differences = _build_repaired_receipt(legacy, storyboard, input_data, attempt_dir)
    if len(joined.cards) != len(storyboard.pages):
        raise ValueError("图片卡片数与 storyboard 页数不一致。")
    _validate_refs(joined, attempt_dir)
    return VerifiedSource(
        run_id=run_id, run_version=run.version, revision_number=revision.revision_number,
        attempt_id=attempt.id, attempt_directory=attempt_dir, run_root=run_root,
        thread_id=thread_id, ledger=ledger.resolve(), receipt_hash=receipt_hash,
        receipt_text=receipt_text, legacy_receipt=legacy, storyboard=storyboard,
        visual=visual, joined=joined, metadata_differences=differences, input=input_data,
    )


def _validate_refs(receipt: ProductionReceipt, attempt_dir: Path) -> None:
    from creatoros.integrations.skill_pair import PairReceipt

    if not isinstance(receipt, PairReceipt):
        raise ValueError("joined receipt 未通过双 Skill 回执验收。")
    production_root = (attempt_dir / "skills" / "production").resolve()
    for page in receipt.pages:
        for ref in page.reference_assets:
            path = Path(ref)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("参考资源路径越界。")
            target = attempt_dir / "skills" / "production" / path
            if (target.is_symlink() or not target.is_file()
                    or not target.resolve().is_relative_to(production_root)):
                raise ValueError("视觉回执引用了 source Attempt 中不存在或越界的资源。")
    if any("assets/character.png" not in page.reference_assets for page in receipt.pages):
        raise ValueError("小白角色参考资源缺失。")


def _verify_images(source: VerifiedSource, image_root: Path) -> None:
    if image_root.is_symlink():
        raise ValueError("source thread 的图片根目录不能是符号链接。")
    root = image_root.resolve()
    if root.is_symlink() or not root.is_dir():
        raise ValueError("source thread 的生成图片目录不存在。")
    if [card.order for card in source.joined.cards] != list(range(1, len(source.storyboard.pages) + 1)):
        raise ValueError("图片卡片页序与 storyboard 不一致。")
    paths = set()
    for card in source.joined.cards:
        candidate = Path(card.source_image_path)
        if not candidate.is_absolute() or candidate.is_symlink():
            raise ValueError(f"第 {card.order} 页图片路径无效。")
        resolved = candidate.resolve()
        if not resolved.is_relative_to(root) or not resolved.is_file() or resolved.suffix.lower() != ".png":
            raise ValueError(f"第 {card.order} 页图片不在 source thread 根目录或不是 PNG。")
        if resolved in paths:
            raise ValueError("多页回执引用了同一张图片。")
        paths.add(resolved)
        try:
            with Image.open(resolved) as image:
                image.verify()
            with Image.open(resolved) as image:
                if image.width <= 0 or image.height <= 0:
                    raise ValueError("图片尺寸无效。")
        except Exception as error:
            raise ValueError(f"第 {card.order} 页 PNG 无法解码。") from error
    if len(paths) != len(source.storyboard.pages):
        raise ValueError("source thread 的图片数量与 storyboard 页数不一致。")


class ReceiptImportProducer(CodexSdkProducer):
    """Run the normal producer materialization path using already verified evidence."""

    fresh_sessions = False

    def __init__(self, *, source: VerifiedSource, **kwargs):
        super().__init__(**kwargs)
        self.source = source

    def _execute(self, prompt, working_directory, *, skill_refs=None, on_thread_started=None, **kwargs):
        del prompt, kwargs
        if len(skill_refs or []) != 2:
            raise ValueError("恢复时没有收到双 Skill 冻结副本。")
        for role, (_, skill_file) in zip(("mind", "production"), skill_refs):
            if _digest(skill_file.parent) != getattr(self.source.input.composition, role).digest:
                raise ValueError(f"目标 Attempt 的 {role} Skill digest 不匹配。")
        (working_directory / "storyboard.json").write_text(
            self.source.storyboard.model_dump_json(indent=2), encoding="utf-8")
        (working_directory / "storyboard.md").write_text(
            "\n\n---\n\n".join(page.page_spec for page in self.source.storyboard.pages), encoding="utf-8")
        recovery = {
            "source": "existing_codex_receipt_import",
            "receipt_sha256": self.source.receipt_hash,
            "receipt_json": self.source.legacy_receipt,
            "receipt_json_exact": self.source.receipt_text,
            "source_run_id": self.source.run_id,
            "source_attempt_id": self.source.attempt_id,
            "source_thread_id": self.source.thread_id,
            "ledger_path": str(self.source.ledger),
            "metadata_differences": list(self.source.metadata_differences),
            "usage": CodexUsage().model_dump(mode="json"),
        }
        (working_directory / "receipt_recovery.json").write_text(
            json.dumps(recovery, ensure_ascii=False, indent=2), encoding="utf-8")
        with (working_directory / "codex_trace.jsonl").open("a", encoding="utf-8") as trace:
            trace.write(json.dumps({"type": "receipt_imported", "source_thread_id": self.source.thread_id,
                                    "source_attempt_id": self.source.attempt_id,
                                    "receipt_sha256": self.source.receipt_hash,
                                    "usage": CodexUsage().model_dump(mode="json")}, ensure_ascii=False) + "\n")
        if on_thread_started:
            on_thread_started(self.source.thread_id)
        return CodexRun(self.source.thread_id, self.source.joined, CodexUsage())


def _preflight(service: ContentRunService, source: VerifiedSource, producer: ReceiptImportProducer) -> None:
    _verify_images(source, producer.generated_images_root / source.thread_id)
    with TemporaryDirectory(prefix="creatoros-receipt-recovery-") as temporary:
        root = Path(temporary) / "run"
        shutil.copytree(source.run_root / "skill-snapshot", root / "skill-snapshot")
        destination = root / f"revision-{source.revision_number + 1:03d}" / "attempt-001"
        produced = producer.produce_to(
            directory=destination,
            pack_id=f"{source.run_id}-r{source.revision_number + 1:03d}",
            creator_id=source.input.creator_id, series_id=source.input.series_id,
            topic_id=source.input.topic_id, topic_title=source.input.topic_title,
            topic_brief=source.input.topic_brief,
            series_description=source.input.series_description, audience=source.input.audience,
            composition=source.input.composition, skills_root=skills_root_for(service.database),
            on_thread_started=None,
        )
        checked = validate_artifact(produced.directory, composition=source.input.composition)
        if checked.card_count != len(source.storyboard.pages):
            raise ValueError("预检产物页数与原 storyboard 不一致。")


def inspect_recovery(service: ContentRunService, run_id: str, ledger: Path,
                     producer: CodexSdkProducer) -> VerifiedSource:
    source = _read_source(service, run_id, ledger)
    _verify_images(source, producer.generated_images_root / source.thread_id)
    # Use the import producer for the exact same materialization/evidence validation
    # path that apply will use. This only writes inside a temporary directory.
    preflight_producer = ReceiptImportProducer(
        source=source, project_root=producer.project_root,
        generated_images_root=producer.generated_images_root,
        timeout_seconds=producer.timeout_seconds,
    )
    _preflight(service, source, preflight_producer)
    return source


def recover_existing_receipt(
    service: ContentRunService,
    run_id: str,
    ledger: Path,
    *,
    expected_version: int,
    accept_receipt_sha256: str,
    producer: CodexSdkProducer | None = None,
):
    producer = producer or CodexSdkProducer.from_defaults()
    source = inspect_recovery(service, run_id, ledger, producer)
    if source.run_version != expected_version:
        raise ContentRunError("Run version 已变化，请重新 dryrun 并检查回执。", code="version_conflict")
    if source.receipt_hash != accept_receipt_sha256.lower():
        raise ValueError("接受的 receipt SHA-256 与当前 ledger 回执不匹配。")

    # The OS lock covers both the revision transition and the no-model import.
    with service.guard:
        service.guard.assert_clean()
        revision = service.request_revision(
            run_id,
            f"恢复已完成的 {len(source.joined.cards)} 张现有图片与视觉回执；不调用模型或生图，沿用原 storyboard 与冻结 Skill。",
            expected_version=expected_version,
        )
        queued = service.get(run_id)
        owner = f"receipt-import:{uuid4()}"
        prepared = service.claim(run_id, owner_id=owner, expected_version=queued.version)
        importer = ReceiptImportProducer(
            source=source, project_root=producer.project_root,
            generated_images_root=producer.generated_images_root,
            timeout_seconds=producer.timeout_seconds,
        )
        original_factory = service.producer_factory
        service.producer_factory = lambda: importer
        try:
            result = service.execute_claimed(prepared, owner_id=owner)
        finally:
            service.producer_factory = original_factory
    return revision, result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect/import an existing verified Codex visual receipt.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--expected-version", type=int)
    parser.add_argument("--accept-receipt-sha256")
    parser.add_argument("--apply", action="store_true", help="Create a revision and import the existing receipt.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.apply and (args.expected_version is None or not args.accept_receipt_sha256):
        raise SystemExit("--apply requires --expected-version and --accept-receipt-sha256")
    database = Database(DATABASE_URL)
    service = ContentRunService(database)
    producer = CodexSdkProducer.from_defaults()
    try:
        source = inspect_recovery(service, args.run_id, args.ledger, producer)
        print(json.dumps({
            "mode": "apply" if args.apply else "dryrun",
            "run_id": source.run_id,
            "observed_version": source.run_version,
            "source_attempt_id": source.attempt_id,
            "source_thread_id": source.thread_id,
            "receipt_sha256": source.receipt_hash,
            "card_count": len(source.joined.cards),
            "metadata_differences": list(source.metadata_differences),
            "preflight": "passed",
        }, ensure_ascii=False, indent=2))
        if not args.apply:
            return 0
        revision, result = recover_existing_receipt(
            service, args.run_id, args.ledger,
            expected_version=args.expected_version,
            accept_receipt_sha256=args.accept_receipt_sha256,
            producer=producer,
        )
        active_input = ContentRunInput.model_validate(service.get_active_revision(args.run_id).production_input_json)
        validation = validate_artifact(result.artifact_directory, composition=active_input.composition)
        print(json.dumps({"status": result.status, "revision_id": revision.id,
                          "artifact_digest": result.artifact_digest,
                          "card_count": validation.card_count}, ensure_ascii=False, indent=2))
        return 0
    finally:
        database.close()


if __name__ == "__main__":
    raise SystemExit(main())
