"""Fault injection: interrupted SDK turns with valid final/files never succeed."""
import asyncio
import json
from contextlib import asynccontextmanager
from enum import Enum
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from creatoros.integrations.codex import CodexProducerError, CodexSdkProducer
from creatoros.integrations.codex_turn_guard import require_completed_turn
from creatoros.integrations.content_discussion import CodexDiscussionReviewer, ContentDiscussionService
from creatoros.integrations.native_production import CHECKPOINT, execute
from creatoros.integrations.producer_skills import ProducerSkillCatalog
from creatoros.integrations.skill_extraction import SkillExtractionService, sdk_extract
from creatoros.integrations.skill_pair import StoryboardReceipt
from creatoros.integrations.topic_research import CodexTopicResearcher, TopicResearchService
from tests.smoke_native_production import NativeTransport, Turn, create_pair, prepare_attempt
from tests.smoke_production_progress import FakeTurn, assistant_item, completed_item, turn_completed
from tests.smoke_skill_draft_files import write_native_drafts
from tests.smoke_topic_research_sdk import ControlledSdk, ControlledTurn


def assert_interrupted(call):
    try:
        call()
    except CodexProducerError as error:
        assert error.error_type == "codex_interrupted", repr(error)
        return error
    else:
        raise AssertionError("interrupted SDK turn was accepted as business success")


class ReturnedInterruptedTurn(FakeTurn):
    async def interrupt(self):
        raise AssertionError("already returned interruption does not need another interrupt")


def client_for(final, on_thread_start=None):
    calls = []
    turn = ReturnedInterruptedTurn([
        completed_item(assistant_item("final", final, "final_answer")),
        turn_completed("interrupted"),
    ])

    async def start_turn(*args, **kwargs):
        calls.append((args, kwargs))
        return turn

    async def start_thread(*args, **kwargs):
        if on_thread_start:
            on_thread_start(kwargs)
        return SimpleNamespace(id="thread-1", turn=start_turn)

    @asynccontextmanager
    async def client(*args, **kwargs):
        yield SimpleNamespace(thread_start=start_thread)

    return client, start_turn, turn, calls


def extraction_case(root):
    directory = root / "extraction"
    directory.mkdir()
    write_native_drafts(directory, "single")
    draft = directory / "draft/skill/SKILL.md"
    old = draft.read_bytes()
    old_response = directory / "response.txt"
    old_response.write_text("previous accepted response", encoding="utf-8")
    client, _, turn, calls = client_for("提炼完成，草稿已写好。")
    with patch("creatoros.integrations.skill_extraction._production_client", client), \
         patch("creatoros.integrations.skill_draft_files.read_file_drafts",
               side_effect=AssertionError("interrupted draft must not be validated as ready")):
        assert_interrupted(lambda: asyncio.run(sdk_extract(
            directory, [], "single", "test", Event(), lambda _: None)))
    assert len(calls) == 1 and turn.stream_calls == 1
    assert draft.read_bytes() == old
    assert old_response.read_text(encoding="utf-8") == "previous accepted response"
    assert json.loads((directory / "production_progress.json").read_text())["status"] == "interrupted"

    # The real worker's authoritative job status also follows SDK interruption,
    # even when no host cancellation flag was set. Its valid draft stays on disk.
    service = SkillExtractionService(ProducerSkillCatalog(root / "extraction-catalog"))
    client, _, _, worker_calls = client_for("草稿完成", lambda controls: write_native_drafts(
        Path(controls["cwd"]).parent, "single"))
    with patch("creatoros.integrations.skill_extraction._production_client", client):
        job = service.submit("interrupted-sdk-with-draft", source_text="Reusable distinctive writing sample")
        service.worker.join(10)
        assert not service.worker.is_alive()
        saved = service.get(job["id"])
    assert saved["status"] == "interrupted" and saved["error_type"] == "codex_interrupted"
    assert not saved["skills"] and saved["revision"] == 0 and len(worker_calls) == 1
    assert (service._path("jobs", job["id"]) / "draft/skill/SKILL.md").is_file()
    assert not service.cancel_event.is_set()
    service.shutdown()


