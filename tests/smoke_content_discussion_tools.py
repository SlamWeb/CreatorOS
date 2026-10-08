import json
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from creatoros.tools.content_discussion import (
    DiscussContentRunArgs, CreatorTasksArgs, GetContentDiscussionArgs,
    RequestContentRevisionArgs, discuss_content_run, get_content_discussion,
    get_creator_tasks, request_content_revision,
)
from creatoros.tools.discussion_wait import observe_discussion
from creatoros.tools.definitions import tool_registry
from creatoros.tools.results import ToolResult


class Client:
    calls = []

    def __init__(self, *args, **kwargs):
        self.base_url = "http://studio.test"

    def request(self, method, path, *, payload=None, params=None):
        self.calls.append((method, path, payload, params))
        return {"status": "ok"}

    def close(self):
        pass


required_tools = {
    "discuss_content_run", "get_content_discussion",
    "request_content_revision", "get_creator_tasks",
}
assert required_tools <= tool_registry.keys()
for name in required_tools:
    assert tool_registry[name].expose_to_model

digest = "a" * 64
discuss = DiscussContentRunArgs(run_id="run/1", request_id="request-123",
                                revision_id="revision-1", artifact_digest=digest,
                                message="请分析封面")
history = GetContentDiscussionArgs(run_id="run/1")
revision = RequestContentRevisionArgs(run_id="run/1", instruction="改标题", expected_version=7)
tasks = CreatorTasksArgs(creator_id="creator-1", series_id="series-1")
context = SimpleNamespace(studio_url="http://studio.test", agent_session_id="session-1")

with patch("creatoros.tools.studio.StudioClient", Client):
    Client.calls.clear()
    result = discuss_content_run(**discuss.model_dump(), context=context)
    assert isinstance(result, ToolResult) and json.loads(result.content)["status"] == "ok"
    assert Client.calls[-1] == ("POST", "/api/runs/run%2F1/discussion", {
        "request_id": "request-123", "revision_id": "revision-1",
        "artifact_digest": digest, "message": "请分析封面"}, None)

    get_content_discussion(**history.model_dump(), context=context)
    assert Client.calls[-1] == ("GET", "/api/runs/run%2F1/discussion", None, None)

    request_content_revision(**revision.model_dump(), context=context)
    method, path, payload, _ = Client.calls[-1]
    assert method == "POST" and path == "/api/runs/run%2F1/revisions"
    assert payload["instruction"] == "改标题" and payload["expected_version"] == 7
    assert set(payload) == {"instruction", "expected_version"}

    get_creator_tasks(**tasks.model_dump(), context=context)
    assert Client.calls[-1] == ("GET", "/api/creators/creator-1/tasks", None,
                                {"series_id": "series-1"})

for model, invalid in (
    (DiscussContentRunArgs, {**discuss.model_dump(), "local_path": "C:/secret.png"}),
    (GetContentDiscussionArgs, {"run_id": "run-1", "thread_id": "arbitrary-thread"}),
):
    try:
        model.model_validate(invalid)
    except Exception:
        pass
    else:
        raise AssertionError(f"{model.__name__} accepted an out-of-contract field")


class MissingDiscussionClient:
    def request(self, method, path, *, timeout_seconds=None):
        assert method == "GET" and path == "/api/runs/run-1/discussion"
        return {"items": []}


progress = []
record = {"id": "discussion-1", "request_id": "request-1", "run_id": "run-1",
          "revision_id": "revision-1", "status": "running", "reply": "partial"}
observed = observe_discussion(MissingDiscussionClient(), record, SimpleNamespace(
    discussion_progress=progress.append, stopping=Event(), research_wait_timeout_seconds=2,
))
missing = json.loads(observed.content)
assert observed.is_error and missing["status"] == "unknown"
assert {key: missing[key] for key in ("id", "request_id", "run_id", "revision_id")} == {
    key: record[key] for key in ("id", "request_id", "run_id", "revision_id")}
assert missing["last_known_status"] == "running" and progress

print("content_discussion_tools_smoke=passed schema=bounded paths=server_owned")
