"""Native file-delivery smoke using the real producer/checkpoint code and local SDK transport."""
import asyncio
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Timer
from unittest.mock import patch

from PIL import Image
from openai_codex.generated import v2_all as sdk_types
from openai_codex.models import Notification

from creatoros.integrations.codex import CodexProducerError, CodexSdkProducer
from creatoros.integrations.native_production import (
    Checkpoint, evidence_files, ingest, load_checkpoint, recover_checkpoint, request_digest, verified_pages,
)
from creatoros.integrations.producer_skills import ProducerSkillCatalog, _digest
from creatoros.integrations.skill_pair import snapshot_pair


def write_skill(directory: Path, name: str, description: str):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\nUse this Skill.\n", encoding="utf-8")


def image(path: Path, color=(24, 72, 120)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (48, 32), color).save(path)


class NativeTransport:
    mode = "success"
    generated_root: Path | None = None
    calls: list = []
    reviews: list = []
    starts: list[str] = []
    resumes: list[str] = []
    generated: dict[tuple[str, int], int] = {}
    interrupted: list[str] = []
    turn_numbers: dict[str, int] = {}

    @classmethod
    def reset(cls, generated_root: Path, mode="success"):
        cls.mode = mode
        cls.generated_root = generated_root
        cls.calls, cls.starts, cls.resumes, cls.generated, cls.interrupted, cls.turn_numbers = [], [], [], {}, [], {}
        cls.reviews = []

    def __init__(self, config):
        assert "memories.use_memories=false" in config.config_overrides
        assert "project_doc_max_bytes=0" in config.config_overrides

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def thread_start(self, **options):
        thread_id = f"native-thread-{len(self.starts) + 1}"
        self.starts.append(thread_id)
        return self._thread(thread_id)

    async def thread_resume(self, thread_id, **options):
        self.resumes.append(thread_id)
        return self._thread(thread_id)

    def _thread(self, thread_id):
        harness = self

        class Thread:
            id = thread_id

            async def turn(inner, inputs, **controls):
                assert len(inputs) in {2, 3}
                assert [item.name for item in inputs[1:]] == (
                    ["local-mind", "local-maker"] if len(inputs) == 3 else ["local-maker"])
                assert all(Path(item.path).is_file() for item in inputs[1:])
                assert controls["cwd"].endswith("\\work") or controls["cwd"].endswith("/work")
                if "output_schema" in controls:
                    NativeTransport.reviews.append((thread_id, inputs, controls))
                    mode = NativeTransport.mode
                    response = {"status": "needs_input" if mode == "composition-conflict" else "ready",
                                "note": "8 个词各占一格，与仅一张六格硬要求冲突。允许两张图还是放宽六格？"
                                        if mode == "composition-conflict" else "保留全部内容，六格是默认，可按可读性拆图。"}
                    text = "not valid json" if mode == "composition-invalid" else json.dumps(response, ensure_ascii=False)
                    return Turn(thread_id, text, (50, 10), is_review=True)
                turn_no = NativeTransport.turn_numbers.get(thread_id, 0) + 1
                NativeTransport.turn_numbers[thread_id] = turn_no
                NativeTransport.calls.append((thread_id, inputs, controls, turn_no))
                mode = NativeTransport.mode
                if mode == "cancel":
                    return Turn(thread_id, None)

                if mode == "partial-failure":
                    NativeTransport.write_artifact(thread_id, controls, 1, "first source image", complete=False)
                elif mode == "recover" and turn_no == 2:
                    assert "本 Revision 技术恢复" in inputs[0].text
                    assert "保留已完成 1 张" in inputs[0].text
                    NativeTransport.write_artifact(thread_id, controls, 2, "second source image", complete=True,
                                                   existing_source=NativeTransport.source_for(thread_id, 1))
                elif mode in {"repair-success", "repair-fails"}:
                    if turn_no == 1:
                        NativeTransport.write_artifact(thread_id, controls, 1, "repair source image", complete=False,
                                                       invalid_content_path="../outside.md")
                    else:
                        assert "不得调用生图工具，不得重新研究或重画" in inputs[0].text
                        assert NativeTransport.generated.get((thread_id, 1)) == 1
                        NativeTransport.write_artifact(
                            thread_id, controls, 1, "repair source image", complete=(mode == "repair-success"),
                            existing_source=NativeTransport.source_for(thread_id, 1),
                            invalid_content_path=("../outside.md" if mode == "repair-fails" else None))
                elif mode == "success":
                    NativeTransport.write_artifact(thread_id, controls, 1, "single source image", complete=True)
                return Turn(thread_id, json.dumps({"complete": mode != "repair-fails"}, ensure_ascii=False),
                            NativeTransport.usage_total(mode, turn_no))

        return Thread()

    @staticmethod
    def usage_total(mode, turn_no):
        # The SDK reports thread-cumulative totals, not per-turn usage.
        if mode == "partial-failure":
            return (100, 20)
        if mode == "recover" and turn_no == 2:
            return (150, 35)
        if mode == "success":
            return (150, 35)
        return (3, 2)

    @classmethod
    def source_for(cls, thread_id: str, order: int) -> str:
        return str(cls.generated_root / thread_id / f"{order}.png")

    @classmethod
    def write_artifact(cls, thread_id, controls, order, prompt, *, complete, existing_source=None,
                       invalid_content_path=None):
        work = Path(controls["cwd"])
        reused_current = existing_source if order == 1 else None
        generated = Path(reused_current) if reused_current else Path(cls.source_for(thread_id, order))
        if reused_current is None:
            cls.generated[(thread_id, order)] = cls.generated.get((thread_id, order), 0) + 1
            image(generated, (order * 31, 50, 80))
        content_name = "page.md" if order == 1 else f"page-{order}.md"
        content_path = work / content_name
        if reused_current is None:
            content_path.write_text(f"Content for page {order}.", encoding="utf-8")
        items = []
        if order == 2 and existing_source:
            items.append({"order": 1, "source_image_path": existing_source,
                          "image_prompt": "first source image", "reference_assets": [], "content_file": "page.md"})
        items.append({"order": order, "source_image_path": str(generated), "image_prompt": prompt,
                      "reference_assets": [], "content_file": invalid_content_path or content_name})
        # Recovery and repair receipts preserve exactly the already accepted prompt/path.
        if order == 2:
            items[0]["image_prompt"] = "first source image"
        body = {"title": "Native smoke", "text": "Draft text", "hashtags": ["#test"],
                "complete": complete, "artifacts": items}
        (work / "delivery.json").write_text(json.dumps(body), encoding="utf-8")