def research_case(root):
    directory = root / "research"
    directory.mkdir()
    old_response = directory / "response.txt"
    old_response.write_text("old verified candidates", encoding="utf-8")
    controlled = ControlledSdk("success", Event())
    original_stream = ControlledTurn.stream

    def interrupted_stream(self):
        async def events():
            async for event in original_stream(self):
                yield turn_completed("interrupted") if event.method == "turn/completed" else event
        return events()

    researcher = CodexTopicResearcher(timeout_seconds=2)
    with patch("openai_codex.AsyncCodex", side_effect=controlled.build), \
         patch.object(ControlledTurn, "stream", interrupted_stream):
        error = assert_interrupted(lambda: researcher.research(
            {"series": {"name": "English"}}, 1, "common words", directory, controlled.cancel))
    assert len(controlled.turn_calls) == 1 and controlled.turn_handle.stream_calls == 1
    assert old_response.read_text(encoding="utf-8") == "old verified candidates"
    receipt = json.loads((directory / "worker_receipt.json").read_text(encoding="utf-8"))
    assert receipt["turns"][-1]["status"] == "interrupted"
    trace = [json.loads(line) for line in (directory / "codex_trace.jsonl").read_text(encoding="utf-8").splitlines()]
    assert not any(event.get("type") == "turn.completed" and "thread_id" in event for event in trace)
    assert json.loads((directory / "production_progress.json").read_text())["status"] == "interrupted"
    service = TopicResearchService(None, ProducerSkillCatalog(root / "research-catalog"))
    record = {"id": "a" * 32, "status": "researching", "progress": {"events": []}}
    service._fail(record, error)
    assert record["status"] == "interrupted" and record["error_type"] == "codex_interrupted"
    assert not service.cancel.is_set()


def discussion_case(root):
    directory = root / "discussion"
    directory.mkdir()
    original = directory / "original-artifact.txt"
    original.write_bytes(b"immutable original artifact")
    client, _, turn, calls = client_for("图片没有问题，讨论完成。")
    with patch("creatoros.integrations.content_discussion._production_client", client):
        assert_interrupted(lambda: CodexDiscussionReviewer(timeout_seconds=2).review(
            directory, "analyze only", [], None, None, Event(), lambda *_: None, lambda *_: None))
    assert len(calls) == 1 and turn.stream_calls == 1
    assert original.read_bytes() == b"immutable original artifact"
    assert not (directory / "reply.txt").exists()
    receipt = json.loads((directory / "worker_receipt.json").read_text(encoding="utf-8"))
    assert receipt["turns"][-1]["status"] == "interrupted"

    service = ContentDiscussionService(None, None, root / "discussion-state", CodexDiscussionReviewer(timeout_seconds=2))
    record = {"id": "interrupted-discussion", "status": "queued", "thread_id": None,
              "source_thread_id": None, "context": {}, "reply": "", "events": []}
    client, _, _, worker_calls = client_for("讨论完成")
    with patch("creatoros.integrations.content_discussion._production_client", client):
        service._execute(record, directory, "analyze only", [])
    assert record["status"] == "interrupted" and len(worker_calls) == 1
    assert not (directory / "reply.txt").exists() and not service.cancel.is_set()
    assert original.read_bytes() == b"immutable original artifact"


