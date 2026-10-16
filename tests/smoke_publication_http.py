"""Real isolated HTTP/storage; remote publish injected to avoid posting a test note."""
from uuid import uuid4

from fastapi.testclient import TestClient

from creatoros.config import PROJECT_ROOT
from creatoros.storage import ContentRepository, CreatorPlatform
from creatoros.tools.publication import _summary
from creatoros.web.chat_links import tool_links
from tests.studio_review_fixtures import make_fixture


class Platform:
    calls = 0

    async def identity(self):
        return {"id": "test-owner", "name": "隔离测试账号"}

    async def publish(self, *args):
        self.calls += 1
        raise TimeoutError("Simulated lost remote acknowledgement")

    async def find_candidates(self, *args):
        return []


def main():
    root = PROJECT_ROOT / "tmp" / ("publication-http-" + uuid4().hex)
    root.mkdir(parents=True)
    db, runs, _, app = make_fixture(root)
    remote = Platform()
    app.state.publishing.client = remote
    repo = ContentRepository(db)
    repo.create_creator(creator_id="other-account", display_name="隔离另一账号", platform=CreatorPlatform.XIAOHONGSHU)
    run = runs.create("review-1")
    runs.execute(run.id)
    base = f"/api/runs/{run.id}"
    with TestClient(app, base_url="http://127.0.0.1:8765") as web:
        owner = web.post("/api/agent/sessions", json={"creator_id": "review-lab"}).json()["id"]
        other = web.post("/api/agent/sessions", json={"creator_id": "other-account"}).json()["id"]
        overview = web.post("/api/agent/sessions", json={}).json()["id"]
        owner_header = {"x-creatoros-agent-session": owner}
        other_header = {"x-creatoros-agent-session": other}
        assert web.post(base + "/publication-request", json={}, headers=other_header).status_code == 403
        created = web.post(base + "/publication-request", json={"title": "隔离发布"}, headers=owner_header)
        assert created.status_code == 200, created.text
        draft = created.json()
        target = f"/api/publication-requests/{draft['id']}"
        assert draft["status"] == "draft" and not draft["can_confirm"]
        assert web.get(target, headers=other_header).status_code == 403
        assert web.post(target + "/reconcile", json={}, headers=other_header).status_code == 403
        assert web.get(target, headers=owner_header).status_code == 200
        consent = {"expected_digest": draft["digest"]}
        assert web.post(target + "/confirm", json=consent).status_code == 403
        assert web.post(target + "/confirm", json=consent, headers={"origin": "http://evil.example"}).status_code == 403
        ui = {"origin": "http://127.0.0.1:8765"}
        assert web.post(target + "/confirm", json=consent, headers=ui).status_code == 409
        detail = web.get(base).json()
        revision = detail["revisions"][0]
        assert web.post(base + "/approve", json={"expected_version": detail["version"],
            "revision_id": revision["id"], "artifact_digest": revision["review_digest"]}).status_code == 200
        for session_id in (owner, overview):
            assert web.post(target + "/confirm", json=consent,
                headers={**ui, "x-creatoros-agent-session": session_id}).status_code == 403
        assert remote.calls == 0
        result = web.post(target + "/confirm", json=consent, headers=ui)
        assert result.status_code == 200, result.text
        assert web.post(target + "/confirm", json=consent, headers=ui).status_code == 200
        # TestClient's event loop runs the injected failing task; querying is safe.
        for _ in range(20):
            state = web.get(target).json()
            if state["status"] == "unknown":
                break
        assert state["status"] == "unknown", state
        assert remote.calls == 1
        assert web.post(target + "/reconcile", json={}, headers=ui).json()["status"] == "unknown"
        assert remote.calls == 1
        import json
        links = tool_links("prepare_publication", json.dumps(_summary(state)), studio_url="http://127.0.0.1:8765")
        assert links and links[0]["url"] == f"/runs/{run.id}"
        assert web.get(target).headers["cache-control"] == "no-store"
    db.close()
    print("publication_http=passed scope=passed ui_consent=passed no_duplicate_send=passed")


if __name__ == "__main__":
    main()
