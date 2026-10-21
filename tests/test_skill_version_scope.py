"""Skill versions through real loopback HTTP and isolated SQLite/files only.

These boundary tests do not claim model selection or image quality validation.
"""
import json
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from uuid import uuid4

import httpx
from PIL import Image
from starlette.requests import Request

from creatoros.storage import ContentRepository, Series
from creatoros.tools.model_projection import project_model_data
from creatoros.web.agent_scope import AgentScopeGuard
from tests.agent_studio_support import serve
from tests.studio_review_fixtures import make_fixture


class SkillVersionScopeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database, self.runs, self.producer, self.app = make_fixture(self.root)
        self.addCleanup(self.database.close)
        self.catalog = self.app.state.skill_installs.catalog
        ContentRepository(self.database).create_creator(creator_id="other", display_name="其他账号")
        self.legacy = self.skill("complete", "legacy_end_to_end")
        self.mind = self.skill("story", "mind")
        self.visual = self.skill("drawing", "production")
        self.foreign = self.skill("foreign", "legacy_end_to_end")
        with self.database.session() as db:
            db.get(Series, "agent-notes").skill_name = self.legacy["id"]
            db.add(Series(id="other-series", creator_id="other", name="其他栏目", description="", audience="",
                          skill_name=self.foreign["id"]))
            db.add(Series(id="paired-series", creator_id="review-lab", name="两个原件", description="", audience="",
                          skill_name=None, mind_skill_id=self.mind["id"], production_skill_id=self.visual["id"]))
        self.account = self.app.state.chat.create("review-lab")["id"]
        self.other = self.app.state.chat.create("other")["id"]
        self.overview = self.app.state.chat.create()["id"]
        self.server = serve(self.app)
        self.url = self.server.__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        self.client = httpx.Client(base_url=self.url, timeout=15, trust_env=False)
        self.addCleanup(self.client.close)

    def skill(self, name, role):
        source = self.root / name
        (source / "assets").mkdir(parents=True)
        (source / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: test {name}\n"
            "creatoros-output: social-content-pack.image-carousel\n---\n"
            "[参考](assets/reference.png)\n", encoding="utf-8")
        Image.new("RGB", (8, 8), "blue").save(source / "assets/reference.png")
        return self.catalog.register_local(source, role=role)

    def header(self, session=None):
        return {"x-creatoros-agent-session": session or self.account}

    def candidate(self, original):
        source = self.catalog.read_skill_file(original["id"], "SKILL.md")
        edited = self.client.put(f"/api/producer-skills/{original['id']}/files/content",
            headers=self.header(), json={"path": "SKILL.md", "expected_digest": source["digest"],
                                        "content": source["content"] + "\n不额外添加标题。\n"})
        self.assertEqual(edited.status_code, 200, edited.text)
        candidate = edited.json()
        self.assertNotEqual(candidate["skill_id"], original["id"])
        self.assertEqual(candidate["version_status"], "candidate")
        return candidate

    def test_scoped_agent_edits_candidate_without_touching_bound_version(self):
        original = self.catalog.read_skill_file(self.legacy["id"], "SKILL.md")
        candidate = self.candidate(self.legacy)
        identity = candidate["skill_id"]
        endpoint = f"/api/producer-skills/{identity}"
        read = self.client.get(endpoint + "/files/text", params={"path": "SKILL.md"}, headers=self.header())
        self.assertEqual(read.status_code, 200, read.text)
        changed = read.json()["content"] + "\n文字加浅色底衬。\n"
        saved = self.client.put(endpoint + "/files/content", headers=self.header(),
            json={"path": "SKILL.md", "content": changed, "expected_digest": candidate["digest"]})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["skill_id"], identity)
        self.assertEqual(self.catalog.read_skill_file(identity, "SKILL.md")["content"], changed)
        self.assertEqual(self.catalog.read_skill_file(self.legacy["id"], "SKILL.md")["content"], original["content"])
        self.assertEqual(self.catalog.describe(self.legacy["id"])["digest"], original["digest"])
        with self.database.session() as db:
            self.assertEqual(db.get(Series, "agent-notes").skill_name, self.legacy["id"])
        image = self.client.post(f"/api/agent/sessions/{self.account}/image-actions/read", headers=self.header(),
                                json={"skill_id": identity, "path": "assets/reference.png"})
        self.assertEqual(image.status_code, 200, image.text)
        self.assertEqual(self.producer.calls, 0)

    def test_other_account_cannot_read_or_write_unrelated_candidate(self):
        candidate = self.candidate(self.legacy)
        endpoint = f"/api/producer-skills/{candidate['skill_id']}"
        headers = self.header(self.other)
        for suffix in ("/files", "/content", "/files/text?path=SKILL.md"):
            denied = self.client.get(endpoint + suffix, headers=headers)
            self.assertEqual(denied.status_code, 403, denied.text)
        denied = self.client.put(endpoint + "/files/content", headers=headers,
            json={"path": "SKILL.md", "content": "bad", "expected_digest": candidate["digest"]})
        self.assertEqual(denied.status_code, 403)
        denied = self.client.request("DELETE", endpoint + "/assets", headers=headers,
            json={"path": "assets/reference.png", "expected_digest": candidate["digest"]})
        self.assertEqual(denied.status_code, 403)
        denied = self.client.post(f"/api/agent/sessions/{self.other}/image-actions/read", headers=headers,
            json={"skill_id": candidate["skill_id"], "path": "assets/reference.png"})
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(self.catalog.describe(candidate["skill_id"])["digest"], candidate["digest"])

    def test_unpublished_candidate_cannot_implicitly_replace_column_binding(self):
        single = self.candidate(self.legacy)
        pair = self.candidate(self.mind)
        for body in (
            {"name": "不能直接用单份候选", "skill_name": single["skill_id"]},
            {"name": "不能直接用组合候选", "mind_skill_id": pair["skill_id"], "production_skill_id": self.visual["id"]},
        ):
            response = self.client.post("/api/series", headers=self.header(),
                json={**body, "creator_id": "review-lab", "request_id": uuid4().hex})
            self.assertIn(response.status_code, (409, 422), response.text)
        changed = self.client.post("/api/series/paired-series/composition", headers=self.header(), json={
            "mind_skill_id": pair["skill_id"], "production_skill_id": self.visual["id"],
            "expected_revision": 1, "request_id": uuid4().hex})
        self.assertEqual(changed.status_code, 409, changed.text)
        self.assertEqual(changed.json()["error"]["code"], "skill_candidate_unpublished")
        with self.database.session() as db:
            series = db.get(Series, "paired-series")
            self.assertEqual(series.mind_skill_id, self.mind["id"])
            self.assertEqual(series.revision, 1)
            self.assertEqual(db.query(Series).count(), 3)

    def test_agent_can_discover_and_trial_but_cannot_publish_or_switch(self):
        for session in (self.account, self.overview):
            for path in (
                f"/api/producer-skills/{self.legacy['id']}/publish-version",
                "/api/series/agent-notes/skill-version",
            ):
                response = self.client.post(path, headers=self.header(session), json={})
                self.assertEqual(response.status_code, 403, response.text)
            response = self.client.get(f"/api/producer-skills/{self.legacy['id']}/versions", headers=self.header(session))
            self.assertEqual(response.status_code, 200, response.text)
            # Authorization reaches normal payload validation, not a global trial ban.
            response = self.client.post(f"/api/producer-skills/{self.legacy['id']}/version-trials",
                headers=self.header(session), json={})
            self.assertEqual(response.status_code, 422, response.text)

    def test_version_catalog_includes_owned_candidate_and_old_versions_only(self):
        candidate = self.candidate(self.legacy)
        rows = self.client.get("/api/producer-skills", params={"include_versions": "true"}, headers=self.header())
        self.assertEqual(rows.status_code, 200, rows.text)
        ids = {item["id"] for item in rows.json()["items"]}
        self.assertIn(candidate["skill_id"], ids)
        self.assertIn(self.legacy["id"], ids)
        self.assertNotIn(self.foreign["id"], ids)
        denied = self.client.get("/api/producer-skills", headers=self.header(), params={
            "include_versions": "true", "family_skill_id": self.foreign["id"]})
        self.assertEqual(denied.status_code, 403, denied.text)
        denied = self.client.get(f"/api/producer-skills/{candidate['skill_id']}/versions", headers=self.header(self.other))
        self.assertEqual(denied.status_code, 403, denied.text)
        # A history response must not leak other-account columns sharing a public family.
        with self.database.session() as db:
            db.get(Series, "other-series").skill_name = self.legacy["id"]
        history = self.client.get(f"/api/producer-skills/{candidate['skill_id']}/versions", headers=self.header())
        self.assertEqual({item["series_id"] for item in history.json()["bindings"]}, {"agent-notes"})
        self.assertEqual(self.producer.calls, 0)

    def test_agent_can_read_older_released_version_of_bound_family(self):
        candidate = self.candidate(self.legacy)
        # Simulate the already-reviewed activation outcome, never invoke a paid trial here.
        self.catalog.publish_skill_candidate(candidate["skill_id"], candidate["digest"])
        with self.database.session() as db:
            db.get(Series, "agent-notes").skill_name = candidate["skill_id"]
        response = self.client.get(f"/api/producer-skills/{self.legacy['id']}/files", headers=self.header())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.producer.calls, 0)

    def test_foreign_override_rejected_before_any_run_write(self):
        before = self.client.get("/api/overview").json()
        response = self.client.post("/api/runs", headers=self.header(), json={
            "topic_id": "review-1", "skill_overrides": {"skill_id": self.foreign["id"],
                "expected_digests": {self.foreign["id"]: self.foreign["digest"]}}})
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self.client.get("/api/overview").json(), before)
        self.assertEqual(self.producer.calls, 0)

    def test_iteration_read_action_and_files_recheck_owner_and_current_series(self):
        iteration_id = "a" * 64
        record = {"creator_id": "review-lab", "series_id": "agent-notes"}
        guard = AgentScopeGuard(self.database, self.app.state.chat, self.app.state.topic_research,
                                iterations=SimpleNamespace(get=lambda identity: record))
        def request(suffix="", session=None, method="GET"):
            return Request({"type": "http", "method": method, "path": "/api/skill-iterations/" + iteration_id + suffix,
                "query_string": b"", "headers": [(b"x-creatoros-agent-session", (session or self.account).encode())]})
        for suffix, method in (("", "GET"), ("/files/ref-1", "GET"), ("/actions", "POST")):
            self.assertIsNone(asyncio.run(guard.check(request(suffix, method=method))))
            denied = asyncio.run(guard.check(request(suffix, session=self.other, method=method)))
            self.assertEqual(denied.status_code, 403)
        with self.database.session() as db:
            db.get(Series, "agent-notes").creator_id = "other"
        self.assertEqual(asyncio.run(guard.check(request())).status_code, 403)
        self.assertEqual(asyncio.run(guard.check(request(session=self.other))).status_code, 403)

    def test_truthy_version_query_alias_cannot_bypass_family_filter(self):
        for flag in ("TRUE", "yes", "on", "1"):
            response = self.client.get("/api/producer-skills", headers=self.header(),
                                       params={"include_versions": flag})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn(self.foreign["id"], {item["id"] for item in response.json()["items"]})

    def test_builtin_bound_column_has_version_discovery_without_registry_record(self):
        with self.database.session() as db:
            db.add(Series(id="builtin-series", creator_id="review-lab", name="内置栏目", description="", audience="",
                          skill_name=self.catalog.BUILTIN_ID))
        response = self.client.get("/api/producer-skills", headers=self.header(), params={"include_versions": "true"})
        self.assertEqual(response.status_code, 200, response.text)
        item = next(row for row in response.json()["items"] if row["id"] == self.catalog.BUILTIN_ID)
        self.assertEqual(item["version_status"], "released")
        response = self.client.get(f"/api/producer-skills/{self.catalog.BUILTIN_ID}/versions", headers=self.header())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["items"][0]["digest"], item["digest"])

    def test_model_projection_keeps_candidate_identity_and_status(self):
        data = {"skill_id": self.legacy["id"], "family_id": "family", "parent_id": "parent",
                "version_number": 2, "version_status": "candidate", "digest": "a" * 64,
                "path": "SKILL.md", "content": "full body", "local_path": "D:/private"}
        projected = project_model_data("update_producer_skill_file", data)
        self.assertEqual(projected, {key: value for key, value in data.items() if key != "local_path"})


if __name__ == "__main__":
    unittest.main()