def legacy_case(root):
    directory = root / "legacy"
    directory.mkdir()
    skill = directory / "SKILL.md"
    skill.write_text("---\nname: mind\ndescription: test\n---\n", encoding="utf-8")
    response = directory / "mind_response.txt"
    response.write_text("original validated storyboard", encoding="utf-8")
    final = json.dumps({"research_brief": "source", "causal_chain": "a -> b",
                        "pages": [{"order": 1, "page_spec": "valid page"}]})
    _, start_turn, turn, calls = client_for(final)
    producer = CodexSdkProducer(project_root=root, generated_images_root=root / "generated", timeout_seconds=2)
    assert_interrupted(lambda: asyncio.run(producer._execute_stage_async(
        "test", directory, skill_name="mind", skill_path=skill,
        receipt_model=StoryboardReceipt, stage="mind", on_thread_started=None, cancel_event=Event(),
        thread=SimpleNamespace(id="thread-1", turn=start_turn))))
    assert len(calls) == 1 and turn.stream_calls == 1
    assert response.read_text(encoding="utf-8") == "original validated storyboard"
    assert json.loads((directory / "production_progress.json").read_text())["status"] == "interrupted"


def native_case(root, *, interrupt_review):
    case = root / ("composition" if interrupt_review else "delivery")
    case.mkdir()
    catalog, pair = create_pair(case)
    directory = case / "attempt"
    checkpoint = prepare_attempt(directory, pair, catalog)
    marker = directory / "old-valid-file.txt"
    marker.write_bytes(b"retain accepted evidence")
    NativeTransport.reset(case / "generated")
    producer = CodexSdkProducer(project_root=case, generated_images_root=case / "generated", timeout_seconds=2)
    original_stream = Turn.stream
    refs = [("local-mind", directory / "skills/mind/SKILL.md"),
            ("local-maker", directory / "skills/production/SKILL.md")]

    async def interrupted_stream(self):
        async for event in original_stream(self):
            status = "interrupted" if self.is_review == interrupt_review else "completed"
            yield turn_completed(status) if event.method == "turn/completed" else event

    with patch("openai_codex.AsyncCodex", NativeTransport), patch.object(Turn, "stream", interrupted_stream):
        assert_interrupted(lambda: asyncio.run(execute(
            producer, directory, refs, "test", checkpoint, None, Event())))
    persisted = json.loads((directory / CHECKPOINT).read_text(encoding="utf-8"))
    assert not persisted["turn_completed"] and not persisted["repair_attempted"]
    assert marker.read_bytes() == b"retain accepted evidence"
    assert len(NativeTransport.reviews) == 1
    assert len(NativeTransport.calls) == (0 if interrupt_review else 1)
    assert not NativeTransport.interrupted, "normal SDK interruption must not initiate another turn"
    if interrupt_review:
        assert persisted["composition_review"] is None and not NativeTransport.generated
    else:
        assert (directory / "work/delivery.json").is_file(), "valid files must remain for explicit recovery"
        assert len(persisted["pages"]) == 1
        assert NativeTransport.generated == {("recovery-thread", 1): 1}, "no image or repair retry"
    receipt = json.loads((directory / "worker_receipt.json").read_text(encoding="utf-8"))
    assert receipt["turns"][-1]["status"] == "interrupted"


def main():
    class Status(Enum):
        COMPLETED = "completed"
    require_completed_turn(SimpleNamespace(status=Status.COMPLETED))
    for status, expected in (("interrupted", "codex_interrupted"), ("failed", "codex_turn_failed"),
                             (None, "codex_protocol_error"), ("inProgress", "codex_protocol_error")):
        try:
            require_completed_turn(SimpleNamespace(status=status, final_response="valid final"))
        except CodexProducerError as error:
            assert error.error_type == expected
        else:
            raise AssertionError(f"non-completed status accepted: {status}")
    with TemporaryDirectory(prefix="codex-completed-turn-") as temporary:
        root = Path(temporary)
        extraction_case(root)
        research_case(root)
        discussion_case(root)
        legacy_case(root)
        native_case(root, interrupt_review=True)
        native_case(root, interrupt_review=False)
    print("codex_completed_turn=passed extraction/research/discussion/legacy/composition/delivery "
          "interrupted-final-rejected=true valid-files-retained=true no-retry=true")


if __name__ == "__main__":
    main()
