from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from creatoros.ai.types import ModelUsage
from creatoros.session.checkpoint import (
    CompactionCheckpoint,
    checkpoint_path,
    clear_compaction_checkpoint,
    load_compaction_checkpoint,
    save_compaction_checkpoint,
)


def main():
    messages = [
        {"role": "system", "content": "stable"},
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "recent"},
        {"role": "assistant", "content": "recent answer"},
    ]
    checkpoint = CompactionCheckpoint.create(
        summary="## Goal\ncontinue",
        messages=messages,
        first_retained_index=3,
        tokens_before=900_000,
        usage=ModelUsage(800, 200, 1_000),
    )
    assert checkpoint.source_message_count == 5
    assert checkpoint.retained_messages == tuple(messages[3:])

    with TemporaryDirectory() as directory:
        session_file = Path(directory) / "latest.json"
        saved_path = save_compaction_checkpoint(checkpoint, session_file)
        assert saved_path == checkpoint_path(session_file)
        loaded = load_compaction_checkpoint(messages, session_file)
        assert loaded == checkpoint

        appended = [*messages, {"role": "user", "content": "new"}]
        assert load_compaction_checkpoint(appended, session_file) == checkpoint

        changed = deepcopy(messages)
        changed[1]["content"] = "rewritten"
        assert load_compaction_checkpoint(changed, session_file) is None

        saved_path.write_text("{broken", encoding="utf-8")
        assert load_compaction_checkpoint(messages, session_file) is None

        saved_path.write_text("[]", encoding="utf-8")
        assert load_compaction_checkpoint(messages, session_file) is None

        clear_compaction_checkpoint(session_file)
        assert not saved_path.exists()

    print("compaction_checkpoint_smoke=passed")


if __name__ == "__main__":
    main()
