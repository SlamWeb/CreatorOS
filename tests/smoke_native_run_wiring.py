"""Local wiring checks for native-v1 protocol snapshots; no paid production."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event

from PIL import Image

from creatoros.content import CarouselCard, PublicationCopy, SocialContentPack
from creatoros.integrations.codex import CodexUsage, ProducedPack, ProductionSession
from creatoros.integrations.native_production import (
    Artifact,
    Checkpoint,
    Delivery,
    SavedArtifact,
    request_digest,
    skill_refs,
)
from creatoros.integrations.producer_skills import ProducerSkillCatalog, _digest, skills_root_for
from creatoros.integrations.skill_pair import freeze_pair, prepare_run_pair, snapshot_pair
from creatoros.runs import ContentRunExecutionError, ContentRunService
from creatoros.runs.models import ContentRunInput
from creatoros.storage import (
    ContentRun,
    Creator,
    CreatorPlatform,
    Database,
    Series,
    Topic,
    TopicSource,
    upgrade_database,
)


class NativeDeliveryError(RuntimeError):
    error_type = "native_delivery_failed"


class FailingNativeProducer:
    def __init__(self):
        self.kwargs = None

    def produce_to(self, **kwargs):
        self.kwargs = kwargs
        raise NativeDeliveryError("local delivery fault injection")


def local_skill(root: Path, name: str, role: str, catalog: ProducerSkillCatalog):
    source = root / name
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: local native fixture\n---\n\nUse the skill for this role.\n",
        encoding="utf-8",
    )
    return catalog.register_local(source, role=role)


def write_native_attempt(service: ContentRunService, prepared: dict, catalog: ProducerSkillCatalog, owner_id: str):
    directory = prepared["directory"]
    directory.mkdir(parents=True)
    (directory / "partial-images").mkdir()
    (directory / "images").mkdir()
    pair = prepared["input"].composition
    freeze_pair(catalog, pair, directory, source_root=directory.parent.parent / "skill-snapshot")
    payload = {
        "pack_id": f"{prepared['run_id']}-r{prepared['revision_number']:03d}",
        "creator_id": prepared["input"].creator_id,
        "series_id": prepared["input"].series_id,
        "topic_id": prepared["input"].topic_id,
        "topic_title": prepared["input"].topic_title,
        "topic_brief": prepared["input"].topic_brief,
        "series_description": prepared["input"].series_description,
        "audience": prepared["input"].audience,
        "revision_instruction": prepared["instruction"],
        "previous_pages": prepared["previous_pages"],
    }
    (directory / "production_request.txt").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    partial = directory / "partial-images" / "01.png"
    Image.new("RGB", (32, 48), "#48536c").save(partial)
    checksum = hashlib.sha256(partial.read_bytes()).hexdigest()
    (directory / "images" / "01.png").write_bytes(partial.read_bytes())
    prompt, content, source_image = "Old prompt", "Previous draft body", "generator/page-1.png"
    artifact = Artifact(order=1, source_image_path=source_image, image_prompt=prompt)
    checkpoint = Checkpoint(
        input_digest=request_digest(directory),
        thread_id="old-thread",
        skill_digests={role: _digest(path.parent) for role, path in skill_refs(directory)},
        pages=[SavedArtifact(
            order=1, source_image_path=source_image, image_prompt=prompt, image_path=str(partial.resolve()),
            sha256=checksum, content=content,
        )],
        delivery=Delivery(title="Old title", text="Old publication text", hashtags=["old-tag"],
                         complete=True, artifacts=[artifact]),
        turn_completed=True,
    )
    (directory / "native_checkpoint.json").write_text(checkpoint.model_dump_json(indent=2), encoding="utf-8")
    pack = SocialContentPack(
        pack_id=payload["pack_id"], creator_id=payload["creator_id"], series_id=payload["series_id"],
        topic_id=payload["topic_id"], topic_title=payload["topic_title"], skill_name=pair.production.id,
        generated_at="2026-10-02T12:00:00+08:00", content_summary="Old title",
        cards=[CarouselCard(order=1, kind="cover", headline="Old title", body=content,
                            visual_brief=prompt, image_path="images/01.png")],
        publish_copy=PublicationCopy(title="Old title", body="Old publication text", hashtags=["old-tag"]),
    )
    (directory / "social_content_pack.json").write_text(pack.model_dump_json(indent=2), encoding="utf-8")
    produced = ProducedPack(
        directory=directory,
        pack=pack,
        session=ProductionSession(thread_id="old-thread", pack_id=pack.pack_id,
                                  created_at="2026-10-02T12:00:00+08:00", usage=CodexUsage()),
    )
    service._mark_produced(prepared["run_id"], prepared["attempt_id"], produced, 0, owner_id=owner_id)
    service._validate_active(prepared["run_id"], owner_id=owner_id)
    return directory


def main():
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        database_url = f"sqlite:///{(root / 'native.db').as_posix()}"
        upgrade_database(database_url)
        database = Database(database_url)
        try:
            catalog = ProducerSkillCatalog(skills_root_for(database))
            mind = local_skill(root, "field-notes", "mind", catalog)
            production = local_skill(root, "panel-maker", "production", catalog)

            native_pair = snapshot_pair(catalog, mind["id"], production["id"], native=True)
            assert native_pair.adapter == "native-carousel-v1"
            assert native_pair.mind.github_url is None and native_pair.mind.commit is None
            assert native_pair.production.github_url is None and native_pair.production.commit is None
            assert not (Path(production["local_path"]) / "assets" / "character.png").exists()
            try:
                snapshot_pair(catalog, mind["id"], production["id"])
            except ValueError:
                pass
            else:
                raise AssertionError("The legacy adapter must keep rejecting arbitrary pairs")

            frozen = prepare_run_pair(
                catalog, native_pair, root / "pair-run", [], first_start=True
            )
            assert frozen == native_pair
            assert (root / "pair-run" / "skill-snapshot" / "skills" / "mind" / "SKILL.md").is_file()
            assert (root / "pair-run" / "skill-snapshot" / "skills" / "production" / "SKILL.md").is_file()

            with database.session() as session:
                session.add(Creator(id="creator", display_name="Local", platform=CreatorPlatform.XIAOHONGSHU))
                session.add(Series(
                    id="native-series", creator_id="creator", name="Native", description="",
                    audience="", skill_name=None, mind_skill_id=mind["id"],
                    production_skill_id=production["id"],
                ))
                session.add(Topic(id="native-topic", series_id="native-series", title="Native test",
                                  brief="", source=TopicSource.MANUAL, position=1))
                session.add(Topic(id="context-topic", series_id="native-series", title="Revision context",
                                  brief="", source=TopicSource.MANUAL, position=2))
                session.add(Series(
                    id="legacy-series", creator_id="creator", name="Legacy", description="",
                    audience="", skill_name=ProducerSkillCatalog.BUILTIN_ID,
                    mind_skill_id=None, production_skill_id=None,
                ))
                session.add(Topic(id="legacy-topic", series_id="legacy-series", title="Legacy test",
                                  brief="", source=TopicSource.MANUAL, position=1))

            producer = FailingNativeProducer()
            native_service = ContentRunService(
                database, producer_factory=lambda: producer, output_root=root / "outputs"
            )
            native_run = native_service.create("native-topic")
            native_input = ContentRunInput.model_validate(native_run.input_snapshot_json)
            assert native_input.production_protocol == "native-v1"
            assert native_input.composition.adapter == "native-carousel-v1"

            # A stale Run-level handle from another Revision must not enter native dispatch.
            with database.session() as session:
                session.get(ContentRun, native_run.id).producer_thread_id = "old-revision-thread"
            try:
                native_service.execute(native_run.id)
            except ContentRunExecutionError:
                pass
            else:
                raise AssertionError("The injected native delivery failure must reach the caller")
            failed = native_service.get(native_run.id)
            assert failed.error_type == "native_delivery_failed" and failed.retryable
            assert producer.kwargs["production_protocol"] == "native-v1"
            assert producer.kwargs["thread_id"] is None
            assert producer.kwargs["previous_pages"] is None

            # A new native Revision receives verified text evidence from its own
            # preceding Revision; image paths and bytes are not reused as input.
            context_run = native_service.create("context-topic")
            context_prepared = native_service._begin_attempt(context_run.id, owner_id="fixture-owner")
            prior_directory = write_native_attempt(native_service, context_prepared, catalog, "fixture-owner")
            native_service.request_revision(
                context_run.id, "Shorten the title", expected_version=native_service.get(context_run.id).version
            )
            revision_prepared = native_service._begin_attempt(context_run.id, owner_id="revision-owner")
            original_context = revision_prepared["previous_pages"]
            context = json.loads(original_context)
            assert context["delivery"] == {
                "title": "Old title", "text": "Old publication text", "hashtags": ["old-tag"]
            }
            assert context["pages"] == [{"order": 1, "content": "Previous draft body", "image_prompt": "Old prompt"}]
            assert "generator/page-1.png" not in original_context and "partial-images" not in original_context

            # Freeze that input on Attempt 1. Even if the previous Revision later
            # changes, a retry of this Revision must use the exact saved request.
            revision_prepared["directory"].mkdir(parents=True)
            request_value = json.dumps({"previous_pages": original_context}, ensure_ascii=False)
            (revision_prepared["directory"] / "production_request.txt").write_text(request_value, encoding="utf-8")
            native_service._mark_failed(
                context_run.id, revision_prepared["attempt_id"], stage="producing",
                error=NativeDeliveryError("retry fixture"), elapsed=0, owner_id="revision-owner",
            )
            checkpoint_path = prior_directory / "native_checkpoint.json"
            checkpoint_path.write_text("changed after the first Attempt", encoding="utf-8")
            retry_prepared = native_service._begin_attempt(context_run.id, owner_id="revision-retry")
            assert retry_prepared["previous_pages"] == original_context
            try:
                native_service._produce_claimed(retry_prepared, owner_id="revision-retry", cancel_event=Event())
            except ContentRunExecutionError:
                pass
            else:
                raise AssertionError("The injected native retry delivery failure must reach the caller")
            assert producer.kwargs["previous_pages"] == original_context
            assert producer.kwargs["thread_id"] is None

            legacy_service = ContentRunService(
                database,
                producer_factory=lambda: None,
                output_root=root / "legacy-outputs",
                production_protocol="legacy",
            )
            legacy_run = legacy_service.create("legacy-topic")
            legacy_input = ContentRunInput.model_validate(legacy_run.input_snapshot_json)
            assert legacy_input.production_protocol == "legacy"
            historical = dict(legacy_run.input_snapshot_json)
            historical.pop("production_protocol")
            assert ContentRunInput.model_validate(historical).production_protocol == "legacy"
        finally:
            database.close()

    print("native_run_wiring_smoke=passed arbitrary_pair legacy_gate snapshots retry_dispatch")


if __name__ == "__main__":
    main()
