"""Real isolated HTTP and SQLite. No remote note is published or read."""
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from creatoros.storage import ContentRepository, CreatorPlatform
from tests.studio_review_fixtures import make_fixture


class NoRemote:
    async def identity(self):
        raise AssertionError("Empty analytics must not call MCP")

    async def publish(self, *args):
        raise AssertionError("Feedback must never publish")


def main():
    with TemporaryDirectory(prefix="creatoros-feedback-http-") as temp:
        db, runs, _, app = make_fixture(Path(temp))
        app.state.publishing.client = NoRemote()
        ContentRepository(db).create_creator(creator_id="another-account", display_name="隔离另一账号",
                                            platform=CreatorPlatform.XIAOHONGSHU)
        with TestClient(app, base_url="http://127.0.0.1:8765") as web:
            owner = web.post("/api/agent/sessions", json={"creator_id": "review-lab"}).json()["id"]
            other = web.post("/api/agent/sessions", json={"creator_id": "another-account"}).json()["id"]
            target = "/api/creators/review-lab/analytics"
            own = {"x-creatoros-agent-session": owner}
            foreign = {"x-creatoros-agent-session": other}
            initial = web.get(target, headers=own)
            assert initial.status_code == 200, initial.text
            assert initial.headers["cache-control"] == "no-store"
            data = initial.json()
            assert not data["enabled"] and not data["checking"] and data["notes"] == []
            assert data["last_updated_at"] is None
            assert data["summary"]["notes"] == data["summary"]["sampled_notes"] == 0
            for method, suffix, payload in [("GET", "", None), ("PUT", "/settings", {"enabled": True}),
                                             ("POST", "/refresh", {})]:
                assert web.request(method, target + suffix, json=payload, headers=foreign).status_code == 403
            for payload in ({"enabled": "true"}, {"enabled": True, "account_id": "override"}, {}):
                assert web.put(target + "/settings", json=payload, headers=own).status_code == 422
            assert web.put(target + "/settings", json={"enabled": True}, headers=own).json()["enabled"]
            persisted = web.get(target, headers=own).json()
            assert persisted["enabled"] and persisted["heartbeat_seconds"] == 300
            assert persisted["refresh_seconds"] == 1800 and persisted["window_days"] == 7
            assert web.get("/api/creators/missing/analytics").status_code == 404
            assert web.post(target + "/refresh", json={}, headers=own).status_code == 200
            assert web.put(target + "/settings", json={"enabled": False}, headers=own).json()["enabled"] is False
            assert web.get(target).json()["enabled"] is False
            assert web.get(target + "/settings", headers=own).status_code == 403
        db.close()
    print("feedback_http=passed scope=passed strict_settings=passed no_remote_calls=passed")


if __name__ == "__main__":
    main()
