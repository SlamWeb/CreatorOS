"""No-model regression for importing an already completed pair receipt."""
from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from creatoros.integrations.codex import CodexProducerError, CodexSdkProducer
from creatoros.integrations.producer_skills import ProducerSkillCatalog, InstallReceipt, skills_root_for
from creatoros.integrations.receipt_recovery import inspect_recovery, recover_existing_receipt
from creatoros.integrations.skill_pair import StoryboardReceipt, snapshot_pair
from creatoros.runs import ContentRunExecutionError, ContentRunRepository, ContentRunService
from creatoros.storage import (
    ContentAttemptStatus, ContentRunStatus, Creator, CreatorPlatform,
    Database, Series, Topic, TopicSource, upgrade_database,
)

THREAD_ID = "01a0f776-7049-7540-a5ba-e2f8060b99c6"


def rejects(call, expected=ValueError):
    try:
        call()
    except expected:
        return
    raise AssertionError("invalid recovery source must be rejected")


def install_fixture(catalog, root: Path, name: str, repo: str, role: str):
    workspace = root / name
    source = workspace / "source"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: recovery fixture\n---\nfixture\n", encoding="utf-8")
    if role == "production":
        (source / "assets").mkdir()
        Image.new("RGB", (32, 32), "white").save(source / "assets" / "character.png")
    import subprocess
    subprocess.run(["git", "-C", str(source), "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(source), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-m", "fixture"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(source), "remote", "add", "origin", repo], check=True, capture_output=True)
    return catalog.register(workspace, repo, InstallReceipt(
        skill_path=".", carousel_compatible=False, compatibility_note="isolated recovery fixture"), role=role)


def page_spec(order: int) -> str:
    semantics = "Visual Semantics：沿调用流程" if order == 1 else f"Visual Semantics：第 {order} 页" 
    return f"PageSpec {order}\n核心问题：第 {order} 页的具体问题？\n实际上屏文字：真实屏幕文案 {order}\n{semantics}"


def legacy_receipt(root: Path, *, changed_text: bool = False, missing_page: bool = False) -> dict:
    storyboard = [page_spec(i) for i in range(1, 12)]
    cards, pages = [], []
    for order in range(1, 12):
        image = root / "generated" / THREAD_ID / f"{order}.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (108, 192), (order * 8, 20, 60)).save(image)
        actual_page = storyboard[order - 1]
        if order == 1:
            actual_page = actual_page.replace("Visual Semantics：沿调用流程", "Visual Semantics：按调用流程")
        if changed_text and order == 2:
            actual_page = actual_page.replace("真实屏幕文案 2", "被改写的屏幕文案")
        cards.append({"order": order, "kind": "cover" if order == 1 else "content",
                      "section": None, "headline": f"主题 {order}", "body": f"说明 {order}",
                      "highlights": [], "visual_brief": None, "source_image_path": str(image)})
        pages.append({"order": order, "page_spec": actual_page, "image_prompt": f"画第 {order} 页",
                      "reference_assets": ["assets/character.png"]})
    if missing_page:
        pages.pop()
    return {"content_summary": "从基础到实践的数据库教学", "cards": cards,
            "publish_copy": {"title": "数据库基础", "body": "完整说明", "hashtags": ["AI"]},
            "sources": [], "research_brief": "官方来源", "causal_chain": "需求到方案",
            "pages": pages}


class FailedReceiptProducer(CodexSdkProducer):
    def __init__(self, *, receipt, storyboard, **kwargs):
        super().__init__(**kwargs)
        self.receipt = receipt
        self.storyboard = storyboard

    def _execute(self, prompt, working_directory, *, on_thread_started=None, **kwargs):
        del prompt, kwargs
        (working_directory / "storyboard.json").write_text(
            self.storyboard.model_dump_json(indent=2), encoding="utf-8")
        (working_directory / "storyboard.md").write_text(
            "\n\n---\n\n".join(page.page_spec for page in self.storyboard.pages), encoding="utf-8")
        images = self.generated_images_root / THREAD_ID
        images.mkdir(parents=True, exist_ok=True)
        # The legacy receipt contains paths; image bytes are created by the fixture before failure.
        for order in range(1, 12):
            image = images / f"{order}.png"
            if not image.exists():
                Image.new("RGB", (108, 192), "white").save(image)
        on_thread_started(THREAD_ID)
        raise CodexProducerError("simulated legacy receipt validation failure",
                                 error_type="invalid_production_receipt")


