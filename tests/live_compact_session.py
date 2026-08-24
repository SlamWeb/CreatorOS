import os
from pathlib import Path
from tempfile import TemporaryDirectory

from dotenv import load_dotenv

from creatoros.agent.compactor import compact_session
from creatoros.ai.context import estimate_tokens
from creatoros.ai.deepseek import DeepSeekProvider
from creatoros.session.checkpoint import load_compaction_checkpoint


def main():
    load_dotenv()
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY。")

    old_turn = [
        {"role": "user", "content": "记录 CreatorOS checkpoint 决策。"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-compact-1",
                    "name": "read_file",
                    "arguments": '{"path":"D:\\\\CreatorOS\\\\SPEC.md"}',
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-compact-1",
            "content": "status=200\ncheckpoint=atomic\n" + "x" * 4_100,
        },
        {"role": "assistant", "content": "checkpoint 决策记录完成。"},
    ]
    recent_turn = [
        {"role": "user", "content": "继续学习自动压缩。"},
        {"role": "assistant", "content": "下一步连接 ContextBudget。"},
    ]
    messages = [
        {"role": "system", "content": "CreatorOS test"},
        *old_turn,
        *recent_turn,
    ]

    with TemporaryDirectory() as directory:
        session_file = Path(directory) / "latest.json"
        checkpoint = compact_session(
            DeepSeekProvider(api_key=api_key),
            messages,
            tools=[{"type": "function", "function": {"name": "read_file"}}],
            session_file=session_file,
            keep_recent_tokens=estimate_tokens(recent_turn),
            custom_instructions=(
                "保留 D:\\CreatorOS\\SPEC.md、call-compact-1、status=200 "
                "和 checkpoint=atomic"
            ),
        )
        assert checkpoint is not None
        assert "D:\\CreatorOS\\SPEC.md" in checkpoint.summary
        assert "call-compact-1" in checkpoint.summary
        assert checkpoint.first_retained_index == 5
        assert checkpoint.retained_messages == tuple(recent_turn)
        assert checkpoint.usage is not None
        assert load_compaction_checkpoint(messages, session_file) == checkpoint
        print("live_compact_session=passed")
        print(f"input_tokens={checkpoint.usage.input_tokens}")
        print(f"output_tokens={checkpoint.usage.output_tokens}")
        print(f"first_retained_index={checkpoint.first_retained_index}")


if __name__ == "__main__":
    main()
