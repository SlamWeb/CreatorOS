"""Agent schema, compact projections and real isolated loopback resource boundaries.

Local audio is decoded by the real media tools; no model or formal service is used.
The loopback import URL intentionally verifies the real unsafe-source rejection.
"""
from copy import deepcopy
import io
import json
from pathlib import Path
import unittest
from uuid import uuid4
import wave
from time import monotonic, sleep

from pydantic import ValidationError

from creatoros.ai.types import ToolCall
from creatoros.context import RuntimeContext
from creatoros.storage import ProductionTask, Series
from creatoros.tools import execute_tool_call, tools
from creatoros.tools.host_contract import model_tool_schemas
from creatoros.tools.model_projection import project_model_data
from creatoros.tools.studio import StartRunArgs, production_tasks_receipt
from creatoros.web.account_context import CreatorContextBuilder
from creatoros.web.chat import ACCOUNT_TOOLS
from tests import test_skill_version_scope as scope_fixture


class StagedAgentSchemaTests(unittest.TestCase):
    def test_video_accepts_bgm_share_text_without_preimport(self):
        share = "2.89 复制打开抖音，看看【作品】 https://v.douyin.com/l5keSl2qzb8/ 07/01 :4pm C@H.ic"
        task = {"kind": "video", "title": "剪辑", "brief": "按节拍剪辑", "skill_ids": ["video-test--" + "a" * 16],
                "inputs": {"collection_id": "images", "bgm_reference": share}}
        parsed = StartRunArgs(topic_id="topic", tasks=[task])
        self.assertEqual(parsed.tasks[0].inputs.bgm_reference, share)
        self.assertIsNone(parsed.tasks[0].inputs.bgm_material_id)
        data = parsed.model_dump(mode="json", exclude_none=True)
        self.assertEqual(data["tasks"][0]["inputs"]["bgm_reference"], share)
        for inputs in ({"collection_id": "images"},
                       {"collection_id": "images", "bgm_reference": "  "},
                       {"collection_id": "images", "bgm_reference": share, "bgm_material_id": "c" * 64}):
            with self.subTest(inputs=inputs), self.assertRaises(ValidationError):
                StartRunArgs(topic_id="topic", tasks=[{**task, "inputs": inputs}])

    def test_task_selection_is_typed_and_contains_no_host_paths(self):
        skill = {"skill_id": "video-test--" + "a" * 16, "digest": "b" * 64, "role": "primary"}
        task = {"kind": "video", "title": "短片", "brief": "保留音乐卡点", "skills": [skill],
                "inputs": {"collection_id": "images", "bgm_material_id": "c" * 64}, "depends_on": ["task-one"]}
        value = StartRunArgs(topic_id="topic", tasks=[task])
        self.assertEqual(value.tasks[0].kind, "video")
        omitted = deepcopy(task)
        omitted["skills"][0].pop("digest")
        self.assertIsNone(StartRunArgs(topic_id="topic", tasks=[omitted]).tasks[0].skills[0].digest)
        for change in ({"skills": [{**skill, "path": "D:/private"}]}, {"skills": [{**skill, "digest": "unknown"}]},
                       {"skills": [{**skill, "role": "mind"}]}, {"skills": [skill, skill]},
                       {"kind": "image"}, {"inputs": {"bgm_material_id": "c" * 64}}, {"depends_on": ["same", "same"]}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                StartRunArgs(topic_id="topic", tasks=[{**task, **change}])
        context = RuntimeContext(Path.cwd(), creator_id="a", allowed_tools=ACCOUNT_TOOLS)
        schemas = {item["function"]["name"]: item["function"] for item in model_tool_schemas(tools, context)}
        for name in ("manage_skill_library", "manage_material"):
            self.assertIn(name, schemas)
            self.assertNotIn("creator_id", schemas[name]["parameters"]["properties"])

    def test_waiting_task_and_resource_projections_preserve_handoff_ids(self):
        data = {"topic_id": "topic", "collection_id": "images", "items": [{"id": "task", "title": "等待剪辑",
            "kind": "video", "run_id": None, "dispatch_state": "waiting", "status": "waiting",
            "depends_on": ["image-task"], "brief": "PRIVATE_BRIEF", "definition_json": {"path": "D:/private"}}]}
        receipt = production_tasks_receipt(data)
        self.assertNotIn("url", receipt["items"][0])
        projected = project_model_data("start_content_run", receipt)
        self.assertEqual(projected["items"][0]["depends_on"], ["image-task"])
        self.assertEqual(projected["items"][0]["dispatch_state"], "waiting")
        self.assertNotIn("PRIVATE", json.dumps(projected))
        library = {"version": 3, "defaults": {"image": [], "video": []}, "items": [{
            "id": "skill", "digest": "d" * 64, "outputs": ["video"], "can_use": True, "can_edit": False,
            "local_path": "D:/private", "content": "PRIVATE_BODY"}]}
        original = deepcopy(library)
        view = project_model_data("manage_skill_library", library)
        self.assertEqual(view["items"][0]["digest"], "d" * 64)
        self.assertFalse(view["items"][0]["can_edit"])
        self.assertNotIn("private", json.dumps(view).lower())
        self.assertEqual(library, original)
        exported = project_model_data("manage_media_collection", {"collection_id": "images", "export_id": "e" * 64,
            "checksum": "c" * 64, "url": "/file", "selected_count": 4})
        self.assertEqual(exported["export_id"], "e" * 64)
        self.assertEqual(exported["collection_id"], "images")
        self.assertNotIn("url", exported)


class StagedAgentHttpTests(unittest.TestCase):
    setUp = scope_fixture.SkillVersionScopeTests.setUp
    skill = scope_fixture.SkillVersionScopeTests.skill
    header = scope_fixture.SkillVersionScopeTests.header
    candidate = scope_fixture.SkillVersionScopeTests.candidate

    def context(self, creator_id="review-lab", session=None):
        return RuntimeContext(self.root, studio_url=self.url, creator_id=creator_id,
            agent_session_id=session or self.account, allowed_tools=ACCOUNT_TOOLS, user_request_id=uuid4().hex)

    def call(self, tool_name, context=None, **arguments):
        return execute_tool_call(ToolCall(uuid4().hex, tool_name, json.dumps(arguments)),
                                 context or self.context(), model_requested=True)

    def test_shared_account_reference_forks_private_candidate(self):
        context = self.context()
        added = self.call("manage_skill_library", context, action="add", skill_id=self.foreign["id"])
        self.assertFalse(added.is_error, added.content)
        replay = self.call("manage_skill_library", context, action="add", skill_id=self.foreign["id"])
        self.assertEqual(json.loads(added.content)["version"], json.loads(replay.content)["version"])
        row = next(item for item in json.loads(added.to_model_content())["items"] if item["id"] == self.foreign["id"])
        self.assertTrue(row["can_use"])
        self.assertFalse(row["can_edit"])
        endpoint = f"/api/producer-skills/{self.foreign['id']}"
        self.assertEqual(self.client.get(endpoint + "/files", headers=self.header()).status_code, 200)
        source = self.catalog.read_skill_file(self.foreign["id"], "SKILL.md")
        for method, path, body in (("PUT", endpoint + "/files/content", {"path": "SKILL.md", "content": source["content"] + "edit", "expected_digest": source["digest"]}),
            ("POST", endpoint + "/versions", {"expected_digest": source["digest"], "request_id": uuid4().hex})):
            response = self.client.request(method, path, headers=self.header(), json=body)
            self.assertEqual(response.status_code, 200, response.text)
            candidate_id = response.json().get("skill_id") or response.json()["id"]
            self.assertNotEqual(candidate_id, self.foreign["id"])
            self.assertEqual(self.catalog._record(candidate_id)["creator_id"], "review-lab")
            foreign_read = self.client.get(f"/api/producer-skills/{candidate_id}/files", headers=self.header(self.other))
            self.assertEqual(foreign_read.status_code, 403, foreign_read.text)
        for method, path, body in (("DELETE", endpoint + "/assets", {"path": "assets/reference.png", "expected_digest": source["digest"]}),
            ("POST", f"/api/agent/sessions/{self.account}/image-actions/skill-asset", {
                "operation": "delete_image", "skill_id": self.foreign["id"], "path": "assets/reference.png",
                "expected_digest": source["digest"], "request_id": uuid4().hex})):
            response = self.client.request(method, path, headers=self.header(), json=body)
            self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.catalog.describe(self.foreign["id"])["digest"], source["digest"])
        with self.database.session() as session:
            session.add(Series(id="shared-legacy", creator_id="review-lab", name="共享旧绑定", description="", audience="",
                               skill_name=self.foreign["id"]))
        self.assertFalse(self.app.state.skill_library.can_edit("review-lab", self.foreign["id"]))

    def test_library_pool_and_context_use_account_library_without_changing_legacy(self):
        context = self.context()
        result = self.call("manage_skill_library", context, action="get", series_id="agent-notes")
        self.assertFalse(result.is_error, result.content)
        before = json.loads(result.content)
        candidate = self.candidate(self.legacy)
        changed = self.call("manage_skill_library", context, action="set_pool", series_id="agent-notes",
            expected_version=before["version"], skill_ids=[candidate["skill_id"], self.mind["id"]],
            defaults={"image": [{"skill_id": candidate["skill_id"], "role": "primary"},
                                {"skill_id": self.mind["id"], "role": "supporting"}], "video": []})
        self.assertFalse(changed.is_error, changed.content)
        self.app.state.skill_library.add("review-lab", self.mind["id"], uuid4().hex)
        tree = CreatorContextBuilder(self.database, self.catalog, self.app.state.skill_library).build("review-lab")
        column = next(row for row in tree["series"] if row["id"] == "agent-notes")
        self.assertEqual(column["skill_pool"]["version"], 1)
        self.assertIn(self.mind["id"], [item["id"] for item in tree["skills"]])
        self.assertNotIn("local_path", json.dumps(tree))
        with self.database.session() as session:
            self.assertEqual(session.get(Series, "agent-notes").skill_name, self.legacy["id"])
        denied = self.call("manage_skill_library", action="get", creator_id="other")
        self.assertTrue(denied.is_error)
        self.assertEqual(denied.error_type, "agent_scope_rejected")
        self.assertEqual(self.client.get("/api/series/other-series/skills", headers=self.header()).status_code, 403)

    def test_agent_changes_pool_without_supplying_legacy_defaults(self):
        context = self.context()
        self.app.state.skill_library.add("review-lab", self.mind["id"], uuid4().hex)
        initial = self.app.state.skill_library.set_series("agent-notes", 0,
            [self.legacy["id"], self.mind["id"]],
            {"image": [{"skill_id": self.legacy["id"], "role": "primary"},
                       {"skill_id": self.mind["id"], "role": "supporting"}], "video": []})
        candidate = self.candidate(self.legacy)
        changed = self.call("manage_skill_library", context, action="set_pool", series_id="agent-notes",
            expected_version=initial["version"], skill_ids=[candidate["skill_id"], self.mind["id"]])
        self.assertFalse(changed.is_error, changed.content)
        result = json.loads(changed.content)
        self.assertEqual(result["defaults"], initial["defaults"])
        removed = self.call("manage_skill_library", context, action="set_pool", series_id="agent-notes",
            expected_version=result["version"], skill_ids=[self.mind["id"]])
        self.assertFalse(removed.is_error, removed.content)
        self.assertEqual(json.loads(removed.content)["defaults"], {"image": [], "video": []})
        stale = self.call("manage_skill_library", context, action="set_pool", series_id="agent-notes",
            expected_version=result["version"], skill_ids=[candidate["skill_id"]])
        self.assertTrue(stale.is_error)

    def test_typed_task_scope_rejects_foreign_skill_material_and_host_path_before_writes(self):
        from creatoros.storage.models import ManagedMaterial
        with self.database.session() as session:
            session.add(ManagedMaterial(id="c" * 64, creator_id="other", name="foreign.wav", kind="audio",
                sha256="c" * 64, size_bytes=1, metadata_json={"duration_seconds": 1, "suffix": ".wav"}))
        base = {"title": "禁止部分创建", "brief": "不应创建任务", "skills": [{
            "skill_id": self.legacy["id"], "digest": self.legacy["digest"], "role": "primary"}]}
        attacks = [{**base, "skills": [{"skill_id": self.foreign["id"], "digest": self.foreign["digest"], "role": "primary"}]},
            {**base, "kind": "video", "inputs": {"collection_id": "unknown", "bgm_material_id": "c" * 64}},
            {**base, "skills": [{**base["skills"][0], "path": "D:/private"}]},
            {**base, "depends_on": ["unknown-task"]}]
        for task in attacks:
            response = self.client.post("/api/topics/review-1/production-tasks", headers=self.header(),
                json={"request_id": uuid4().hex, "tasks": [base, task]})
            self.assertEqual(response.status_code, 403, response.text)
        with self.database.session() as session:
            self.assertEqual(session.query(ProductionTask).count(), 0)
        self.assertEqual(self.producer.calls, 0)

    def test_material_queries_are_account_scoped_and_link_failure_does_not_claim_success(self):
        from tests.media_test_support import require_media
        require_media(self)
        output = io.BytesIO()
        with wave.open(output, "wb") as sound:
            sound.setnchannels(1); sound.setsampwidth(2); sound.setframerate(8000)
            sound.writeframes(b"\0\0" * 8000)
        receipt = self.app.state.materials.put("review-lab", "music.wav", output.getvalue())
        listed = self.call("manage_material", action="list")
        self.assertFalse(listed.is_error, listed.content)
        self.assertEqual(json.loads(listed.to_model_content())["items"][0]["id"], receipt["id"])
        got = self.call("manage_material", action="get", material_id=receipt["id"])
        self.assertFalse(got.is_error, got.content)
        self.assertEqual(json.loads(got.content)["duration_seconds"], 1)
        denied = self.client.get(f"/api/creators/other/materials/{receipt['id']}", headers=self.header(self.other))
        self.assertEqual(denied.status_code, 403)
        failed = self.call("manage_material", action="import_link", source_url="http://127.0.0.1/music.mp3")
        self.assertTrue(failed.is_error, failed.content)
        self.assertEqual(len(self.app.state.materials.list("review-lab")), 1)

    def test_new_series_can_start_empty_without_secret_builtin_binding(self):
        result = self.call("compose_series", name="先建栏目再选Skill")
        self.assertFalse(result.is_error, result.content)
        identity = json.loads(result.content)["series_id"]
        with self.database.session() as session:
            series = session.get(Series, identity)
            self.assertIsNone(series.skill_name)
            self.assertIsNone(series.mind_skill_id)
            self.assertIsNone(series.production_skill_id)
        pool = self.client.get(f"/api/series/{identity}/skills", headers=self.header())
        self.assertEqual(pool.status_code, 200, pool.text)
        self.assertEqual(pool.json()["defaults"], {"image": [], "video": []})
        tree = CreatorContextBuilder(self.database, self.catalog).build("review-lab")
        self.assertEqual(next(row for row in tree["series"] if row["id"] == identity)["skill_bindings"], {})

    def test_single_agent_production_uses_stage_default_without_legacy_binding(self):
        from creatoros.storage import Topic
        with self.database.session() as session:
            series_id = session.get(Topic, "review-1").series_id
            column = session.get(Series, series_id)
            column.skill_name = column.mind_skill_id = column.production_skill_id = None
        library = self.app.state.skill_library
        library.add("review-lab", self.legacy["id"], uuid4().hex)
        pool = library.get_series(series_id)
        library.set_series(series_id, pool["version"], [self.legacy["id"]], {
            "image": [{"skill_id": self.legacy["id"], "role": "primary"}], "video": []})
        result = self.call("start_content_run", topic_id="review-1")
        self.assertFalse(result.is_error, result.content)
        from creatoros.storage import ContentRun
        with self.database.session() as session:
            run = session.query(ContentRun).filter_by(topic_id="review-1").one()
            self.assertEqual(run.input_snapshot_json["execution_spec"]["skills"][0]["skill_id"], self.legacy["id"])

    def test_same_turn_images_then_dependent_video_have_distinct_host_keys_and_replay(self):
        """Real HTTP writes; controlled local producer is not a video-quality claim."""
        from tests.media_test_support import require_media
        require_media(self)
        context = self.context()
        image_arguments = {"topic_id": "review-1", "tasks": [{"title": "同轮图片", "brief": "独立图片"}]}
        image_result = self.call("start_content_run", context, **image_arguments)
        self.assertFalse(image_result.is_error, image_result.content)
        images = json.loads(image_result.content)
        deadline = monotonic() + 15
        while monotonic() < deadline:
            status = self.client.get(f"/api/runs/{images['items'][0]['run_id']}").json()["status"]
            if status == "awaiting_approval":
                break
            sleep(.05)
        self.assertEqual(status, "awaiting_approval")
        source = self.root / "same-turn-video"
        source.mkdir()
        (source / "SKILL.md").write_text("---\nname: same-turn-video\ndescription: test\ncreatoros-outputs: [video]\n---\nbody\n", encoding="utf-8")
        skill = self.catalog.register_local(source, role="production")
        self.app.state.skill_library.add("review-lab", skill["id"], uuid4().hex)
        output = io.BytesIO()
        with wave.open(output, "wb") as sound:
            sound.setnchannels(1); sound.setsampwidth(2); sound.setframerate(8000)
            sound.writeframes(b"\0\0" * 8000)
        bgm = self.app.state.materials.put("review-lab", "same-turn.wav", output.getvalue())
        # A plan collects only these exact Runs in an account-owned, topic-independent folder.
        precise = self.app.state.media_collections.create("review-lab", name="本轮精确收图",
            request_id=uuid4().hex, run_ids=[item["run_id"] for item in images["items"]])
        self.assertIsNone(precise["topic_id"])
        video_arguments = {"topic_id": "review-1", "tasks": [{"kind": "video", "title": "同轮视频", "brief": "承接这些图片与音乐",
            "skills": [{"skill_id": skill["id"], "role": "primary"}],
            "inputs": {"collection_id": precise["id"], "bgm_material_id": bgm["id"]},
            "depends_on": [item["id"] for item in images["items"]]}]}
        video_result = self.call("start_content_run", context, **video_arguments)
        self.assertFalse(video_result.is_error, video_result.content)
        videos = json.loads(video_result.content)
        self.assertNotEqual(videos["items"][0]["id"], images["items"][0]["id"])
        replay = self.call("start_content_run", context, **video_arguments)
        self.assertFalse(replay.is_error, replay.content)
        self.assertEqual([item["id"] for item in json.loads(replay.content)["items"]], [item["id"] for item in videos["items"]])
        explicit = deepcopy(video_arguments)
        explicit["tasks"][0]["skills"][0]["digest"] = skill["digest"]
        explicit_replay = self.call("start_content_run", context, **explicit)
        self.assertFalse(explicit_replay.is_error, explicit_replay.content)
        self.assertEqual(json.loads(explicit_replay.content)["items"][0]["id"], videos["items"][0]["id"])
        repeated_images = self.call("start_content_run", context, **image_arguments)
        self.assertFalse(repeated_images.is_error, repeated_images.content)
        self.assertEqual(json.loads(repeated_images.content)["items"][0]["id"], images["items"][0]["id"])
        with self.database.session() as session:
            self.assertEqual(session.query(ProductionTask).count(), 2)
            video_task = session.get(ProductionTask, videos["items"][0]["id"])
            self.assertEqual(video_task.definition_json["execution_spec"]["skills"][0]["digest"], skill["digest"])
        stale = self.call("start_content_run", self.context(), topic_id="review-1", tasks=[{
            "title": "过期摘要不得提交", "brief": "reject", "skills": [{
                "skill_id": self.legacy["id"], "digest": "0" * 64, "role": "primary"}]}])
        self.assertTrue(stale.is_error, stale.content)
        self.assertEqual(stale.error_type, "skill_digest_conflict")

    def test_agent_video_accepts_original_share_text_without_import(self):
        """Actual scoped HTTP and isolated SQLite; controlled producer, no real Codex."""
        from tests.media_test_support import require_media
        require_media(self)
        context = self.context()
        image_result = self.call("start_content_run", context, topic_id="review-1", tasks=[{
            "title": "参考图片", "brief": "一张测试图片"}])
        self.assertFalse(image_result.is_error, image_result.content)
        images = json.loads(image_result.content)
        run_id = images["items"][0]["run_id"]
        deadline = monotonic() + 15
        while monotonic() < deadline:
            status = self.client.get(f"/api/runs/{run_id}").json()["status"]
            if status == "awaiting_approval": break
            sleep(.05)
        self.assertEqual(status, "awaiting_approval")
        source = self.root / "share-link-skill"
        source.mkdir()
        (source / "SKILL.md").write_text("---\nname: share-link-skill\ndescription: edit\ncreatoros-outputs: [video]\n---\n剪辑图片。\n", encoding="utf-8")
        skill = self.catalog.register_local(source, role="production")
        self.app.state.skill_library.add("review-lab", skill["id"], uuid4().hex)
        share = "2.89 复制打开抖音，看看【作品】 https://v.douyin.com/l5keSl2qzb8/ 07/01 :4pm C@H.ic"
        task = {"kind": "video", "title": "链接剪辑", "brief": "照音乐卡点", "skill_ids": [skill["id"]],
                "inputs": {"collection_id": images["collection_id"], "bgm_reference": share}}
        result = self.call("start_content_run", context, topic_id="review-1", tasks=[task])
        self.assertFalse(result.is_error, result.content)
        item = json.loads(result.content)["items"][0]
        with self.database.session() as session:
            saved = session.get(ProductionTask, item["id"])
            self.assertEqual(saved.definition_json["inputs"]["bgm_reference"], share)
        self.assertEqual(self.app.state.materials.list("review-lab"), [])


if __name__ == "__main__":
    unittest.main()
