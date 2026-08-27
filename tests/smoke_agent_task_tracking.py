import json
from io import StringIO

import creatoros.tools.personclone as personclone_tools
from creatoros.agent import loop as agent_loop
from creatoros.agent.loop import run_agent
from creatoros.ai.types import StreamEnd, TextDelta, ToolCallDelta
from creatoros.terminal import Console


class FakePersonCloneClient:
    def close(self):
        pass

    def add_author(self, author, kinds, max_items):
        assert (author, kinds, max_items) == ("alice", ["answer"], 1)
        return {
            "id": "job-1",
            "author": "alice",
            "status": "running",
            "stage": "clustering",
            "label": "正在生成作者领域画像",
        }


class FakeProvider:
    def __init__(self):
        self.calls = 0

    def stream(self, context):
        del context
        self.calls += 1
        if self.calls == 1:
            yield ToolCallDelta(
                0,
                "call-1",
                "add_author",
                json.dumps({"author": "alice", "kinds": ["answer"], "max_items": 1}),
            )
            yield StreamEnd("tool_calls")
        else:
            yield TextDelta("任务已登记")
            yield StreamEnd("stop")


def main():
    previous_factory = personclone_tools._client_factory
    original_load = agent_loop.load_messages
    original_save = agent_loop.save_messages
    personclone_tools._client_factory = FakePersonCloneClient
    agent_loop.load_messages = lambda: [{"role": "system", "content": "test"}]
    agent_loop.save_messages = lambda messages: None
    try:
        events = []
        inputs = iter(["添加 alice", "/exit"])
        run_agent(
            FakeProvider(),
            on_agent_event=events.append,
            console=Console(input_fn=lambda prompt: next(inputs), output=StringIO()),
            max_turns=2,
        )
    finally:
        personclone_tools._client_factory = previous_factory
        agent_loop.load_messages = original_load
        agent_loop.save_messages = original_save

    updates = [event for event in events if event.kind == "task_updated"]
    assert len(updates) == 1
    assert updates[0].data == {
        "task_id": "job-1",
        "kind": "author_index",
        "status": "running",
        "progress": "正在生成作者领域画像",
    }
    print("agent_task_tracking_smoke=passed")


if __name__ == "__main__":
    main()