class Turn:
    def __init__(self, thread_id, final_text, usage_total=(3, 2), *, is_review=False):
        self.id, self.final_text, self.usage_total = "turn-1", final_text, usage_total
        self.is_review = is_review

    async def interrupt(self):
        NativeTransport.interrupted.append(self.id)

    async def stream(self):
        if NativeTransport.mode == "cancel":
            await asyncio.sleep(30)
            return
        input_tokens, output_tokens = self.usage_total
        usage = sdk_types.ThreadTokenUsageUpdatedNotification.model_validate({
            "threadId": "native-thread", "turnId": self.id,
            "tokenUsage": {"last": {"inputTokens": input_tokens, "cachedInputTokens": 0,
                                      "outputTokens": output_tokens, "reasoningOutputTokens": 0,
                                      "totalTokens": input_tokens + output_tokens},
                           "total": {"inputTokens": input_tokens, "cachedInputTokens": 0,
                                     "outputTokens": output_tokens, "reasoningOutputTokens": 0,
                                     "totalTokens": input_tokens + output_tokens}},
        })
        yield Notification("thread/tokenUsage/updated", usage)
        if NativeTransport.mode == "partial-failure" and not self.is_review:
            raise RuntimeError("injected native transport failure")
        text = self.final_text or "{}"
        yield Notification("item/agentMessage/delta", sdk_types.AgentMessageDeltaNotification(
            threadId="native-thread", turnId=self.id, itemId="assistant-final", delta=text))
        yield Notification("item/completed", sdk_types.ItemCompletedNotification.model_validate({
            "threadId": "native-thread", "turnId": self.id, "completedAtMs": 1,
            "item": {"type": "agentMessage", "id": "assistant-final", "phase": "final_answer", "text": text},
        }))
        yield Notification("turn/completed", sdk_types.TurnCompletedNotification.model_validate({
            "threadId": "native-thread", "turn": {"id": self.id, "status": "completed", "items": []},
        }))


