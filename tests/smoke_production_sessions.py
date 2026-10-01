"""Transport/fault tests for production boundaries; real SDK probe is separate."""
import asyncio
import json
from threading import Event, Timer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from openai_codex.generated import v2_all as sdk_types
from openai_codex.models import Notification

from creatoros.integrations.codex import CodexSdkProducer, CodexProducerError, PRODUCTION_CONFIG
from creatoros.integrations.skill_pair import StoryboardReceipt, VisualReceipt, join_visual, mind_prompt, visual_prompt

STORY = StoryboardReceipt.model_validate({
    "research_brief": "official sources", "causal_chain": "消息是什么 → 怎样交接 → 为什么会重复",
    "pages": [{"order": 1, "page_spec": "Page 1：消息就是程序间传递的数据，例子是订单通知。"},
              {"order": 2, "page_spec": "Page 2：承接订单通知，队列暂存消息等待消费者处理。"}],
})


class Transport:
    calls = []
    fail_visual = False
    change_page = False
    slow = False
    interrupted = []

    def __init__(self, config):
        assert config.config_overrides == PRODUCTION_CONFIG
        assert "memories.use_memories=false" in config.config_overrides
        assert "memories.generate_memories=false" in config.config_overrides

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def thread_resume(self, *args, **kwargs):
        raise AssertionError("Production must not resume old threads")

    async def thread_start(self, **kwargs):
        assert "不要读取全局记忆" in kwargs["developer_instructions"]
        thread_id = f"fresh-{len(self.calls) + 1}"
        class Thread:
            id = thread_id
            async def turn(inner, inputs, **controls):
                assert len(inputs) == 2  # current request plus only this stage's Skill
                assert inputs[1].path in inputs[0].text  # unregistered native references may not be injected
                Transport.calls.append((inner.id, inputs, controls))
                name = inputs[1].name
                class Turn:
                    id = "turn"

                    async def interrupt(self):
                        Transport.interrupted.append(self)
                    async def stream(self):
                        if Transport.slow:
                            await asyncio.sleep(0.1)
                        if name == "mind":
                            value = STORY.model_dump()
                        else:
                            assert name == "xiaobai"
                            if Transport.fail_visual:
                                raise RuntimeError("Injected visual failure")
                            cards = [dict(order=p.order, kind="content", section=None, headline=f"page {p.order}",
                                          body=None, highlights=[], visual_brief=None,
                                          source_image_path=f"C:/unused/{p.order}.png") for p in STORY.pages]
                            value = dict(content_summary="summary", cards=cards,
                                         publish_copy=dict(title="MQ", body="body", hashtags=[]), sources=[],
                                         pages=[dict(order=p.order, image_prompt=f"prompt {p.order}",
                                                     reference_assets=["assets/character.png"]) for p in STORY.pages])
                            if Transport.change_page:
                                value["pages"][0]["page_spec"] = "rewritten content"
                        final_text = json.dumps(value, ensure_ascii=False)
                        usage = sdk_types.ThreadTokenUsageUpdatedNotification.model_validate({
                            "threadId": inner.id, "turnId": "turn",
                            "tokenUsage": {
                                "last": {"inputTokens": 4, "cachedInputTokens": 0,
                                         "outputTokens": 2, "reasoningOutputTokens": 0, "totalTokens": 6},
                                "total": {"inputTokens": 4, "cachedInputTokens": 0,
                                          "outputTokens": 2, "reasoningOutputTokens": 0, "totalTokens": 6},
                            },
                        })
                        yield Notification("item/agentMessage/delta", sdk_types.AgentMessageDeltaNotification(
                            threadId=inner.id, turnId="turn", itemId="assistant-final", delta=final_text))
                        yield Notification("item/completed", sdk_types.ItemCompletedNotification.model_validate({
                            "threadId": inner.id, "turnId": "turn", "completedAtMs": 1,
                            "item": {"type": "agentMessage", "id": "assistant-final",
                                     "phase": "final_answer", "text": final_text},
                        }))
                        yield Notification("item/commandExecution/outputDelta",
                                           sdk_types.CommandExecutionOutputDeltaNotification.model_validate({
                                               "threadId": inner.id, "turnId": "turn",
                                               "itemId": "tool-1", "delta": "PRIVATE_TOOL_OUTPUT",
                                           }))
                        yield Notification("thread/tokenUsage/updated", usage)
                        yield Notification("turn/completed", sdk_types.TurnCompletedNotification.model_validate({
                            "threadId": inner.id,
                            "turn": {"id": "turn", "status": "completed", "items": []},
                        }))
                return Turn()
        return Thread()


