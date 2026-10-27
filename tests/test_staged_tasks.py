"""Persistent dependency transitions with controlled execution, real DB/media files.

No model quality or live Codex claim; the live frontend run is recorded separately.
"""
from pathlib import Path
from threading import Event, RLock
import io
import unittest
import wave
from sqlalchemy import select, func

from creatoros.integrations.managed_materials import MaterialStore
from creatoros.integrations.media_tools import media_tool
from creatoros.integrations.skill_library import SkillLibraryService
from creatoros.runs import ContentRunService, ContentRunError
from creatoros.runs.production_tasks import ProductionTaskService
from creatoros.storage import ContentRun, ProductionTask, ContentRunStatus
from creatoros.storage.models import ProductionQueueItem
from creatoros.web.artifacts import StudioArtifacts
from creatoros.web.media_collections import MediaCollectionService
from tests import test_selected_skill_run as fixture
from tests.test_production_queue import Producer


class HeldDispatcher:
    """No process/model: assert the exact persisted queue before consumers start."""
    _lock = RLock()
    _wake = Event()
    def start(self): pass
    def _readiness_error(self): return None


class StagedTasksTests(unittest.TestCase):
    skill = fixture.SelectedSkillRunTests.skill

    def setUp(self):
        try:
            media_tool("ffmpeg"); media_tool("ffprobe")
        except ValueError as error:
            self.skipTest(str(error))
        fixture.SelectedSkillRunTests.setUp(self)
        self.runs = ContentRunService(self.db, output_root=self.root / "outputs",
                                     producer_factory=Producer, production_protocol="legacy")
        source = self.root / "editing"
        source.mkdir()
        (source / "SKILL.md").write_text("---\nname: edit-video\ndescription: editing\ncreatoros-outputs: [video]\n---\nKeep ordered frames.\n", encoding="utf-8")
        self.video = self.catalog.register_local(source, role="production")
        self.library = SkillLibraryService(self.db, self.catalog)
        self.library.add("creator", skill_id=self.video["id"], request_id="video-library")
        self.collections = MediaCollectionService(self.db, StudioArtifacts(self.db, self.runs.output_root))
        self.materials = MaterialStore(self.db)
        raw = io.BytesIO()
        with wave.open(raw, "wb") as sound:
            sound.setparams((1, 2, 8000, 0, "NONE", "not compressed")); sound.writeframes(b"\0\0" * 8000)
        self.audio = self.materials.put("creator", "music.wav", raw.getvalue())
        self.tasks = ProductionTaskService(self.db, self.runs, HeldDispatcher(),
            library=self.library, collections=self.collections, materials=self.materials)

    def image(self):
        # Preserve the legacy image fixture here; typed image is covered separately.
        result = self.tasks.create("topic", request_id="image", tasks=[dict(title="First",
            skill_overrides=dict(skill_id=self.base["id"], expected_digests={self.base["id"]: self.base["digest"]}))])
        return result, result["items"][0]

    def video_payload(self, image_result, image):
        return dict(title="Edit ordered images", kind="video", skills=[dict(skill_id=self.video["id"],
            digest=self.video["digest"], role="primary")], inputs=dict(collection_id=image_result["collection_id"],
            bgm_material_id=self.audio["id"]), depends_on=[image["id"]])

    def test_wait_freeze_dispatch_once_and_material_collection_does_not_collect_video(self):
        images, image = self.image()
        payload = self.video_payload(images, image)
        result = self.tasks.create("topic", request_id="video", tasks=[payload])
        task_id = result["items"][0]["id"]
        self.assertEqual(result["items"][0]["status"], "waiting")
        self.assertIsNone(result["items"][0]["run_id"])
        self.tasks.stages.advance()
        self.assertIsNone(self.tasks.list("topic")["items"][1]["run_id"])
        with self.db.session() as session:
            frozen = session.get(ProductionTask, task_id).definition_json["execution_spec"]
        self.assertTrue(Path(frozen["skills"][0]["path"]).joinpath("SKILL.md").is_file())
        run = self.runs.get(image["run_id"])
        self.runs.execute(run.id)
        self.tasks.stages.advance()
        rows = self.tasks.list("topic")["items"]
        self.assertEqual(rows[1]["dispatch_state"], "dispatched", rows[1])
        video_run = self.runs.get(rows[1]["run_id"])
        spec = video_run.input_snapshot_json["execution_spec"]
        self.assertEqual({value["kind"] for value in spec["materials"]}, {"audio", "images_zip"})
        self.assertIsNone(video_run.thread_id if hasattr(video_run, "thread_id") else None)
        self.tasks.stages.advance()
        replay = self.tasks.create("topic", request_id="video", tasks=[payload])
        self.assertEqual(replay["items"][0]["run_id"], video_run.id)
        detail = self.collections.get("creator", images["collection_id"])
        self.assertEqual({item["run_id"] for item in detail["items"]}, {image["run_id"]})
        with self.db.session() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(ContentRun)), 2)
            self.assertEqual(session.scalar(select(func.count()).select_from(ProductionQueueItem)), 2)

    def test_failed_dependency_pause_explicit_continue_and_cancel_do_not_create_run(self):
        images, image = self.image()
        result = self.tasks.create("topic", request_id="video", tasks=[self.video_payload(images, image)])
        identifier = result["items"][0]["id"]
        with self.db.session() as session:
            session.get(ContentRun, image["run_id"]).status = ContentRunStatus.FAILED
        self.tasks.stages.advance()
        self.assertEqual(self.tasks.list("topic")["items"][1]["status"], "needs_attention")
        continued = self.tasks.control("topic", identifier, "resume")
        self.assertEqual(continued["items"][0]["status"], "waiting")
        self.tasks.control("topic", identifier, "cancel")
        self.tasks.stages.advance()
        self.assertEqual(self.tasks.list("topic")["items"][1]["status"], "cancelled")
        with self.db.session() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(ContentRun)), 1)

    def test_video_rejects_collection_owned_by_another_topic(self):
        images, image = self.image()
        other = self.collections.create("creator", name="other", request_id="other-topic", topic_id="another")
        payload = self.video_payload(images, image)
        payload["inputs"]["collection_id"] = other["id"]
        with self.assertRaisesRegex(ValueError, "本选题素材集"):
            self.tasks.create("topic", request_id="bad-collection", tasks=[payload])

    def test_existing_completed_collection_dispatches_video_without_database_self_lock(self):
        images, image = self.image()
        self.runs.execute(image["run_id"])
        payload = self.video_payload(images, image)
        payload["depends_on"] = []
        result = self.tasks.create("topic", request_id="immediate-video", tasks=[payload])
        self.assertEqual(result["items"][0]["dispatch_state"], "dispatched")
        self.assertIsNotNone(result["items"][0]["run_id"])

    def test_explicit_old_export_cannot_silently_omit_waited_dependency(self):
        images, image = self.image()
        self.runs.execute(image["run_id"])
        detail = self.collections.get("creator", images["collection_id"])
        old_export = self.collections.export("creator", detail["id"], expected_version=detail["version"],
                                             snapshot_digest=detail["snapshot_digest"])
        second = self.tasks.create("topic", request_id="image-two", tasks=[dict(title="Second",
            skill_overrides=dict(skill_id=self.base["id"], expected_digests={self.base["id"]: self.base["digest"]}))])
        image_two = second["items"][0]
        payload = self.video_payload(images, image_two)
        payload["inputs"]["export_id"] = old_export["export_id"]
        created = self.tasks.create("topic", request_id="video-old-export", tasks=[payload])
        self.runs.execute(image_two["run_id"])
        self.tasks.stages.advance()
        result = self.tasks.list("topic")["items"][-1]
        self.assertEqual(result["dispatch_state"], "needs_attention")
        self.assertIsNone(result["run_id"])

    def test_builtin_stage_default_is_supported_without_registered_record(self):
        builtin = self.catalog.BUILTIN_ID
        self.library.add("creator", skill_id=builtin, request_id="builtin-library")
        pool = self.library.get_series("single")
        self.library.set_series("single", pool["version"], [self.base["id"], builtin],
            {"image": [dict(skill_id=builtin, role="primary")], "video": []})
        result = self.tasks.create("topic", request_id="builtin-task", tasks=[dict(title="Builtin")])
        run = self.runs.get(result["items"][0]["run_id"])
        self.assertEqual(run.input_snapshot_json["production_protocol"], "task-v1")
        self.assertEqual(run.input_snapshot_json["execution_spec"]["skills"][0]["skill_id"], builtin)

    def test_account_skill_can_be_selected_without_changing_series_pool(self):
        self.library.add("creator", skill_id=self.visual["id"], request_id="extra-account-skill")
        before = self.library.get_series("single")
        self.assertNotIn(self.visual["id"], [item["id"] for item in before["items"]])
        result = self.tasks.create("topic", request_id="account-skill-task", tasks=[
            dict(title="Account skill", skill_ids=[self.visual["id"]])])
        run = self.runs.get(result["items"][0]["run_id"])
        self.assertEqual(run.input_snapshot_json["execution_spec"]["skills"][0]["skill_id"], self.visual["id"])
        self.assertEqual(self.library.get_series("single"), before)

    def test_single_start_uses_explicit_stage_default_and_replay_keeps_frozen_edition(self):
        pool = self.library.get_series("single")
        self.library.set_series("single", pool["version"], [self.base["id"]],
            {"image": [dict(skill_id=self.base["id"], role="primary")], "video": []})
        run = self.runs.create("topic")
        self.assertEqual(run.input_snapshot_json["production_protocol"], "task-v1")
        self.assertEqual(run.input_snapshot_json["execution_spec"]["skills"][0]["skill_id"], self.base["id"])
        self.assertEqual(self.runs.create("topic").id, run.id)

    def test_plain_stage_image_rework_freezes_but_fresh_reads_current_default_and_respects_clear(self):
        defaults = lambda skill: {"image": [dict(skill_id=skill, role="primary")], "video": []}
        pool = self.library.get_series("single")
        ids = [self.base["id"], self.visual["id"]]
        self.library.set_series("single", pool["version"], ids, defaults(self.base["id"]))
        source = self.runs.create("topic")
        with self.db.session() as session:
            session.get(ContentRun, source.id).status = ContentRunStatus.FAILED
        pool = self.library.get_series("single")
        self.library.set_series("single", pool["version"], ids, defaults(self.visual["id"]))
        revision = self.runs.request_revision(source.id, "Keep existing draft and fix text",
                                              expected_version=self.runs.get(source.id).version)
        self.assertEqual(revision.production_input_json["execution_spec"]["skills"][0]["skill_id"], self.base["id"])
        with self.db.session() as session:
            session.get(ContentRun, source.id).status = ContentRunStatus.FAILED
        fresh = self.runs.restart_fresh(source.id, expected_version=self.runs.get(source.id).version, request_id="new-stage-default")
        self.assertEqual(fresh.input_snapshot_json["execution_spec"]["skills"][0]["skill_id"], self.visual["id"])
        self.assertIsNone(fresh.producer_thread_id)
        pool = self.library.get_series("single")
        self.library.set_series("single", pool["version"], ids, {"image": [], "video": []})
        with self.db.session() as session:
            session.get(ContentRun, fresh.id).status = ContentRunStatus.FAILED
        with self.assertRaises(ContentRunError) as denied:
            self.runs.restart_fresh(fresh.id, expected_version=self.runs.get(fresh.id).version, request_id="cleared-default")
        self.assertEqual(denied.exception.code, "skill_selection_required")

    def test_task_image_fresh_retains_own_selection_after_column_default_changes(self):
        result = self.tasks.create("topic", request_id="typed-image", tasks=[dict(title="Explicit image",
            skills=[dict(skill_id=self.base["id"], digest=self.base["digest"], role="primary")])])
        source = self.runs.get(result["items"][0]["run_id"])
        with self.db.session() as session:
            session.get(ContentRun, source.id).status = ContentRunStatus.FAILED
        pool = self.library.get_series("single")
        self.library.set_series("single", pool["version"], [self.base["id"], self.visual["id"]],
            {"image": [dict(skill_id=self.visual["id"], role="primary")], "video": []})
        fresh = self.runs.restart_fresh(source.id, expected_version=self.runs.get(source.id).version, request_id="task-fresh")
        self.assertEqual(fresh.task_id, source.task_id)
        self.assertEqual(fresh.input_snapshot_json["execution_spec"]["skills"][0]["skill_id"], self.base["id"])

    def test_scoped_video_fresh_needs_no_image_default_and_rejects_image_overrides(self):
        import httpx
        from creatoros.web.app import create_app
        from tests.agent_studio_support import serve
        images, image = self.image()
        self.runs.execute(image["run_id"])
        payload = self.video_payload(images, image)
        payload["depends_on"] = []
        result = self.tasks.create("topic", request_id="video-fresh-guards", tasks=[payload])
        source = self.runs.get(result["items"][0]["run_id"])
        with self.db.session() as session:
            session.get(ContentRun, source.id).status = ContentRunStatus.FAILED
        pool = self.library.get_series("single")
        self.library.set_series("single", pool["version"], [self.base["id"], self.video["id"]],
            {"image": [], "video": [dict(skill_id=self.video["id"], role="primary")]})
        app = create_app(database=self.db, run_service=self.runs, defer_background_start=True)
        with serve(app) as url, httpx.Client(base_url=url, trust_env=False) as client:
            sid = client.post("/api/agent/sessions", json={"creator_id": "creator"}).json()["id"]
            headers = {"x-creatoros-agent-session": sid}
            request = dict(topic_id="topic", mode="fresh", source_run_id=source.id,
                           expected_version=self.runs.get(source.id).version, request_id="video-http-fresh")
            invalid = client.post("/api/runs", headers=headers, json={**request,
                "skill_overrides": dict(skill_id=self.base["id"], expected_digests={self.base["id"]: self.base["digest"]})})
            self.assertEqual(invalid.status_code, 422, invalid.text)
            fresh = client.post("/api/runs", headers=headers, json=request)
            self.assertEqual(fresh.status_code, 201, fresh.text)
            self.assertEqual(fresh.json()["task_kind"], "video")
            self.assertEqual(fresh.json()["task_id"], source.task_id)
            self.assertIsNone(fresh.json()["producer_thread_id"])

    def test_waiting_task_cannot_be_listed_or_cancelled_after_account_transfer(self):
        from creatoros.storage import Series
        images, image = self.image()
        video = self.tasks.create("topic", request_id="waiting-owner", tasks=[self.video_payload(images, image)])
        identifier = video["items"][0]["id"]
        self.repo.create_creator(creator_id="other", display_name="Other")
        with self.db.session() as session:
            session.get(Series, "single").creator_id = "other"
        with self.assertRaises(ContentRunError) as listed:
            self.tasks._list("topic", [identifier])
        self.assertEqual(listed.exception.code, "scope_conflict")
        with self.assertRaises(ContentRunError) as cancelled:
            self.tasks.control("topic", identifier, "cancel")
        self.assertEqual(cancelled.exception.code, "scope_conflict")
        with self.db.session() as session:
            self.assertEqual(session.get(ProductionTask, identifier).dispatch_state, "waiting")

    def test_invalid_dependency_rolls_back_task_and_no_arbitrary_path_contract(self):
        images, image = self.image()
        payload = self.video_payload(images, image)
        payload["depends_on"] = ["foreign"]
        with self.assertRaises(ContentRunError):
            self.tasks.create("topic", request_id="bad", tasks=[payload])
        payload["depends_on"] = [image["id"]]
        payload["skills"][0]["path"] = "C:/private"
        with self.assertRaises(ContentRunError):
            self.tasks.create("topic", request_id="path", tasks=[payload])
        self.assertEqual(len(self.tasks.list("topic")["items"]), 1)
