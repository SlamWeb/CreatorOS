from __future__ import annotations

import json
import hashlib
import shutil
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from fastapi.responses import Response
from PIL import Image

from creatoros.context import RuntimeContext
from creatoros.integrations.studio import StudioClient, StudioClientError
from creatoros.storage import ContentRepository, CreatorPlatform, Database, Series, upgrade_database
from creatoros.tools.definitions import tool_registry
from creatoros.tools.studio import get_producer_skill
from creatoros.web.app import create_app
from tests.agent_studio_support import serve


with TemporaryDirectory() as temporary, ExitStack() as cleanup:
    root = Path(temporary)
    database_path = root / "skills.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    upgrade_database(database_url)
    database = Database(database_url)
    cleanup.callback(database.close)
    ContentRepository(database).create_creator(creator_id="creator-test", display_name="Test",
                                               platform=CreatorPlatform.XIAOHONGSHU)
    ContentRepository(database).create_creator(creator_id="creator-other", display_name="Other",
                                               platform=CreatorPlatform.XIAOHONGSHU)
    app = create_app(database=database, chat_root=root / "sessions")
    catalog = app.state.skill_installs.catalog
    source = root / "source-skill"
    source.mkdir()
    content = "---\nname: account-skill\ndescription: A registered test skill\n---\n" + ("按步骤说明规则。\n" * 700)
    (source / "SKILL.md").write_text(content, encoding="utf-8")
    (source / "notes.md").write_text("只读取文本。\n", encoding="utf-8")
    (source / "assets").mkdir()
    Image.new("RGB", (3, 2), (35, 125, 220)).save(source / "assets" / "reference-01.jpg")
    (source / "assets" / "bad-image.png").write_bytes(b"not an image")
    registered = catalog.register_local(source, role="mind")
    skill_id = registered["id"]
    working = catalog.root / "working" / skill_id
    with database.session() as session:
        session.add(Series(id="series-test", creator_id="creator-test", name="Test",
                           description="", audience="", skill_name=skill_id))

    @app.put("/api/test-invalid-json")
    def invalid_json_write():
        return Response(content="injected non-JSON response", media_type="text/plain")

    context = None

    with serve(app) as studio_url, httpx.Client(base_url=studio_url, timeout=5, trust_env=False) as client:
        account_session = app.state.chat.create("creator-test")["id"]
        other_session = app.state.chat.create("creator-other")["id"]
        headers = {"x-creatoros-agent-session": account_session}
        context = RuntimeContext(project_root=root, studio_url=studio_url,
                                 agent_session_id=account_session)
        before_db = hashlib.sha256(database_path.read_bytes()).hexdigest()
        before_files = {path.relative_to(working).as_posix(): (path.stat().st_mtime_ns, path.read_bytes())
                        for path in working.rglob("*") if path.is_file()}

        file_result = get_producer_skill(skill_id, path="notes.md", context=context)
        assert not file_result.is_error, file_result.content
        text_body = json.loads(file_result.content)
        assert text_body["content"] == (working / "notes.md").read_bytes().decode("utf-8")
        assert text_body["kind"] == "markdown"
        assert len(text_body["digest"]) == 64 and text_body["editable"]
        text_route = f"/api/producer-skills/{skill_id}/files/text"
        for image_path in ("assets/reference-01.jpg", "assets/bad-image.png"):
            image_response = client.get(text_route, params={"path": image_path}, headers=headers)
            assert image_response.status_code == 415, image_response.text
            assert image_response.headers["content-type"].startswith("application/json")
            assert image_response.headers["cache-control"] == "no-store"
            assert image_response.json()["error"]["code"] == "skill_text_only"
            image_result = get_producer_skill(skill_id, path=image_path, context=context)
            assert image_result.is_error and image_result.error_type == "skill_text_only"
            assert "只能读取" in image_result.content and "不代表已实际看图" in image_result.content
            assert "可能已生效" not in image_result.content
        cross_account = client.get(text_route, params={"path": "notes.md"},
                                   headers={"x-creatoros-agent-session": other_session})
        assert cross_account.status_code == 403, cross_account.text
        denied_write = client.put(text_route, headers=headers, json={})
        assert denied_write.status_code == 403, denied_write.text
        invalid_path = client.get(text_route, params={"path": "../outside.md"}, headers=headers)
        assert invalid_path.status_code == 404, invalid_path.text

        # The UI keeps its binary preview route. A mistaken GET there must only
        # report a failed read; it cannot imply any write might have succeeded.
        studio = StudioClient(studio_url)
        for method, path, params in (
            ("get", f"/api/producer-skills/{skill_id}/files/content", {"path": "assets/reference-01.jpg"}),
            ("PUT", "/api/test-invalid-json", None),
        ):
            try:
                studio.request(method, path, params=params, payload={} if method == "PUT" else None)
            except StudioClientError as error:
                assert error.code == "studio_invalid_response"
                assert ("可能已生效" in str(error)) == (method == "PUT"), str(error)
            else:
                raise AssertionError("non-JSON response should fail")
        studio.close()

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
        assert hashlib.sha256(database_path.read_bytes()).hexdigest() == before_db
        assert {path.relative_to(working).as_posix(): (path.stat().st_mtime_ns, path.read_bytes())
                for path in working.rglob("*") if path.is_file()} == before_files
        # Missing editable data stays missing: this read endpoint must not invoke describe()/repair.
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
    assert "skill_text_only" in tool.description
    database.close()


# Transport failures are injected because a response-loss race cannot be made
# deterministic through an ordinary successful HTTP request.
def failed_transport(request):
    raise httpx.ReadTimeout("injected response loss", request=request)


with httpx.Client(transport=httpx.MockTransport(failed_transport)) as transport:
    studio = StudioClient("http://127.0.0.1:8765", client=transport)
    for method, code in (("GET", "studio_read_failed"), ("POST", "studio_outcome_unknown"),
                         ("PUT", "studio_outcome_unknown")):
        try:
            studio.request(method, "/api/test-response-loss")
        except StudioClientError as error:
            assert error.code == code, (method, error.code)
        else:
            raise AssertionError("response loss should fail")

print("producer_skill_read_tool_smoke=passed registered=builtin paging=bounded path=restricted "
      "image=skill_text_only scope=403 get_invalid_json=read_only no_repair=true")