def producer_request(producer, directory, pair, skills_root, *, cancel_event=None):
    return producer.produce_to(
        directory=directory, pack_id="run-r001", creator_id="creator-test", series_id="series-test",
        topic_id="topic-test", topic_title="Native production", topic_brief="A scoped smoke test",
        series_description="", audience="", composition=pair, skills_root=skills_root,
        production_protocol="native-v1", cancel_event=cancel_event,
    )


def create_pair(root):
    catalog = ProducerSkillCatalog(root / "catalog", project_root=root)
    mind = root / "authoring" / "mind"
    maker = root / "authoring" / "maker"
    write_skill(mind, "local-mind", "Create concise content.")
    write_skill(maker, "local-maker", "Produce image artifacts.")
    (maker / "assets").mkdir()
    (maker / "assets" / "reference.md").write_text("approved reference", encoding="utf-8")
    mind_record = catalog.register_local(mind, role="mind")
    maker_record = catalog.register_local(maker, role="production")
    pair = snapshot_pair(catalog, mind_record["id"], maker_record["id"], native=True)
    return catalog, pair


def prepare_attempt(directory: Path, pair, catalog):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "work").mkdir(exist_ok=True)
    from creatoros.integrations.skill_pair import freeze_pair
    freeze_pair(catalog, pair, directory)
    payload = {"pack_id": "run-r001", "creator_id": "creator-test", "series_id": "series-test",
               "topic_id": "topic-test", "topic_title": "Native production", "topic_brief": "A scoped smoke test",
               "series_description": "", "audience": "", "revision_instruction": None, "previous_pages": None}
    (directory / "production_request.txt").write_text(json.dumps(payload), encoding="utf-8")
    checkpoint = Checkpoint(input_digest=request_digest(directory), thread_id="recovery-thread",
                            skill_digests={role: _digest(directory / "skills" / role)
                                           for role in ("mind", "production")})
    return checkpoint


def make_delivery(directory: Path, thread_id="boundary-thread", source=None, *, content_file="page.md",
                  reference_assets=None, order=1, prompt="actual prompt"):
    work = directory / "work"
    work.mkdir(parents=True, exist_ok=True)
    content = work / "page.md"
    content.write_text("accepted page content", encoding="utf-8")
    body = {"title": "Boundary", "text": "Draft", "hashtags": [], "complete": True,
            "artifacts": [{"order": order, "source_image_path": str(source),
                           "image_prompt": prompt, "reference_assets": reference_assets or [],
                           "content_file": content_file}]}
    (work / "delivery.json").write_text(json.dumps(body), encoding="utf-8")


def assert_value_error(action):
    try:
        action()
    except ValueError:
        return
    raise AssertionError("unsafe or invalid native evidence was accepted")


