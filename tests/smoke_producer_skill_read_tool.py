from __future__ import annotations

import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from creatoros.context import RuntimeContext
from creatoros.storage import ContentRepository, CreatorPlatform, Database, upgrade_database
from creatoros.tools.definitions import tool_registry
from creatoros.tools.studio import get_producer_skill
from creatoros.web.app import create_app
from tests.agent_studio_support import serve


with TemporaryDirectory() as temporary:
    root = Path(temporary)
    database_url = f"sqlite:///{(root / 'skills.db').as_posix()}"
    upgrade_database(database_url)
    database = Database(database_url)
    ContentRepository(database).create_creator(creator_id="creator-test", display_name="Test",
                                               platform=CreatorPlatform.XIAOHONGSHU)
    app = create_app(database=database, chat_root=root / "sessions")
    catalog = app.state.skill_installs.catalog
    source = root / "source-skill"
    source.mkdir()
    content = "---\nname: account-skill\ndescription: A registered test skill\n---\n" + ("按步骤说明规则。\n" * 700)
    (source / "SKILL.md").write_text(content, encoding="utf-8")
    registered = catalog.register_local(source, role="mind")
    skill_id = registered["id"]
    context = None

    with serve(app) as studio_url, httpx.Client(base_url=studio_url, timeout=5, trust_env=False) as client:
        account_session = app.state.chat.create("creator-test")["id"]
        headers = {"x-creatoros-agent-session": account_session}
        context = RuntimeContext(project_root=root, studio_url=studio_url,
                                 agent_session_id=account_session)

        first = json.loads(get_producer_skill(skill_id, offset=0, limit=1200, context=context).content)
        second = client.get(f"/api/producer-skills/{skill_id}/content?offset=1200&limit=4000",
                            headers=headers)
        assert second.status_code == 200, second.text
        second_page = second.json()
        assert first["name"] == "account-skill" and first["description"] == "A registered test skill"
        assert "producible" not in first and "carousel_compatible" not in first
        assert first["content"] == content[:1200]
        assert first["page"] == {"offset": 0, "limit": 1200, "total_chars": len(content),
                                 "has_more": True, "next_offset": 1200}
        assert first["content"] + second_page["content"] == content[:5200]
        assert second_page["page"]["has_more"] and second_page["page"]["next_offset"] == 5200
        third = client.get(f"/api/producer-skills/{skill_id}/content?offset=5200&limit=4000",
                           headers=headers).json()
        assert first["content"] + second_page["content"] + third["content"] == content
        assert third["page"]["has_more"] is False and third["page"]["next_offset"] is None

        builtin = client.get("/api/producer-skills/knowledge-to-carousel/content?limit=200",
                             headers=headers)
        assert builtin.status_code == 200 and builtin.json()["content"]
        assert builtin.json()["name"] == "knowledge-to-carousel"

        unregistered = client.get("/api/producer-skills/../../outside/content", headers=headers)
        assert unregistered.status_code in {403, 404}
        arbitrary = client.get("/api/producer-skills/not-a-registered-skill/content", headers=headers)
        assert arbitrary.status_code in {403, 404}
        oversized = client.get(f"/api/producer-skills/{skill_id}/content?limit=4001", headers=headers)
        assert oversized.status_code == 422
        # Missing editable data stays missing: this read endpoint must not invoke describe()/repair.
        working = catalog.root / "working" / skill_id
        shutil.rmtree(working)
        missing = client.get(f"/api/producer-skills/{skill_id}/content", headers=headers)
        assert missing.status_code == 404 and not working.exists()
        working.mkdir()
        invalid_body = working / "SKILL.md"
        invalid_body.write_bytes(b"\xff\xfe invalid utf-8")
        invalid_bytes = invalid_body.read_bytes()
        invalid = client.get(f"/api/producer-skills/{skill_id}/content", headers=headers)
        assert invalid.status_code == 404 and invalid_body.read_bytes() == invalid_bytes

    tool = tool_registry["get_producer_skill"]
    assert tool.to_schema()["function"]["parameters"]["properties"]["limit"]["maximum"] == 4000
    database.close()

print("producer_skill_read_tool_smoke=passed registered=builtin paging=bounded path=restricted no_repair=true")
