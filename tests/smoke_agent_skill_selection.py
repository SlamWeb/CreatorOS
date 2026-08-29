import json
from io import StringIO

from creatoros.agent import loop as agent_loop
from creatoros.ai.types import StreamEnd, TextDelta, ToolCallDelta
from creatoros.terminal import Console
from creatoros.tools import tools
from creatoros.tools.results import ToolResult


class FakeProvider:
    def __init__(self):
        self.calls = 0
        self.contexts = []

    def stream(self, context):
        self.contexts.append(context)
        self.calls += 1
        if self.calls == 1:
            yield ToolCallDelta(0, "read-1", "read_file", '{"path":"creatoros/skills/route_and_answer/SKILL.md"}')
            yield StreamEnd("tool_calls")
        elif self.calls == 2:
            yield ToolCallDelta(0, "route-1", "route_hotspots", '{"limit":3,"top_k":1}')
            yield StreamEnd("tool_calls")
        elif self.calls == 3:
            yield TextDelta("候选已准备好，请选择一个作者队列中的热点。")
            yield StreamEnd("stop")
        elif self.calls == 4:
            yield ToolCallDelta(0, "ask-1", "ask_author", '{"author":"alice","question":"平台治理"}')
            yield StreamEnd("tool_calls")
        else:
            yield TextDelta("已完成这一个作者和热点的回答。")
            yield StreamEnd("stop")


def main():
    provider = FakeProvider()
    original_load = agent_loop.load_messages
    original_save = agent_loop.save_messages
    original_execute = agent_loop.execute_tool_call
    calls = []

    def fake_execute(tool_call, context=None):
        calls.append(tool_call.name)
        if tool_call.name == "read_file":
            return ToolResult(content="---\nname: route-and-answer\n---\n# Route and Answer")
        if tool_call.name == "route_hotspots":
            return ToolResult(
                content=json.dumps(
                    {
                        "plans": [
                            {
                                "author_id": "alice",
                                "display_name": "Alice",
                                "hot": [{"position": 1, "rank": 2, "title": "平台治理"}],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            )
        if tool_call.name == "ask_author":
            return ToolResult(content="Alice 的数字分身回答")
        raise AssertionError(f"unexpected tool: {tool_call.name}")

    agent_loop.load_messages = lambda: [{"role": "system", "content": "test"}]
    agent_loop.save_messages = lambda messages: None
    agent_loop.execute_tool_call = fake_execute
    try:
        inputs = iter(["按 route-and-answer 找热点", "选择 Alice 队列第 1 个", "/exit"])
        output = StringIO()
        agent_loop.run_agent(
            provider,
            console=Console(input_fn=lambda prompt: next(inputs), output=output),
            max_turns=6,
        )
    finally:
        agent_loop.load_messages = original_load
        agent_loop.save_messages = original_save
        agent_loop.execute_tool_call = original_execute

    assert calls == ["read_file", "route_hotspots", "ask_author"]
    assert "route_and_answer" not in {
        schema["function"]["name"] for schema in tools
    }
    second_messages, _ = provider.contexts[1].to_request()
    assert any("# Route and Answer" in str(message.get("content")) for message in second_messages)
    fourth_messages, _ = provider.contexts[3].to_request()
    assert any("平台治理" in str(message.get("content")) for message in fourth_messages)
    assert "候选已准备好" in output.getvalue()
    assert "已完成这一个作者和热点的回答" in output.getvalue()
    print("agent_skill_selection_smoke=passed")


if __name__ == "__main__":
    main()