def main():
    with TemporaryDirectory(prefix="native-production-") as temporary:
        root = Path(temporary)
        catalog, pair = create_pair(root)
        generated_root = root / "generated"
        producer = CodexSdkProducer(project_root=root, generated_images_root=generated_root, timeout_seconds=8)

        # A real native producer turn sends both SkillInputs into one fresh thread.
        NativeTransport.reset(generated_root, "success")
        with patch("openai_codex.AsyncCodex", NativeTransport):
            pack = producer_request(producer, root / "simple" / "revision-001" / "attempt-001", pair, catalog.root)
        assert len(NativeTransport.starts) == 1 and not NativeTransport.resumes
        assert len(NativeTransport.reviews) == 1
        assert NativeTransport.reviews[0][0] == NativeTransport.calls[0][0]
        assert len(NativeTransport.calls) == 1 and len(NativeTransport.calls[0][1]) == 3
        simple = Path(pack.directory)
        checkpoint = load_checkpoint(simple)
        assert checkpoint and checkpoint.turn_completed and checkpoint.delivery.complete
        assert checkpoint.composition_review.status == "ready"
        assert (simple / "composition_review.md").is_file()
        assert checkpoint.usage.model_dump() == {
            "input_tokens": 150, "cached_input_tokens": 0,
            "output_tokens": 35, "reasoning_output_tokens": 0,
        }
        assert json.loads((simple / "production_usage.json").read_text()) == checkpoint.usage.model_dump()
        assert pack.session.usage == checkpoint.usage
        assert len(verified_pages(simple, checkpoint)) == 1
        assert len(evidence_files(simple)) >= 4
        assert (simple / "images" / "01.png").is_file()

        # A completed sibling recovery is offline and must report zero new usage.
        existing_calls = len(NativeTransport.calls)
        offline_pack = producer_request(producer, simple.parent / "attempt-002", pair, catalog.root)
        assert len(NativeTransport.calls) == existing_calls
        assert len(NativeTransport.reviews) == 1
        assert offline_pack.session.usage.model_dump() == {
            "input_tokens": 0, "cached_input_tokens": 0,
            "output_tokens": 0, "reasoning_output_tokens": 0,
        }

        # Ingest uses real generated files and rejects escapes, bad order, and symlink resources.
        boundary = root / "boundary" / "revision-001" / "attempt-001"
        cp = prepare_attempt(boundary, pair, catalog)
        valid_image = generated_root / cp.thread_id / "valid.png"
        image(valid_image)
        allowed_reference = boundary / "skills" / "production" / "assets" / "reference.md"
        make_delivery(boundary, cp.thread_id, valid_image, reference_assets=[str(allowed_reference)])
        delivery_file = boundary / "work" / "delivery.json"
        delivery_file.write_text("\ufeff" + delivery_file.read_text(encoding="utf-8"), encoding="utf-8")
        ingest(boundary, cp, generated_root)
        assert Path(cp.pages[0].image_path).is_file()
        assert hashlib.sha256(Path(cp.pages[0].image_path).read_bytes()).hexdigest() == cp.pages[0].sha256

        outside = root / "outside.md"
        outside.write_text("outside", encoding="utf-8")
        empty_cp = Checkpoint(input_digest=request_digest(boundary), thread_id=cp.thread_id,
                              skill_digests=cp.skill_digests)
        make_delivery(boundary, cp.thread_id, valid_image, content_file=str(outside))
        assert_value_error(lambda: ingest(boundary, empty_cp, generated_root))
        outside_image = generated_root.parent / "outside.png"
        make_delivery(boundary, cp.thread_id, outside_image)
        image(outside_image)
        assert_value_error(lambda: ingest(boundary, empty_cp, generated_root))
        make_delivery(boundary, cp.thread_id, valid_image, order=2)
        assert_value_error(lambda: ingest(boundary, empty_cp, generated_root))

        evil_thread = empty_cp.model_copy(update={"thread_id": "../escape"})
        escaped_image = generated_root.parent / "escape" / "outside.png"
        image(escaped_image)
        make_delivery(boundary, evil_thread.thread_id, escaped_image)
        assert_value_error(lambda: ingest(boundary, evil_thread, generated_root))

        # Only finalized deliveries are immutable; previews remain editable.
        cp.turn_completed = True
        make_delivery(boundary, cp.thread_id, valid_image, prompt="rewritten prompt")
        assert_value_error(lambda: ingest(boundary, cp, generated_root))
        cp.turn_completed = False
        reference = boundary / "skills" / "production" / "assets" / "escape.md"
        try:
            reference.symlink_to(outside)
        except OSError as error:
            raise AssertionError(f"test environment cannot create a symlink: {error}") from error
        make_delivery(boundary, cp.thread_id, valid_image,
                      reference_assets=[str(reference)])
        assert_value_error(lambda: ingest(boundary, empty_cp, generated_root))
        reference.unlink()

        # Checkpoint validation rejects modified bytes and frozen inputs.
        saved_path = Path(cp.pages[0].image_path)
        original_bytes = saved_path.read_bytes()
        saved_path.write_bytes(b"tampered image")
        assert load_checkpoint(boundary) is None
        assert_value_error(lambda: verified_pages(boundary, cp))
        saved_path.write_bytes(original_bytes)
        assert load_checkpoint(boundary, "0" * 64) is None
        request_file = boundary / "production_request.txt"
        original_request = request_file.read_bytes()
        request_file.write_text(request_file.read_text(encoding="utf-8") + " tampered", encoding="utf-8")
        assert load_checkpoint(boundary) is None
        request_file.write_bytes(original_request)
        (boundary / "skills" / "mind" / "SKILL.md").write_text("tampered skill", encoding="utf-8")
        assert load_checkpoint(boundary) is None
        (boundary / "skills" / "mind" / "SKILL.md").write_text(
            (catalog.locate(pair.mind.id) / "SKILL.md").read_text(encoding="utf-8"), encoding="utf-8")
        assert load_checkpoint(boundary) is not None

        # Sibling attempts in one Revision reuse the verified page and same SDK thread.
        revision = root / "recovery-run" / "revision-001"
        first_dir = revision / "attempt-001"
        first_cp = prepare_attempt(first_dir, pair, catalog)
        source1 = generated_root / first_cp.thread_id / "1.png"
        image(source1, (90, 10, 40))
        make_delivery(first_dir, first_cp.thread_id, source1,
                      content_file=str(first_dir / "work" / "page.md"))
        ingest(first_dir, first_cp, generated_root)
        previous_index = (first_dir / "work" / "delivery.json").read_bytes()
        (first_dir / "work" / "notes.md").write_text("same revision notes", encoding="utf-8")
        second_dir = revision / "attempt-002"
        second_dir.mkdir()
        (second_dir / "work").mkdir()
        prepare_attempt(second_dir, pair, catalog)
        restored = recover_checkpoint(second_dir, request_digest(second_dir))
        assert restored and restored.thread_id == first_cp.thread_id and len(restored.pages) == 1
        assert Path(restored.pages[0].image_path).parent == second_dir / "partial-images"
        assert (second_dir / "work" / "notes.md").read_text(encoding="utf-8") == "same revision notes"
        assert (second_dir / "work" / "previous-delivery.json").read_bytes() == previous_index
        rebased_delivery = json.loads((second_dir / "work" / "delivery.json").read_text(encoding="utf-8"))
        assert rebased_delivery["artifacts"][0]["content_file"] == "page.md"
        assert recover_checkpoint(root / "recovery-run" / "revision-002" / "attempt-001",
                                  request_digest(second_dir)) is None
        first_bytes = Path(restored.pages[0].image_path).read_bytes()
        Path(restored.pages[0].image_path).write_bytes(b"tamper latest checkpoint")
        third_dir = revision / "attempt-003"
        third_dir.mkdir()
        (third_dir / "work").mkdir()
        prepare_attempt(third_dir, pair, catalog)
        assert_value_error(lambda: recover_checkpoint(third_dir, request_digest(third_dir)))
        Path(restored.pages[0].image_path).write_bytes(first_bytes)

        # End-to-end failure then same-Revision resume: image 1 is not regenerated.
        run_root = root / "same-revision"
        revision_root = run_root / "revision-001"
        producer = CodexSdkProducer(project_root=root, generated_images_root=generated_root, timeout_seconds=8)
        NativeTransport.reset(generated_root, "partial-failure")
        with patch("openai_codex.AsyncCodex", NativeTransport):
            try:
                producer_request(producer, revision_root / "attempt-001", pair, catalog.root)
                raise AssertionError("injected failure should end the first attempt")
            except CodexProducerError as error:
                assert error.error_type == "native_delivery_failed"
        failed_dir = revision_root / "attempt-001"
        saved = load_checkpoint(failed_dir)
        assert saved and len(saved.pages) == 1 and not saved.turn_completed
        assert saved.usage.model_dump() == {
            "input_tokens": 100, "cached_input_tokens": 0,
            "output_tokens": 20, "reasoning_output_tokens": 0,
        }
        NativeTransport.mode = "recover"
        with patch("openai_codex.AsyncCodex", NativeTransport):
            recovered_pack = producer_request(producer, revision_root / "attempt-002", pair, catalog.root)
        assert NativeTransport.starts == ["native-thread-1"]
        assert NativeTransport.resumes == ["native-thread-1"]
        assert len(NativeTransport.reviews) == 1, "technical recovery must reuse the existing composition decision"
        assert NativeTransport.generated[("native-thread-1", 1)] == 1
        assert NativeTransport.generated[("native-thread-1", 2)] == 1
        recovered_dir = Path(recovered_pack.directory)
        recovered_checkpoint = load_checkpoint(recovered_dir)
        assert recovered_checkpoint and len(recovered_checkpoint.pages) == 2
        # Checkpoint keeps cumulative thread totals; this Attempt's receipt/session
        # subtracts the recovered baseline so prior cost is not counted twice.
        assert recovered_checkpoint.usage.model_dump() == {
            "input_tokens": 150, "cached_input_tokens": 0,
            "output_tokens": 35, "reasoning_output_tokens": 0,
        }
        expected_attempt_usage = {
            "input_tokens": 50, "cached_input_tokens": 0,
            "output_tokens": 15, "reasoning_output_tokens": 0,
        }
        assert json.loads((recovered_dir / "production_usage.json").read_text()) == expected_attempt_usage
        assert recovered_pack.session.usage.model_dump() == expected_attempt_usage

        # A malformed delivery gets one index-only repair turn; the image tool is not repeated.
        repair_root = root / "repair" / "revision-001"
        NativeTransport.reset(generated_root, "repair-success")
        with patch("openai_codex.AsyncCodex", NativeTransport):
            repaired_pack = producer_request(producer, repair_root / "attempt-001", pair, catalog.root)
        assert len(NativeTransport.calls) == 2
        assert NativeTransport.generated == {("native-thread-1", 1): 1}
        assert "不得调用生图工具，不得重新研究或重画" in NativeTransport.calls[1][1][0].text
        assert load_checkpoint(Path(repaired_pack.directory)).delivery.complete

        # One failed repair does not create a third turn or another image.
        NativeTransport.reset(generated_root, "repair-fails")
        with patch("openai_codex.AsyncCodex", NativeTransport):
            try:
                producer_request(producer, root / "repair-fail" / "revision-001" / "attempt-001", pair, catalog.root)
                raise AssertionError("bad repaired index must fail")
            except CodexProducerError as error:
                assert error.error_type == "native_delivery_failed"
        assert len(NativeTransport.calls) == 2 and NativeTransport.generated == {("native-thread-1", 1): 1}

        # A semantic conflict ends before production, not as a broken image index.
        NativeTransport.reset(generated_root, "composition-conflict")
        conflict_dir = root / "conflict" / "revision-001" / "attempt-001"
        with patch("openai_codex.AsyncCodex", NativeTransport):
            try:
                producer_request(producer, conflict_dir, pair, catalog.root)
                raise AssertionError("a composition conflict must stop production")
            except CodexProducerError as error:
                assert error.error_type == "skill_composition_needs_input"
                assert "8 个词" in str(error) and "提出返工" in str(error)
        assert len(NativeTransport.reviews) == 1 and not NativeTransport.calls and not NativeTransport.generated
        conflict_cp = load_checkpoint(conflict_dir)
        assert conflict_cp.composition_review.status == "needs_input" and not conflict_cp.repair_attempted
        assert not (conflict_dir / "production_repair_response.txt").exists()
        assert (conflict_dir / "composition_response.txt").is_file()
        # Even bypassing the service retry guard must not spend tokens on the same conflict.
        with patch("openai_codex.AsyncCodex", NativeTransport):
            try:
                producer_request(producer, conflict_dir.parent / "attempt-002", pair, catalog.root)
                raise AssertionError("a saved conflict needs a new requirement, not a technical retry")
            except CodexProducerError as error:
                assert error.error_type == "skill_composition_needs_input"
        assert len(NativeTransport.reviews) == 1 and len(NativeTransport.starts) == 1 and not NativeTransport.resumes

        # Invalid review output is preserved and cannot fall into image-index repair.
        NativeTransport.reset(generated_root, "composition-invalid")
        invalid_dir = root / "invalid-review" / "revision-001" / "attempt-001"
        with patch("openai_codex.AsyncCodex", NativeTransport):
            try:
                producer_request(producer, invalid_dir, pair, catalog.root)
                raise AssertionError("invalid review output must stop production")
            except CodexProducerError as error:
                assert error.error_type == "skill_composition_invalid"
        assert not NativeTransport.calls and not NativeTransport.generated
        assert (invalid_dir / "composition_response.txt").read_text() == "not valid json"
        # A malformed response is a technical failure, not a frozen semantic decision.
        NativeTransport.mode = "success"
        with patch("openai_codex.AsyncCodex", NativeTransport):
            valid_after_retry = producer_request(producer, invalid_dir.parent / "attempt-002", pair, catalog.root)
        assert len(NativeTransport.reviews) == 2 and len(NativeTransport.calls) == 1
        assert NativeTransport.starts == ["native-thread-1"] and NativeTransport.resumes == ["native-thread-1"]
        assert load_checkpoint(Path(valid_after_retry.directory)).composition_review.status == "ready"

        # Active cancellation interrupts the real turn collector and records interrupted state.
        NativeTransport.reset(generated_root, "cancel")
        cancel = Event()
        timer = Timer(0.75, cancel.set)
        timer.start()
        cancel_dir = root / "cancel" / "revision-001" / "attempt-001"
        try:
            with patch("openai_codex.AsyncCodex", NativeTransport):
                try:
                    producer_request(producer, cancel_dir, pair, catalog.root, cancel_event=cancel)
                    raise AssertionError("active cancellation must stop native production")
                except CodexProducerError as error:
                    assert error.error_type == "codex_interrupted"
        finally:
            timer.cancel()
        assert NativeTransport.interrupted
        assert json.loads((cancel_dir / "production_progress.json").read_text())['status'] == "interrupted"

        # A live but silent native turn has the same bounded deadline as startup.
        NativeTransport.reset(generated_root, "cancel")
        short_producer = CodexSdkProducer(project_root=root, generated_images_root=generated_root, timeout_seconds=0.3)
        timeout_dir = root / "timeout" / "revision-001" / "attempt-001"
        with patch("openai_codex.AsyncCodex", NativeTransport):
            try:
                producer_request(short_producer, timeout_dir, pair, catalog.root)
                raise AssertionError("silent native turn must time out")
            except CodexProducerError as error:
                assert error.error_type == "codex_timeout"
        assert NativeTransport.interrupted
        assert json.loads((timeout_dir / "production_progress.json").read_text())['status'] == "failed"

        # A single mature Skill retains its one-turn path without a composition review.
        NativeTransport.reset(generated_root, "success")
        maker = catalog.locate(pair.production.id)
        with patch("openai_codex.AsyncCodex", NativeTransport):
            single = producer.produce_to(
                directory=root / "single" / "revision-001" / "attempt-001", pack_id="single-r001",
                creator_id="creator-test", series_id="series-test", topic_id="topic-test",
                topic_title="Single Skill", skill_directory=maker, skill_digest=_digest(maker),
                skill_name=pair.production.id, production_protocol="native-v1")
        assert not NativeTransport.reviews and len(NativeTransport.calls) == 1
        assert load_checkpoint(Path(single.directory)).composition_review is None

    print("native_production_smoke=passed ingest=verified boundary=blocked recovery=same-revision usage=cumulative-checkpoint-attempt-delta offline-recovery=zero multi-skill=one-thread repair=once-no-regeneration cancel=interrupted")


if __name__ == "__main__":
    main()
