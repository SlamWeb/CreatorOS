import json

import creatoros.tools.personclone as personclone_tools
from creatoros.ai.types import ToolCall
from creatoros.integrations.personclone import AuthorJobStatus, PersonaAnswer
from creatoros.tools.definitions import tool_registry
from creatoros.tools.execution import execute_tool_call


class FakePersonCloneClient:
    def __init__(self):
        self.closed = False
        self.job_polls = 0

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

    def get_author_job(self, job_id):
        assert job_id == "job-1"
        self.job_polls += 1
        if self.job_polls > 1:
            return AuthorJobStatus(
                id="job-1",
                author="alice",
                status="ready",
                stage="ready",
                label="作者领域画像已就绪",
            )
        return AuthorJobStatus(
            id="job-1",
            author="alice",
            status="running",
            stage="clustering",
            label="正在生成作者领域画像",
        )

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


class StuckPersonCloneClient(FakePersonCloneClient):
    def get_author_job(self, job_id):
        assert job_id == "job-1"
        return AuthorJobStatus(
            id="job-1",
            author="alice",
            status="running",
            stage="indexing",
            label="正在建立索引",
        )


def main():
    clients = []
    shared_client = FakePersonCloneClient()
    previous_factory = personclone_tools._client_factory

    def factory():
        clients.append(shared_client)
        return shared_client

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
        refreshed = execute_tool_call(
            ToolCall("2b", "get_author_job", json.dumps({"job_id": "job-1"}))
        )
        waited = execute_tool_call(
            ToolCall(
                "2c",
                "wait_author_job",
                json.dumps({"job_id": "job-1", "timeout_seconds": 1}),
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
    assert "任务句柄：job-1" in job.content
    assert job.details == {
        "task_id": "job-1",
        "kind": "author_index",
        "author": "alice",
        "status": "queued",
        "stage": None,
        "label": None,
        "updated_at": None,
        "error_message": None,
    }
    assert refreshed.content == "作者任务 job-1 当前状态：running/clustering。正在生成作者领域画像"
    assert refreshed.details["stage"] == "clustering"
    assert waited.content == "作者任务 job-1 当前状态：ready/ready。作者领域画像已就绪"
    assert waited.details["poll_count"] == 1
    assert waited.details["timed_out"] is False
    assert answer.content == "Alice 的回答"
    assert answer.details["trace_id"] == "trace-1"
    assert all(client.closed for client in clients)
    assert {"add_author", "get_author_job", "wait_author_job"}.issubset(tool_registry)

    personclone_tools._client_factory = StuckPersonCloneClient
    timed_out = execute_tool_call(
        ToolCall(
            "2d",
            "wait_author_job",
            json.dumps({"job_id": "job-1", "timeout_seconds": 1, "poll_interval_seconds": 0.1}),
        )
    )
    personclone_tools._client_factory = previous_factory
    assert timed_out.is_error
    assert timed_out.error_type == "author_job_wait_timeout"
    assert timed_out.details["timed_out"] is True
    print("personclone_tools_smoke=passed")


if __name__ == "__main__":
    main()