def ledger_file(path: Path, receipt: dict) -> None:
    message = json.dumps(receipt, ensure_ascii=False, separators=(",", ":"))
    meta = {"type": "session_meta", "payload": {"id": THREAD_ID}}
    response = {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": message}]}}
    path.write_text(json.dumps(meta) + "\n" + json.dumps(response, ensure_ascii=False) + "\n", encoding="utf-8")


def main():
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        db_url = f"sqlite:///{(root / 'isolated.db').as_posix()}"
        upgrade_database(db_url)
        database = Database(db_url)
        catalog = ProducerSkillCatalog(skills_root_for(database))
        mind = install_fixture(catalog, root, "knowledge-to-storyboard-deep",
                               "https://github.com/SlamWeb/knowledge-to-storyboard", "mind")
        visual = install_fixture(catalog, root, "xiaobai",
                                 "https://github.com/SlamWeb/creatoros-ip-skills", "production")
        pair = snapshot_pair(catalog, mind["id"], visual["id"])
        storyboard = StoryboardReceipt.model_validate({
            "research_brief": "官方来源", "causal_chain": "需求到方案",
            "pages": [{"order": i, "page_spec": page_spec(i)} for i in range(1, 12)],
        })
        with database.session() as session:
            session.add(Creator(id="creator", display_name="Fixture", platform=CreatorPlatform.XIAOHONGSHU))
            session.add(Series(id="series", creator_id="creator", name="Fixture", description="tech",
                               audience="beginner", skill_name=None, mind_skill_id=mind["id"],
                               production_skill_id=visual["id"]))
            session.add(Topic(id="topic", series_id="series", title="数据库知识",
                              brief="从零基础到面试", source=TopicSource.MANUAL, position=1))
        initial = legacy_receipt(root)
        failed_producer = FailedReceiptProducer(
            receipt=initial, storyboard=storyboard, project_root=Path(__file__).parents[1],
            generated_images_root=root / "generated")
        service = ContentRunService(database, producer_factory=lambda: failed_producer, output_root=root / "outputs")
        run = service.create("topic")
        try:
            service.execute(run.id)
        except ContentRunExecutionError:
            pass
        else:
            raise AssertionError("fixture must produce a failed receipt attempt")
        failed = service.get(run.id)
        assert failed.status is ContentRunStatus.FAILED and failed.error_type == "invalid_production_receipt"
        repository = ContentRunRepository(database)
        first_revision = service.get_active_revision(run.id)
        first_attempt = repository.list_attempts(first_revision.id)[0]
        assert first_attempt.status is ContentAttemptStatus.FAILED
        ledger = root / "ledger.jsonl"
        ledger_file(ledger, initial)
        producer = CodexSdkProducer(project_root=Path(__file__).parents[1],
                                    generated_images_root=root / "generated")

        inspected = inspect_recovery(service, run.id, ledger, producer)
        assert len(inspected.joined.cards) == 11
        assert inspected.metadata_differences == ("page 1: approved Visual Semantics echo wording only",)
        rejects(lambda: recover_existing_receipt(
            service, run.id, ledger, expected_version=failed.version,
            accept_receipt_sha256="0" * 64, producer=producer))
        assert len(service.repository.list_revisions(run.id)) == 1

        for value, bad_ledger in ((legacy_receipt(root, changed_text=True), root / "changed.jsonl"),
                                  (legacy_receipt(root, missing_page=True), root / "missing-page.jsonl")):
            ledger_file(bad_ledger, value)
            rejects(lambda: inspect_recovery(service, run.id, bad_ledger, producer))
            assert len(service.repository.list_revisions(run.id)) == 1

        image = root / "generated" / THREAD_ID / "7.png"
        parked = image.with_suffix(".png.missing")
        image.rename(parked)
        rejects(lambda: inspect_recovery(service, run.id, ledger, producer))
        parked.rename(image)
        assert len(service.repository.list_revisions(run.id)) == 1

        _new_revision, result = recover_existing_receipt(
            service, run.id, ledger, expected_version=failed.version,
            accept_receipt_sha256=inspected.receipt_hash, producer=producer)
        assert result.status == "awaiting_approval"
        assert service.get(run.id).status is ContentRunStatus.AWAITING_APPROVAL
        revision = service.get_active_revision(run.id)
        assert revision.revision_number == 2 and first_attempt.status is ContentAttemptStatus.FAILED
        attempts = repository.list_attempts(revision.id)
        assert len(attempts) == 1 and attempts[0].status is ContentAttemptStatus.SUCCEEDED
        out = Path(revision.artifact_directory)
        receipt_record = json.loads((out / "receipt_recovery.json").read_text(encoding="utf-8"))
        assert receipt_record["source_attempt_id"] == first_attempt.id
        assert receipt_record["receipt_sha256"] == inspected.receipt_hash
        assert receipt_record["usage"] == {"input_tokens": 0, "cached_input_tokens": 0,
                                            "output_tokens": 0, "reasoning_output_tokens": 0}
        assert attempts[0].usage_json == {"input_tokens": 0, "cached_input_tokens": 0,
                                         "output_tokens": 0, "reasoning_output_tokens": 0}
        assert attempts[0].producer_thread_id == THREAD_ID
        assert not service.get(run.id).approved_revision_id
        assert len(list((out / "images").glob("*.png"))) == 11
        database.close()
    print("receipt_recovery_smoke=passed explicit_hash storyboard_diff images skill_digests new_revision awaiting_approval")


if __name__ == "__main__":
    main()
