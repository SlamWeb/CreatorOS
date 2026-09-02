import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

import creatoros.tools.content as content_tools
from creatoros.ai.types import ToolCall
from creatoros.integrations.codex import (
    CodexProducer,
    CodexProducerError,
    CodexRun,
    CodexUsage,
    ProducedCard,
    ProductionCopy,
    ProductionReceipt,
    parse_codex_jsonl,
)
from creatoros.content import SocialContentPack
from creatoros.tools.definitions import tool_registry
from creatoros.tools.execution import execute_tool_call


def receipt(image_path: Path) -> ProductionReceipt:
    return ProductionReceipt(
        content_summary="用餐厅协作解释异步等待。",
        cards=[
            ProducedCard(
                order=1,
                kind="cover",
                section=None,
                headline="async/await 是怎么协作的？",
                body=None,
                highlights=["等待时做别的事"],
                visual_brief=None,
                source_image_path=str(image_path),
            )
        ],
        publish_copy=ProductionCopy(
            title="把 async/await 想成一家餐厅",
            body="等待后厨时，服务员可以先服务下一桌。",
            hashtags=["#Python"],
        ),
        sources=[],
    )


def jsonl(value: ProductionReceipt) -> str:
    events = [
        {"type": "thread.started", "thread_id": "thread-1"},
        {"type": "error", "message": "Reconnecting... 1/5"},
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": value.model_dump_json()},
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 100,
                "cached_input_tokens": 80,
                "output_tokens": 20,
                "reasoning_output_tokens": 5,
            },
        },
    ]
    return "\n".join(json.dumps(item, ensure_ascii=False) for item in events)


class FakeCodexProducer(CodexProducer):
    def __init__(self, *, project_root: Path, generated_images_root: Path, run: CodexRun):
        super().__init__(
            project_root=project_root,
            generated_images_root=generated_images_root,
        )
        self.run = run

    def _execute(
        self,
        prompt: str,
        working_directory: Path,
        *,
        thread_id: str | None = None,
        on_thread_started=None,
    ) -> CodexRun:
        assert "receipt mode" in prompt
        assert working_directory.is_dir()
        if on_thread_started:
            on_thread_started(self.run.thread_id)
        return self.run


def main() -> None:
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        source_skill = Path(__file__).parents[1] / "creatoros" / "skills" / "knowledge-to-carousel"
        shutil.copytree(
            source_skill,
            root / "creatoros" / "skills" / "knowledge-to-carousel",
        )
        generated_root = root / "codex-generated"
        source = generated_root / "thread-1" / "exec-image.png"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"fake png")
        parsed = parse_codex_jsonl(jsonl(receipt(source)))
        assert parsed.thread_id == "thread-1"
        assert parsed.usage.cached_input_tokens == 80

        producer = FakeCodexProducer(
            project_root=root,
            generated_images_root=generated_root,
            run=parsed,
        )
        previous = content_tools._producer_factory
        content_tools._producer_factory = lambda: producer
        try:
            result = execute_tool_call(
                ToolCall(
                    "produce-1",
                    "produce_content_pack",
                    json.dumps(
                        {
                            "creator_id": "creatoros-lab",
                            "series_id": "python-basics",
                            "topic_id": "async-await",
                            "topic_title": "Python async/await 就像餐厅协作",
                        }
                    ),
                )
            )
        finally:
            content_tools._producer_factory = previous

        assert not result.is_error, result.content
        payload = json.loads(result.content)
        pack = SocialContentPack.load(payload["output_directory"])
        assert payload["thread_id"] == "thread-1"
        assert payload["card_count"] == 1
        assert pack.cards[0].image_path == "images/01-cover.png"
        assert (Path(payload["output_directory"]) / "production_session.json").is_file()

        outside = root / "outside.png"
        outside.write_bytes(b"not this thread")
        unsafe_directory = root / "unsafe-pack"
        unsafe_directory.mkdir()
        unsafe_run = CodexRun("thread-1", receipt(outside), CodexUsage())
        try:
            producer._materialize(
                unsafe_run,
                directory=unsafe_directory,
                pack_id="unsafe-pack",
                creator_id="creatoros-lab",
                series_id="python-basics",
                topic_id="unsafe",
                topic_title="unsafe",
                generated_at="2026-09-02T00:00:00+08:00",
            )
        except CodexProducerError as error:
            assert error.error_type == "unsafe_generated_image_path"
        else:
            raise AssertionError("当前 thread 之外的图片路径应该被拒绝。")

    schema = tool_registry["produce_content_pack"].to_schema()["function"]
    assert set(schema["parameters"]["required"]) == {
        "creator_id",
        "series_id",
        "topic_id",
        "topic_title",
    }
    invalid = execute_tool_call(
        ToolCall(
            "produce-2",
            "produce_content_pack",
            json.dumps(
                {
                    "creator_id": "../escape",
                    "series_id": "python-basics",
                    "topic_id": "async-await",
                    "topic_title": "bad",
                }
            ),
        )
    )
    assert invalid.is_error and invalid.error_type == "invalid_arguments"
    print("codex_producer_smoke=passed thread=thread-1 cards=1")


if __name__ == "__main__":
    main()
