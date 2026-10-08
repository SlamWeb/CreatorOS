"""Exercise model-facing discussion/revision tools through real loopback HTTP routes."""
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Timer
from types import SimpleNamespace
import json

from creatoros.tools.content_discussion import (
    discuss_content_run, get_content_discussion, request_content_revision,
)
from creatoros.tools.studio import get_content_run
from tests.agent_studio_support import serve
from tests.studio_review_fixtures import make_fixture


class Reviewer:
    def __init__(self):
        self.release = Event()

    def review(self, directory, prompt, images, thread_id, source, cancel, emit, bind):
        assert len(images) == 5 and all(path.is_file() for path in images)
        bind(thread_id or "isolated-thread", "continued_discussion" if thread_id else "forked_production")
        emit("delta", "隔离检查完成。")
        assert self.release.wait(3)
        return "隔离检查完成，没有修改作品。", {"input_tokens": 1}


with TemporaryDirectory() as temporary:
    root = Path(temporary)
    database, runs, producer, app = make_fixture(root)
    run = runs.create("review-1")
    runs.execute(run.id)
    discussions = app.state.content_discussions
    reviewer = Reviewer()
    discussions.reviewer = reviewer
    session_id = app.state.chat.create("review-lab")["id"]
    updates = []
    context = SimpleNamespace(
        project_root=root, studio_url=None, agent_session_id=session_id,
        discussion_progress=updates.append, stopping=Event(),
        research_wait_timeout_seconds=5,
    )

    with serve(app) as base_url:
        context.studio_url = base_url
        detail = json.loads(get_content_run(run.id, context=context).content)
        assert detail["status"] == "awaiting_approval"
        selected = next(item for item in detail["revisions"] if item["artifact_available"])
        assert selected["revision_id"] and len(selected["artifact_digest"]) == 64
        Timer(0.9, reviewer.release.set).start()

        result = discuss_content_run(
            run_id=run.id, request_id="tool-http-discussion-1",
            revision_id=selected["revision_id"], artifact_digest=selected["artifact_digest"],
            message="只做讨论，不要改作品。", context=context,
        )
        discussed = json.loads(result.content)
        assert not result.is_error and discussed["status"] == "completed", result.content
        assert discussed["reply"] == "隔离检查完成，没有修改作品。"
        assert updates and any(item["status"] in {"queued", "running"} for item in updates)

        history = json.loads(get_content_discussion(run.id, context=context).content)
        assert history["items"][-1]["id"] == discussed["id"]

        before = json.loads(get_content_run(run.id, context=context).content)
        revised = request_content_revision(run.id, "明确要求：改一下标题", before["version"], context=context)
        revision_response = json.loads(revised.content)
        assert not revised.is_error, revised.content
        assert revision_response["version"] > before["version"]
        assert revision_response["revisions"][-1]["revision_number"] > selected["revision_number"]
        assert revision_response["revisions"][-1]["artifact_digest"] is None

    database.close()

print("content_discussion_tool_http=passed actual_routes=discussion_revision_run_projection=true")
