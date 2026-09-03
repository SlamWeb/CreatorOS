import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from creatoros.storage import (
    ContentRepository,
    CreatorPlatform,
    Database,
    TopicSource,
    upgrade_database,
)
from creatoros.runs import ContentRunService
from creatoros.web import create_app


class SyncASGIClient:
    """Small synchronous facade for httpx's current ASGI transport."""

    def __init__(self, app):
        self.app = app

    def get(self, path: str) -> httpx.Response:
        return asyncio.run(self._get(path))

    async def _get(self, path: str) -> httpx.Response:
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(path)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


with TemporaryDirectory() as temporary_directory:
    database_path = Path(temporary_directory) / "creatoros.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    upgrade_database(database_url)
    database = Database(database_url)
    app = create_app(database=database)

    with SyncASGIClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["writable_routes_enabled"] is False
        openapi = client.get("/openapi.json").json()
        assert all(
            not {"post", "put", "patch", "delete"}.intersection(path_item)
            for path_item in openapi["paths"].values()
        )

        empty = client.get("/api/overview")
        assert empty.status_code == 200
        assert empty.json()["counts"] == {
            "creator_count": 0,
            "active_creator_count": 0,
            "series_count": 0,
            "active_series_count": 0,
            "producing_count": 0,
            "awaiting_approval_count": 0,
        }
        assert empty.json()["creators"] == []
        assert client.get("/api/creators").json()["page"]["total"] == 0
        assert client.get("/api/operations").json()["page"]["total"] == 0
        missing = client.get("/api/creators/missing")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "not_found"
        invalid_page = client.get("/api/creators?limit=101")
        assert invalid_page.status_code == 422
        assert invalid_page.json()["error"]["code"] == "invalid_request"

        repository = ContentRepository(database)
        creator = repository.create_creator(
            creator_id="interview-lab",
            display_name="面试知识实验室",
            platform=CreatorPlatform.XIAOHONGSHU,
        )
        series = repository.create_series(
            series_id="agent-basics",
            creator_id=creator.id,
            name="Agent 每日一题",
            description="用图片讲清 Agent 工程知识。",
            audience="准备面试的开发者",
            skill_name="knowledge-to-carousel",
        )
        topic = repository.add_topic(
            topic_id="agent-state",
            series_id=series.id,
            title="AgentState 和 Messages 有什么区别？",
            source=TopicSource.MANUAL,
            brief="用一个生活类比解释。",
        )

        overview = client.get("/api/overview").json()
        assert overview["counts"]["creator_count"] == 1
        assert overview["counts"]["series_count"] == 1
        assert overview["creators"][0]["series"][0]["available_topic_count"] == 1

        topic_page = client.get(f"/api/series/{series.id}/topics").json()
        assert topic_page["items"][0]["available_actions"] == ["start"]
        assert topic_page["items"][0]["brief"] == "用一个生活类比解释。"

        run = ContentRunService(database).create(topic.id)
        detail_response = client.get(f"/api/runs/{run.id}")
        assert detail_response.status_code == 200
        detail = detail_response.json()
        assert detail["topic_title"] == topic.title
        assert detail["revisions"][0]["artifact_available"] is False
        assert "artifact_directory" not in detail
        assert str(database_path) not in detail_response.text

        run_page = client.get("/api/runs?status=queued").json()
        assert run_page["page"]["total"] == 1
        assert run_page["items"][0]["allowed_actions"] == ["execute", "cancel"]

        after = client.get("/api/overview").json()
        assert after["counts"]["creator_count"] == 1
        assert after["counts"]["producing_count"] == 0
        assert after["counts"]["awaiting_approval_count"] == 0

    database.close()

print("studio_api_smoke=passed empty=passed catalog=passed run_projection=passed no_write=passed")