def execute(producer, directory, refs, *, cancel_event=None):
    directory.mkdir()
    return producer._execute(json.dumps({"topic_title": "消息队列", "topic_brief": "零基础到面试"}), directory,
                             thread_id="OLD_TASK_DO_NOT_RESUME", skill_refs=refs, cancel_event=cancel_event)


def main():
    prompt = visual_prompt("xiaobai", STORY)
    assert prompt.startswith("@xiaobai 用这个视觉 Skill 把下面整套 pages 可视化")
    assert "不依赖前后页" not in prompt and "跨页连续性" in prompt
    assert "画风" in mind_prompt("mind", "user request")
    with TemporaryDirectory(prefix="production-sessions-") as temporary:
        root = Path(temporary)
        refs = []
        for name in ("mind", "xiaobai"):
            file = root / name / "SKILL.md"
            file.parent.mkdir()
            file.write_text("fixture", encoding="utf-8")
            refs.append((name, file))
        producer = CodexSdkProducer(project_root=root, generated_images_root=root)
        with patch("openai_codex.AsyncCodex", Transport):
            for index in (1, 2):
                run = execute(producer, root / f"attempt-{index}", refs)
                assert run.thread_id == f"fresh-{index * 2}"
                assert [p.page_spec for p in run.receipt.pages] == [p.page_spec for p in STORY.pages]
                assert (root / f"attempt-{index}/visual_response.txt").is_file()
            assert [c[1][1].name for c in Transport.calls] == ["mind", "xiaobai", "mind", "xiaobai"]
            assert len({c[0] for c in Transport.calls}) == 4
            for _, inputs, _ in Transport.calls:
                if inputs[1].name == "xiaobai":
                    assert "topic_title" not in inputs[0].text  # no operational/history prompt
                    assert all(p.page_spec in inputs[0].text for p in STORY.pages)
            Transport.fail_visual = True
            try:
                execute(producer, root / "failed", refs)
                raise AssertionError("Injected failure must escape")
            except CodexProducerError:
                assert StoryboardReceipt.model_validate_json((root / "failed/storyboard.json").read_text()) == STORY
                assert (root / "failed/storyboard.md").read_text(encoding="utf-8").startswith("Page 1")
                assert json.loads((root / "failed/production_progress.json").read_text())["status"] == "failed"
                events = [json.loads(line) for line in (root / "failed/codex_trace.jsonl").read_text().splitlines()]
                assert events[-1]["type"] == "stage.failed" and events[-1]["stage"] == "visual"
                failed_metadata = (root / "failed/production_progress.json").read_text() + (root / "failed/codex_trace.jsonl").read_text()
                assert "Injected visual failure" not in failed_metadata and "PRIVATE_TOOL_OUTPUT" not in failed_metadata
            Transport.fail_visual = False
            Transport.change_page = True
            try:
                execute(producer, root / "rewritten", refs)
                raise AssertionError("Visual stage must not rewrite Mind output")
            except CodexProducerError as error:
                assert error.error_type == "invalid_production_receipt"
            Transport.change_page = False
            value = json.loads((root / "attempt-1/visual_response.txt").read_text(encoding="utf-8"))
            value["pages"].pop()
            try:
                join_visual(STORY, VisualReceipt.model_validate(value))
                raise AssertionError("Missing visual page must be rejected before materialization")
            except ValueError:
                pass
            stopped = Event()
            stopped.set()
            cancelled = root / "cancelled"
            cancelled.mkdir()
            before = len(Transport.calls)
            try:
                producer._execute("{}", cancelled, skill_refs=refs, cancel_event=stopped)
                raise AssertionError("Stopped operation must not start a new stage")
            except CodexProducerError as error:
                assert error.error_type == "codex_interrupted" and len(Transport.calls) == before
            Transport.slow = True
            producer.timeout_seconds = 10
            active_cancel = Event()
            timer = Timer(0.02, active_cancel.set)
            timer.start()
            try:
                execute(producer, root / "active-cancel", refs, cancel_event=active_cancel)
                raise AssertionError("Active cancellation must stop the production stage")
            except CodexProducerError as error:
                assert error.error_type == "codex_interrupted"
                assert json.loads((root / "active-cancel/production_progress.json").read_text())["status"] == "interrupted"
            finally:
                timer.cancel()
            producer.timeout_seconds = 0
            try:
                execute(producer, root / "timeout", refs)
                raise AssertionError("Timeout must stop before visual stage")
            except CodexProducerError as error:
                assert error.error_type == "codex_timeout"
                assert not (root / "timeout/visual_request.txt").exists()
                assert Transport.interrupted
                assert json.loads((root / "timeout/production_progress.json").read_text())["status"] == "failed"
    print("production_sessions_smoke=passed fresh_threads isolated_skills handoff failures cancel timeout unchanged_pages")


if __name__ == "__main__":
    main()
