"""Local image/DB/HTTP regression; no model or production-data access."""
import base64
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import func, select

from creatoros.runs import ContentRunError
from creatoros.storage import ContentRepository, CreatorPlatform, ContentRun, Topic, Series
from creatoros.web.agent_scope import AgentScopeGuard
from creatoros.web.artifacts import StudioArtifacts
from creatoros.web.chat_image_actions import chat_image_action_routes, extraction_owner_path, _managed_path, import_pack, ImportRequest
from tests.studio_review_fixtures import make_fixture
from tests.agent_studio_support import serve
from creatoros.context import RuntimeContext
from creatoros.tools.chat_images import read_image


with TemporaryDirectory() as temporary:
    root = Path(temporary)
    db, runs, producer, app = make_fixture(root)
    chat = app.state.chat
    catalog = app.state.skill_installs.catalog
    scope = AgentScopeGuard(db, chat, app.state.topic_research)
    if not any(getattr(r, "path", "").endswith("/image-actions/import") for r in app.routes):
        app.include_router(chat_image_action_routes(chat, catalog, app.state.skill_extractions,
                           runs, StudioArtifacts(db, runs.output_root), scope))
    ContentRepository(db).create_creator(creator_id="other", display_name="Other", platform=CreatorPlatform.XIAOHONGSHU)
    sid = chat.create("review-lab")["id"]
    other_sid = chat.create("other")["id"]
    output = io.BytesIO()
    Image.new("RGB", (20, 30), "blue").save(output, format="PNG")
    raw = output.getvalue()
    attachment = chat.attachments.put(sid, "example.png", base64.b64encode(raw).decode())
    source = root / "reference-skill"
    (source / "assets").mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: reference-skill\ndescription: Image fixture\n---\nUse the reference.", encoding="utf-8")
    (source / "assets" / "reference.png").write_bytes(raw)
    skill_id = catalog.register_local(source, role="mind")["id"]
    with db.session() as transaction:
        transaction.get(Series, "agent-notes").skill_name = skill_id
    with serve(app) as studio_url, httpx.Client(base_url=studio_url, timeout=10, trust_env=False) as client:
        headers = {"x-creatoros-agent-session": sid}
        endpoint = f"/api/agent/sessions/{sid}/image-actions"
        seen = client.post(endpoint + "/read", headers=headers, json={"attachment_id": attachment["id"]})
        assert seen.status_code == 200, seen.text
        assert "base64" not in seen.text
        context = RuntimeContext(project_root=root, studio_url=studio_url, agent_session_id=sid, user_request_id="turn-1")
        tool_seen = read_image(attachment_id=attachment["id"], context=context)
        assert not tool_seen.is_error and tool_seen.details["image_attachments"][0]["id"] == attachment["id"]
        denied = client.post(endpoint + "/read", headers={"x-creatoros-agent-session": other_sid}, json={"attachment_id": attachment["id"]})
        assert denied.status_code == 403, denied.text
        denied = client.post(f"/api/agent/sessions/{other_sid}/image-actions/read",
            headers={"x-creatoros-agent-session": other_sid}, json={"attachment_id": attachment["id"]})
        assert denied.status_code == 404, denied.text
        invalid = client.post(endpoint + "/read", headers=headers, json={"attachment_id": attachment["id"], "skill_id": "knowledge-to-carousel", "path": "SKILL.md"})
        assert invalid.status_code == 422
        skill_image = client.post(endpoint + "/read", headers=headers, json={"skill_id": skill_id, "path": "assets/reference.png"})
        assert skill_image.status_code == 200, skill_image.text
        other_skill = client.post(f"/api/agent/sessions/{other_sid}/image-actions/read",
            headers={"x-creatoros-agent-session": other_sid}, json={"skill_id": skill_id, "path": "assets/reference.png"})
        assert other_skill.status_code == 403
        unsafe = client.post(endpoint + "/read", headers=headers, json={"skill_id": skill_id, "path": "../outside.png"})
        assert unsafe.status_code in {404, 422}, unsafe.text
        text_as_image = client.post(endpoint + "/read", headers=headers, json={"skill_id": skill_id, "path": "SKILL.md"})
        assert text_as_image.status_code == 422
        # Boundary-only fixture: a missing job returns 404 to its owner, never a global listing.
        job_id = "a" * 64
        owner_path = extraction_owner_path(chat, job_id)
        owner_path.parent.mkdir(parents=True, exist_ok=True)
        owner_path.write_text(json.dumps({"session_id": sid}), encoding="utf-8")
        assert client.get(f"/api/skill-extractions/{job_id}", headers=headers).status_code == 404
        assert client.get(f"/api/skill-extractions/{job_id}", headers={"x-creatoros-agent-session": other_sid}).status_code == 403
        assert client.get("/api/skill-extractions", headers=headers).status_code == 403
        assert client.post(f"/api/skill-extractions/{job_id}/revise", headers=headers, json={}).status_code == 403
        payload = dict(series_id="agent-notes", attachment_ids=[attachment["id"]], title="导入的成品", body="用户文案", request_id="import-1")
        result = client.post(endpoint + "/import", headers=headers, json=payload)
        assert result.status_code == 200, result.text
        imported = result.json()
        replay = client.post(endpoint + "/import", headers=headers, json=payload)
        assert replay.json()["run_id"] == imported["run_id"] and replay.json()["deduplicated"]
        changed = client.post(endpoint + "/import", headers=headers, json={**payload, "title": "不同的文案"})
        assert changed.status_code == 409, changed.text
        cross = client.post(f"/api/agent/sessions/{other_sid}/image-actions/import",
            headers={"x-creatoros-agent-session": other_sid}, json=payload)
        assert cross.status_code == 403
        detail = client.get(imported["url"].replace("/runs/", "/api/runs/")).json()
        assert detail["status"] == "awaiting_approval", detail
        assert "revise" not in detail["allowed_actions"]
        revision = detail["revisions"][0]
        assert revision["artifact_available"] and len(revision["cards"]) == 1
        card = client.get(revision["cards"][0]["url"])
        assert card.status_code == 200 and card.content == raw
        loaded = client.post(endpoint + "/read", headers=headers, json={"run_id": imported["run_id"], "revision_id": revision["id"], "image_index": 1})
        assert loaded.status_code == 200, loaded.text
        for operation in (lambda: runs.request_revision(imported["run_id"], "change", expected_version=detail["version"]),
                          lambda: runs.claim(imported["run_id"], owner_id="test")):
            try:
                operation()
            except ContentRunError as error:
                assert error.code == "external_import_readonly", str(error)
            else:
                raise AssertionError("External imports must not enter production")
        approved = runs.approve(imported["run_id"], revision_id=revision["id"],
            artifact_digest=revision["review_digest"], expected_version=detail["version"])
        assert approved.status.value == "approved"
        with db.session() as transaction:
            assert transaction.scalar(select(func.count()).select_from(ContentRun)) == 1
            assert transaction.scalar(select(func.count()).select_from(Topic)) == 3
        assert producer.calls == 0
        # Failed file verification leaves no half-created Topic/Run; retained files are not success.
        try:
            import_pack(runs, sid, ImportRequest(**{**payload, "request_id": "bad-bytes"}), [(attachment, b"not an image")])
        except (ValueError, OSError):
            pass
        else:
            raise AssertionError("Invalid imported bytes were accepted")
        with db.session() as transaction:
            assert transaction.scalar(select(func.count()).select_from(ContentRun)) == 1
            assert transaction.scalar(select(func.count()).select_from(Topic)) == 3
        outside = root / "outside"
        outside.mkdir()
        linked = runs.output_root / "linked"
        linked.symlink_to(outside, target_is_directory=True)
        for bad in (runs.output_root / ".." / "escaped", linked / "artifact.png"):
            try:
                _managed_path(runs.output_root, bad)
            except HTTPException as error:
                assert error.status_code == 403
            else:
                raise AssertionError("Unmanaged import target accepted")
        linked.unlink()
    db.close()
print("chat_image_actions_smoke=passed image_read=passed account_scope=passed import_idempotency=passed approval=passed model_calls=0")
