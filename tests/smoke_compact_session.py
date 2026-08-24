from pathlib import Path
from tempfile import TemporaryDirectory

from creatoros.agent.compactor import compact_session
from creatoros.ai.context import estimate_tokens
from creatoros.ai.types import ModelResponse, ModelUsage
from creatoros.session.checkpoint import load_compaction_checkpoint


def summary(goal: str) -> str:
    return f"""## Goal
{goal}
## Constraints & Preferences
none
## Progress
### Done
done
### In Progress
none
### Blocked
none
## Key Decisions
none
## Important Facts & IDs
none
## Files & Artifacts
none
## Next Steps
none
## Unresolved Questions
none"""


class RecordingProvider:
    context_window = 100_000
    reserve_output_tokens = 10_000

    def __init__(self):
        self.contexts = []

    def complete(self, context):
        self.contexts.append(context)
        return ModelResponse(
            content=summary(f"checkpoint-{len(self.contexts)}"),
            tool_calls=[],
            usage=ModelUsage(500, 100, 600),
        )


def main():
    old_turn = [
        {"role": "user", "content": "old request"},
        {"role": "assistant", "content": "old answer"},
    ]
    recent_turn = [
        {"role": "user", "content": "recent request"},
        {"role": "assistant", "content": "recent answer"},
    ]
    messages = [
        {"role": "system", "content": "stable"},
        *old_turn,
        *recent_turn,
    ]
    provider = RecordingProvider()

    with TemporaryDirectory() as directory:
        session_file = Path(directory) / "latest.json"
        checkpoint = compact_session(
            provider,
            messages,
            tools=[{"type": "function"}],
            session_file=session_file,
            keep_recent_tokens=estimate_tokens(recent_turn),
        )
        assert checkpoint is not None
        assert checkpoint.first_retained_index == 3
        assert checkpoint.retained_messages == tuple(recent_turn)
        assert load_compaction_checkpoint(messages, session_file) == checkpoint
        assert not provider.contexts[0].tools

        no_op = compact_session(
            provider,
            messages,
            tools=[],
            checkpoint=checkpoint,
            session_file=session_file,
            keep_recent_tokens=10_000,
        )
        assert no_op is None
        assert len(provider.contexts) == 1

        new_turn = [
            {"role": "user", "content": "new request"},
            {"role": "assistant", "content": "new answer"},
        ]
        extended = [*messages, *new_turn]
        updated = compact_session(
            provider,
            extended,
            tools=[],
            checkpoint=checkpoint,
            session_file=session_file,
            keep_recent_tokens=estimate_tokens(new_turn),
        )
        assert updated is not None
        assert updated.first_retained_index == 5
        assert updated.retained_messages == tuple(new_turn)
        prompt = provider.contexts[-1].messages[0]["content"]
        assert "checkpoint-1" in prompt
        assert "recent request" in prompt
        assert "old request" not in prompt

    print("compact_session_smoke=passed")


if __name__ == "__main__":
    main()
