"""Fault injection with real local PNGs/SQLite/HTTP; no paid image calls."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from creatoros.integrations.codex import CodexSdkProducer, CodexProducerError
from creatoros.integrations.visual_production import input_digest, load_checkpoint, verified_pages, VisualCheckpoint
from creatoros.integrations.producer_skills import ProducerSkillCatalog, skills_root_for
from creatoros.runs import ContentRunService
from creatoros.runs.service import ContentRunExecutionError
from creatoros.storage import Database, Creator, CreatorPlatform, Series, Topic, TopicSource, upgrade_database
from tests.smoke_pair_production import install_fixture
from tests.smoke_production_sessions import Transport


def main():
    # SDK initialization/thread-start RPCs are bounded and explicitly closed on timeout.
    import asyncio
    from time import monotonic
    from threading import Event
    from creatoros.integrations.codex import _production_client, _bounded_sdk
    class StuckClient:
        closed = False
        def __init__(self, config):
            pass
        async def __aenter__(self):
            await asyncio.Future()
        async def __aexit__(self, *args):
            StuckClient.closed = True
    async def startup_checks():
        with patch("openai_codex.AsyncCodex", StuckClient):
            try:
                async with _production_client(monotonic() + 0.03):
                    raise AssertionError("stuck initialization must timeout")
            except CodexProducerError as error:
                assert error.error_type == "codex_timeout" and StuckClient.closed
        stopped = Event()
        stopped.set()
        try:
            await _bounded_sdk(asyncio.sleep(5), monotonic() + 10, stopped)
            raise AssertionError("stopped startup must not continue")
        except CodexProducerError as error:
            assert error.error_type == "codex_interrupted"
    asyncio.run(startup_checks())
    Transport.calls, Transport.started_threads = [], []
    with TemporaryDirectory(prefix="visual-checkpoint-") as temporary:
        root = Path(temporary)
        url = f"sqlite:///{(root / 'fixture.db').as_posix()}"
        upgrade_database(url)
        db = Database(url)
        try:
            catalog = ProducerSkillCatalog(skills_root_for(db))
            mind = install_fixture(catalog, root, "knowledge-to-storyboard-deep",
                                   "https://github.com/SlamWeb/knowledge-to-storyboard", "mind")
            visual = install_fixture(catalog, root, "xiaobai", "https://github.com/SlamWeb/creatorOS-ip-skills", "production")
            with db.session() as session:
                session.add(Creator(id="account", display_name="Fixture", platform=CreatorPlatform.XIAOHONGSHU))
                session.add(Series(id="series", creator_id="account", name="Fixture", description="tech", audience="beginner",
                                   skill_name=None, mind_skill_id=mind["id"], production_skill_id=visual["id"]))
                session.add(Topic(id="topic", series_id="series", title="消息队列", source=TopicSource.MANUAL, position=1))
            producer = CodexSdkProducer(project_root=root, generated_images_root=root / "generated")
            Transport.generated_root = producer.generated_images_root
            service = ContentRunService(db, producer_factory=lambda: producer, output_root=root / "outputs",
                                        production_protocol="legacy")
            run = service.create("topic")
            Transport.fail_page_order = 2
            with patch("openai_codex.AsyncCodex", Transport):
                try:
                    service.execute(run.id)
                    raise AssertionError("Page 2 fault must propagate")
                except ContentRunExecutionError:
                    pass
                failed = service.get(run.id)
                assert failed.error_type == "visual_delivery_failed" and failed.retryable
                revision = service.get_active_revision(run.id)
                attempt = service.repository.list_attempts(revision.id)[-1]
                first = Path(attempt.output_directory)
                checkpoint = VisualCheckpoint.model_validate_json((first / "visual_checkpoint.json").read_text())
                assert [p.order for p in checkpoint.pages] == [1]
                assert attempt.usage_json is not None
                before_hash = checkpoint.pages[0].sha256
                Transport.fail_page_order = None
                result = service.execute(run.id)
                second = Path(result.artifact_directory)
                resumed = VisualCheckpoint.model_validate_json((second / "visual_checkpoint.json").read_text())
                assert result.status == "awaiting_approval" and len(resumed.pages) == 2
                assert resumed.pages[0].sha256 == before_hash
                assert resumed.pages[0].source_thread_id != resumed.visual_thread_id
                assert len([c for c in Transport.calls if c[1][1].name == "knowledge-to-storyboard-deep"]) == 1
                # Completed page 1 is not resubmitted; only failed page 2 is explicitly resumed.
                page_calls = [c for c in Transport.calls if c[2]["output_schema"]["title"] == "RenderedPage"]
                assert len(page_calls) == 3 and len({c[0] for c in page_calls}) == 2
                from creatoros.content import SocialContentPack
                pack = SocialContentPack.load(second)
                assert pack.cards[0].headline and pack.publish_copy.title

            digest = input_digest((second / "production_request.txt").read_text(), [
                ("mind", second / "skills/mind/SKILL.md"), ("visual", second / "skills/production/SKILL.md")])
            assert load_checkpoint(second, digest) is not None
            try:
                load_checkpoint(second, "wrong-input-digest")
                raise AssertionError("Different input must not resume")
            except ValueError:
                pass
            saved = second / "visual_checkpoint.json"
            raw = saved.read_text()
            changed = json.loads(raw)
            changed["storyboard"]["pages"][0]["page_spec"] = "silently rewritten"
            saved.write_text(json.dumps(changed))
            try:
                load_checkpoint(second, digest)
                raise AssertionError("Changed content must not resume")
            except ValueError:
                pass
            saved.write_text(raw)
            page_file = Path(resumed.pages[0].image_path)
            image_bytes = page_file.read_bytes()
            page_file.write_bytes(b"bad image")
            try:
                verified_pages(second, resumed)
                raise AssertionError("Changed bytes must not resume")
            except ValueError:
                pass
            page_file.write_bytes(image_bytes)
            # Expired deadlines are checked before any new model request.
            import asyncio
            from time import monotonic
            calls = len(Transport.calls)
            try:
                asyncio.run(producer._execute_stage_async("no work", second,
                    skill_name="xiaobai", skill_path=second / "skills/production/SKILL.md",
                    receipt_model=VisualCheckpoint, stage="visual", on_thread_started=None,
                    cancel_event=None, deadline=monotonic() - 1))
                raise AssertionError("Expired deadline must fail")
            except CodexProducerError as error:
                assert error.error_type == "codex_timeout" and len(Transport.calls) == calls
            # Reworked content is output; retry must retain the Revision's original previous_pages input.
            service.request_revision(run.id, "change the content story", expected_version=service.get(run.id).version)
            from creatoros.integrations.skill_pair import StoryboardReceipt
            revised_story = StoryboardReceipt.model_validate(dict(
                research_brief="revised official sources", causal_chain="new story",
                pages=[dict(order=1, page_spec="New first page"), dict(order=2, page_spec="New second page")]))
            Transport.fail_page_order = 2
            before = len(Transport.calls)
            with patch("openai_codex.AsyncCodex", Transport), patch("tests.smoke_production_sessions.STORY", revised_story):
                try:
                    service.execute(run.id)
                    raise AssertionError("Reworked page 2 must fail")
                except ContentRunExecutionError:
                    pass
                rev = service.get_active_revision(run.id)
                first_rework = Path(service.repository.list_attempts(rev.id)[-1].output_directory)
                original_request = (first_rework / "production_request.txt").read_text(encoding="utf-8")
                assert "revised official sources" not in json.loads(original_request)["previous_pages"]
                Transport.fail_page_order = None
                fixed = service.execute(run.id)
                assert (Path(fixed.artifact_directory) / "production_request.txt").read_text(encoding="utf-8") == original_request
                assert len([c for c in Transport.calls[before:] if c[1][1].name == "knowledge-to-storyboard-deep"]) == 1
            # Only one text-only receipt repair; never an unbounded page retry loop.
            service.request_revision(run.id, "new visual", expected_version=service.get(run.id).version)
            before = len(Transport.calls)
            Transport.invalid_page = True
            with patch("openai_codex.AsyncCodex", Transport):
                try:
                    service.execute(run.id)
                    raise AssertionError("Repeated bad page receipt must fail")
                except ContentRunExecutionError:
                    pass
            turns = [c for c in Transport.calls[before:] if c[2]["output_schema"]["title"] == "RenderedPage"]
            assert len(turns) == 2 and "严禁重新生图" in turns[1][1][0].text
            assert service.get(run.id).retryable
        finally:
            db.close()
            Transport.generated_root, Transport.fail_page_order, Transport.invalid_page = None, None, False
    print("visual_checkpoint_smoke=passed same_thread partial_save explicit_resume skip_completed metadata input_content_byte_guards deadline usage")


if __name__ == "__main__":
    main()
