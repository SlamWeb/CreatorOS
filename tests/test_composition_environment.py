"""Local failure injection: no paid models or formal data."""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from creatoros.integrations.codex import CodexProducerError, CodexSdkProducer
from creatoros.integrations.native_production import (
    CompositionReview, composition_environment_failure, load_checkpoint, require_ready,
)
from tests.smoke_native_production import NativeTransport, create_pair, image, producer_request


class BlockedTransport(NativeTransport):
    def _thread(self, thread_id):
        thread = super()._thread(thread_id)
        original = thread.turn

        async def turn(inputs, **controls):
            result = await original(inputs, **controls)
            if "output_schema" in controls:
                result.final_text = json.dumps({"status": "needs_input",
                    "note": "无法读取两份指定的 SKILL.md：拒绝访问工作区外路径。"})
            return result
        thread.turn = turn
        return thread


class CompositionEnvironmentTests(unittest.TestCase):
    def test_database_records_environment_as_retryable_not_content_conflict(self):
        from creatoros.integrations.producer_skills import ProducerSkillCatalog, skills_root_for
        from creatoros.runs import ContentRunExecutionError, ContentRunService
        from creatoros.storage import Creator, Database, Series, Topic, TopicSource, upgrade_database
        from tests.smoke_native_run_wiring import local_skill

        class UnavailableShell:
            def produce_to(self, **kwargs):
                raise CodexProducerError("PowerShell不可启动", error_type="codex_environment_error")

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            url = "sqlite:///" + (root / "test.sqlite").as_posix()
            upgrade_database(url)
            database = Database(url)
            try:
                catalog = ProducerSkillCatalog(skills_root_for(database))
                skill = local_skill(root, "maker", "production", catalog)
                mind = local_skill(root, "mind", "mind", catalog)
                with database.session() as session:
                    session.add(Creator(id="creator", display_name="Test", platform="xiaohongshu"))
                    session.add(Series(id="series", creator_id="creator", name="Test",
                                       description="", audience="", skill_name=None,
                                       mind_skill_id=mind["id"], production_skill_id=skill["id"]))
                    session.add(Topic(id="topic", series_id="series", title="Test",
                                      source=TopicSource.MANUAL, position=1))
                service = ContentRunService(database, output_root=root / "outputs",
                                            producer_factory=UnavailableShell)
                run = service.create("topic")
                with self.assertRaises(ContentRunExecutionError):
                    service.execute(run.id)
                failed = service.get(run.id)
                self.assertTrue(failed.retryable)
                self.assertEqual(failed.error_type, "codex_environment_error")
                with self.assertRaises(ContentRunExecutionError):
                    service.execute(run.id)
                attempts = service.repository.list_attempts(service.get_active_revision(run.id).id)
                self.assertEqual(len(attempts), 2)
                self.assertTrue(all(item.error_type == "codex_environment_error" for item in attempts))
            finally:
                database.close()

    def test_legacy_access_failure_is_not_a_conflict(self):
        review = CompositionReview(status="needs_input", note="无法读取SKILL.md：拒绝访问。")
        with self.assertRaises(CodexProducerError) as raised:
            require_ready(review)
        self.assertEqual(raised.exception.error_type, "codex_environment_error")
        self.assertNotIn("请通过", str(raised.exception))

    def test_real_conflict_still_requires_user_choice(self):
        with self.assertRaises(CodexProducerError) as raised:
            require_ready(CompositionReview(status="needs_input", note="八词各一格与一张六格冲突。"))
        self.assertEqual(raised.exception.error_type, "skill_composition_needs_input")
        self.assertFalse(composition_environment_failure(CompositionReview(status="ready", note="可以配合")))

    def test_explicit_technical_block(self):
        with self.assertRaises(CodexProducerError) as raised:
            require_ready(CompositionReview(status="blocked", note="读取工具不可用"))
        self.assertEqual(raised.exception.error_type, "codex_environment_error")

    def test_retry_rechecks_without_overwriting_failed_attempt(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog, pair = create_pair(root)
            producer = CodexSdkProducer(project_root=root, generated_images_root=root / "generated")
            NativeTransport.reset(producer.generated_images_root)
            first = root / "run/revision-001/attempt-001"
            with patch("openai_codex.AsyncCodex", BlockedTransport):
                with self.assertRaises(CodexProducerError) as raised:
                    producer_request(producer, first, pair, catalog.root)
            self.assertEqual(raised.exception.error_type, "codex_environment_error")
            self.assertEqual(load_checkpoint(first).composition_review.status, "blocked")
            self.assertEqual(NativeTransport.generated, {})
            before = {p.relative_to(first): p.read_bytes() for p in first.rglob("*") if p.is_file()}
            second = first.parent / "attempt-002"
            with patch("openai_codex.AsyncCodex", NativeTransport):
                result = producer_request(producer, second, pair, catalog.root)
            self.assertEqual(len(result.pack.cards), 1)
            self.assertEqual(len(NativeTransport.reviews), 2)
            self.assertEqual(NativeTransport.starts, ["native-thread-1"])
            self.assertEqual(NativeTransport.resumes, ["native-thread-1"])
            self.assertEqual(before, {p.relative_to(first): p.read_bytes()
                                     for p in first.rglob("*") if p.is_file()})
            self.assertEqual(len(NativeTransport.calls), 1)

    def test_blocked_preflight_with_unindexed_image_never_regenerates(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog, pair = create_pair(root)
            producer = CodexSdkProducer(project_root=root, generated_images_root=root / "generated")
            NativeTransport.reset(producer.generated_images_root)
            first = root / "run/revision-001/attempt-001"
            with patch("openai_codex.AsyncCodex", BlockedTransport):
                with self.assertRaises(CodexProducerError):
                    producer_request(producer, first, pair, catalog.root)
            checkpoint = load_checkpoint(first)
            image(root / "generated" / checkpoint.thread_id / "unindexed.png")
            with patch("openai_codex.AsyncCodex", NativeTransport):
                with self.assertRaises(CodexProducerError) as raised:
                    producer_request(producer, first.parent / "attempt-002", pair, catalog.root)
            self.assertEqual(raised.exception.error_type, "codex_environment_error")
            self.assertEqual(NativeTransport.resumes, [])
            self.assertEqual(NativeTransport.calls, [])


if __name__ == "__main__":
    unittest.main()
