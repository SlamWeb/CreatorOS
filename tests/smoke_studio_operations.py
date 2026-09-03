import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from creatoros.storage import Database, upgrade_database
from creatoros.web import create_app


class SyncASGIClient:
    def __init__(self, app):
        self.app = app

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        return asyncio.run(self._request(method, path, **kwargs))

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, path, **kwargs)

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
        creator_response = client.request(
            "POST",
            "/api/creators",
            json={"display_name": "面试知识实验室", "account_handle": "demo", "daily_content_limit": 3},
        )
        assert creator_response.status_code == 201
        creator = creator_response.json()
        assert creator["id"].startswith("creator-")
        assert creator["platform"] == "xiaohongshu"

        series_response = client.request(
            "POST",
            f"/api/creators/{creator['id']}/series",
            json={
                "name": "Agent 每日一题",
                "description": "用轮播图讲清一个 Agent 工程知识点。",
                "audience": "准备 AI Agent 开发面试的初学者",
            },
        )
        assert series_response.status_code == 201
        series = series_response.json()
        assert series["skill_name"] == "knowledge-to-carousel"
        duplicate = client.request(
            "POST",
            f"/api/creators/{creator['id']}/series",
            json={"name": "Agent 每日一题"},
        )
        assert duplicate.status_code == 409

        plan = {
            "schema_version": 1,
            "operations": [
                {
                    "action": "add_topics",
                    "series_id": series["id"],
                    "topics": [
                        {"topic_id": "topic-state", "title": "AgentState 和 Messages 有什么区别？"},
                        {"topic_id": "topic-tools", "title": "Tool Calling 为什么需要 Schema？"},
                    ],
                }
            ],
        }
        preview_response = client.request(
            "POST",
            "/api/operations/preview",
            json={"request_text": "先加入两个 Agent 面试选题", "plan": plan},
        )
        assert preview_response.status_code == 201
        preview = preview_response.json()
        assert preview["status"] == "awaiting_approval"
        assert preview["revision"] == preview["version"] == 1
        assert preview["confirmation_token"]
        assert client.request("GET", f"/api/series/{series['id']}/topics").json()["page"]["total"] == 0

        stale = client.request(
            "POST",
            f"/api/operations/{preview['id']}/confirm",
            json={
                "expected_version": 2,
                "expected_revision": 1,
                "confirmation_token": preview["confirmation_token"],
            },
        )
        assert stale.status_code == 409
        assert client.request("GET", f"/api/series/{series['id']}/topics").json()["page"]["total"] == 0

        confirmed = client.request(
            "POST",
            f"/api/operations/{preview['id']}/confirm",
            json={
                "expected_version": 1,
                "expected_revision": 1,
                "confirmation_token": preview["confirmation_token"],
            },
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["status"] == "succeeded"
        topic_page = client.request("GET", f"/api/series/{series['id']}/topics").json()
        assert [item["title"] for item in topic_page["items"]] == [
            "AgentState 和 Messages 有什么区别？",
            "Tool Calling 为什么需要 Schema？",
        ]

        idempotent = client.request(
            "POST",
            f"/api/operations/{preview['id']}/confirm",
            json={
                "expected_version": 1,
                "expected_revision": 1,
                "confirmation_token": preview["confirmation_token"],
            },
        )
        assert idempotent.status_code == 200
        assert len(client.request("GET", f"/api/series/{series['id']}/topics").json()["items"]) == 2

        invalid_creator = client.request("POST", "/api/creators", json={"display_name": "  "})
        assert invalid_creator.status_code == 422

    database.close()

print("studio_operations_smoke=passed create=preview=confirm=passed stale=blocked idempotent=passed")
