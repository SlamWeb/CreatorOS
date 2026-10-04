"""Account binding persists independently of model output; all data is temporary."""
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import httpx

from creatoros.storage import ContentRepository, Creator
from creatoros.web.app import create_app
from creatoros.web.chat import AgentChatService
from tests.agent_studio_support import serve
from tests.studio_review_fixtures import make_fixture


def main(live=False):
    with TemporaryDirectory() as directory:
        root = Path(directory)
        db, runs, producer, _ = make_fixture(root)
        repo = ContentRepository(db)
        repo.create_creator(creator_id="other-account", display_name="另一账号")
        chats = root / "chats"
        app = create_app(database=db, run_service=runs, chat_root=chats)
        try:
            with serve(app) as url, httpx.Client(base_url=url, trust_env=False, timeout=20) as client:
                base = "/api/agent/sessions"
                overview = client.post(base, json={}).json()
                account = client.post(base, json={"creator_id": "review-lab"}).json()
                other = client.post(base, json={"creator_id": "other-account"}).json()
                assert overview["scope_kind"] == "overview" and overview["creator_id"] is None
                assert account["scope_kind"] == "creator" and account["creator_id"] == "review-lab"
                assert client.post(base, json={"creator_id": "missing"}).status_code == 404
                assert client.post(base, json={"creator_id": ""}).status_code == 422
                assert client.post(base, json={"creator_id": "review-lab", "scope_kind": "overview"}).status_code == 422
                assert client.patch(f"{base}/{account['id']}", json={"creator_id": "other-account"}).status_code == 405
                assert client.post(f"{base}/{account['id']}/turns", json={
                    "request_id": str(uuid4()), "text": "切换账号", "expected_version": 0,
                    "creator_id": "other-account"}).status_code == 422
                items = client.get(base, params={"scope_kind": "creator", "creator_id": "review-lab"}).json()["items"]
                assert [item["id"] for item in items] == [account["id"]]
                assert client.get(base, params={"scope_kind": "creator"}).status_code == 422
                assert client.get(base, params={"scope_kind": "overview", "creator_id": "review-lab"}).status_code == 422
                # No SQL migration or rewrite required for legacy session metadata.
                legacy_path = chats / overview["id"] / "view.json"
                old = json.loads(legacy_path.read_text(encoding="utf-8"))
                old.pop("scope_kind"); old.pop("creator_id")
                legacy_path.write_text(json.dumps(old), encoding="utf-8")
                assert client.get(f"{base}/{overview['id']}").json()["scope_kind"] == "overview"
                filtered = client.get(base, params={"scope_kind": "overview"}).json()["items"]
                assert [item["id"] for item in filtered] == [overview["id"]]
                for _ in range(31):
                    app.state.chat.create()
                # Account filtering happens before the global recent-30 limit.
                filtered = client.get(base, params={"scope_kind": "creator", "creator_id": "review-lab"}).json()["items"]
                assert [item["id"] for item in filtered] == [account["id"]]
                if live:
                    prompts = [
                        "只查询当前账号名称、栏目与选题，不要写入、调研或生产。",
                        "继续查询刚才的账号有哪些栏目，确认我的对话绑定没有变；不要执行任何写操作。",
                    ]
                    current = account
                    for prompt in prompts:
                        response = client.post(f"{base}/{account['id']}/turns", json={
                            "request_id": str(uuid4()), "text": prompt, "expected_version": current["version"]})
                        assert response.status_code == 202, response.text
                        current = wait_live(client, account["id"])
                        assert current["status"] == "idle", current.get("error")
                        assert current["creator_id"] == "review-lab"
                    messages = json.loads((chats / account["id"] / "messages.json").read_text(encoding="utf-8"))
                    calls = [c["name"] for m in messages for c in m.get("tool_calls", [])]
                    assert calls and set(calls) <= {"list_creators", "list_creator_series", "list_series_topics"}, calls
                    assert "另一账号" not in json.dumps(messages, ensure_ascii=False)
                    assert client.get(f"{base}/{other['id']}").json()["entries"] == []
                    print(json.dumps({"real_deepseek": True, "tools": calls,
                        "usage": [e for e in current["entries"] if e["kind"] == "usage"]}, ensure_ascii=False))
                with db.session() as session:
                    session.get(Creator, "review-lab").is_active = False
                    session.delete(session.get(Creator, "other-account"))
                assert client.post(base, json={"creator_id": "review-lab"}).status_code == 409
                inactive = client.post(f"{base}/{account['id']}/turns", json={
                    "request_id": str(uuid4()), "text": "你好", "expected_version": client.get(f"{base}/{account['id']}").json()["version"]})
                assert inactive.status_code == 409 and "已停用" in inactive.json()["error"]["message"]
                assert client.post(f"{base}/{other['id']}/turns", json={
                    "request_id": str(uuid4()), "text": "你好", "expected_version": 0}).status_code == 404
                assert client.get(f"{base}/{other['id']}").json()["creator_id"] == "other-account"
                assert producer.calls == 0
            restored = AgentChatService(chats, creator_lookup=repo.get_creator)
            restored.start()
            assert restored.get(account["id"])["creator_id"] == "review-lab"
            assert restored.get(overview["id"])["scope_kind"] == "overview"
        finally:
            db.close()
    print("account sessions smoke passed (isolated JSON + SQLite, no production)")


def wait_live(client, session_id):
    # Provider calls can take longer than deterministic smoke; bounded by API timeout.
    from time import monotonic, sleep
    deadline = monotonic() + 180
    while monotonic() < deadline:
        doc = client.get(f"/api/agent/sessions/{session_id}").json()
        if doc["status"] != "running":
            return doc
        sleep(.25)
    raise AssertionError("real chat timeout")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    main(parser.parse_args().live)
