import json

import creatoros.tools.personclone as personclone_tools
from creatoros.ai.types import ToolCall
from creatoros.integrations.personclone import PersonaAnswer
from creatoros.tools.execution import execute_tool_call


class FakePersonCloneClient:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True

    def list_personas(self):
        return {
            "default_author": "alice",
            "personas": [{
                "author": "alice",
                "display_name": "Alice",
                "index_dir": "internal",
                "narrative_schema_available": False,
            }],
        }

    def add_author(self, author, kinds, max_items):
        assert (author, kinds, max_items) == ("alice", ["answer"], 10)
        return {"id": "job-1", "status": "queued"}

    def ask_author(self, author, question, *, query_mode, writer_prompt, parent_top_k):
        assert (author, question, query_mode, writer_prompt, parent_top_k) == (
            "alice",
            "热点问题",
            "grounded",
            "strong_identity",
            20,
        )
        return PersonaAnswer(
            author=author,
            answer="Alice 的回答",
            sources=[{"title": "source-1"}],
            trace_id="trace-1",
        )


def main():
    clients = []
    previous_factory = personclone_tools._client_factory

    def factory():
        client = FakePersonCloneClient()
        clients.append(client)
        return client

    personclone_tools._client_factory = factory
    try:
        authors = execute_tool_call(ToolCall("1", "list_authors", "{}"))
        job = execute_tool_call(
            ToolCall(
                "2",
                "add_author",
                json.dumps({"author": "alice", "kinds": ["answer"], "max_items": 10}),
            )
        )
        answer = execute_tool_call(
            ToolCall("3", "ask_author", json.dumps({"author": "alice", "question": "热点问题"}))
        )
    finally:
        personclone_tools._client_factory = previous_factory

    assert not authors.is_error
    assert "index_dir" not in authors.content
    assert '"recommended_writer_prompt": "strong_identity"' in authors.content
    assert "job-1" not in job.content
    assert job.details == {
        "task_id": "job-1",
        "kind": "author_index",
        "author": "alice",
        "status": "queued",
    }
    assert answer.content == "Alice 的回答"
    assert answer.details["trace_id"] == "trace-1"
    assert all(client.closed for client in clients)
    print("personclone_tools_smoke=passed")


if __name__ == "__main__":
    main()
