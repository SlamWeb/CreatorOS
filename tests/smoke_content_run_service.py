from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from creatoros.content import CarouselCard, PublicationCopy, SocialContentPack
from creatoros.integrations.codex import CodexUsage, ProducedPack, ProductionSession
from creatoros.runs import ContentRunError, ContentRunRepository, ContentRunService
from creatoros.storage import (
    ContentAttemptStatus,
    ContentRepository,
    ContentRunEventType,
    ContentRunStatus,
    CreatorPlatform,
    Database,
    TopicSource,
    upgrade_database,
)


class ControlledProducer:
    def __init__(self):
        self.interrupt_next = True
        self.seen_thread_ids: list[str | None] = []

    def produce_to(self, **request) -> ProducedPack:
        self.seen_thread_ids.append(request["thread_id"])
        thread_id = request["thread_id"] or "thread-content-1"
        request["on_thread_started"](thread_id)
        if self.interrupt_next:
            self.interrupt_next = False
            raise KeyboardInterrupt
        directory = Path(request["directory"])
        directory.mkdir(parents=True, exist_ok=False)
        image_path = directory / "images" / "01-cover.png"
        image_path.parent.mkdir()
        Image.new("RGB", (1080, 1440), "#f2eee7").save(image_path)
        pack = SocialContentPack(
            pack_id=request["pack_id"],
            creator_id=request["creator_id"],
            series_id=request["series_id"],
            topic_id=request["topic_id"],
            topic_title=request["topic_title"],
            skill_name="knowledge-to-carousel",
            generated_at="2026-09-02T12:00:00+08:00",
            content_summary="A deterministic smoke artifact.",
            cards=[
                CarouselCard(
                    order=1,
                    kind="cover",
                    headline="What is Agent State?",
                    image_path="images/01-cover.png",
                )
            ],
            publish_copy=PublicationCopy(
                title="What is Agent State?",
                body="A concise explanation.",
            ),
        )
        (directory / "social_content_pack.json").write_text(
            pack.model_dump_json(indent=2), encoding="utf-8"
        )
        session = ProductionSession(
            thread_id=thread_id,
            pack_id=request["pack_id"],
            created_at="2026-09-02T12:00:00+08:00",
            usage=CodexUsage(input_tokens=100, cached_input_tokens=80, output_tokens=20),
        )
        (directory / "production_session.json").write_text(
            session.model_dump_json(indent=2), encoding="utf-8"
        )
        return ProducedPack(directory=directory, pack=pack, session=session)


with TemporaryDirectory() as temporary:
    root = Path(temporary)
    url = f"sqlite:///{(root / 'creatoros.db').as_posix()}"
    upgrade_database(url)
    database = Database(url)
    content = ContentRepository(database)
    content.create_creator(
        creator_id="creator-1",
        display_name="Creator One",
        platform=CreatorPlatform.XIAOHONGSHU,
    )
    content.create_series(
        series_id="series-1",
        creator_id="creator-1",
        name="Agent Basics",
        description="Agent interview cards",
        audience="beginners",
        skill_name="knowledge-to-carousel",
    )
    content.add_topic(
        topic_id="topic-1",
        series_id="series-1",
        title="What is Agent State?",
        source=TopicSource.MANUAL,
    )

    producer = ControlledProducer()
    service = ContentRunService(
        database,
        producer_factory=lambda: producer,
        output_root=root / "outputs",
    )
    created = service.create("topic-1", idempotency_key="daily:topic-1")
    assert service.create("topic-1", idempotency_key="daily:topic-1").id == created.id

    try:
        service.execute(created.id)
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("ControlledProducer should interrupt the first attempt.")
    interrupted = service.get(created.id)
    assert interrupted.status is ContentRunStatus.INTERRUPTED
    assert interrupted.producer_thread_id == "thread-content-1"

    first_result = service.execute(created.id)
    assert first_result.status == ContentRunStatus.AWAITING_APPROVAL.value
    assert producer.seen_thread_ids == [None, "thread-content-1"]
    first_ready = service.get(created.id)
    first_revision = ContentRunRepository(database).get_revision(first_result.revision_id)
    assert first_revision is not None and first_revision.artifact_digest
    attempts = ContentRunRepository(database).list_attempts(first_revision.id)
    assert [item.status for item in attempts] == [
        ContentAttemptStatus.INTERRUPTED,
        ContentAttemptStatus.SUCCEEDED,
    ]

    second_revision = service.request_revision(
        created.id,
        "Use a clearer restaurant analogy.",
        expected_version=first_ready.version,
    )
    assert second_revision.revision_number == 2
    second_result = service.execute(created.id)
    assert second_result.revision_id == second_revision.id
    assert producer.seen_thread_ids[-1] == "thread-content-1"

    ready = service.get(created.id)
    revision = ContentRunRepository(database).get_revision(second_revision.id)
    assert revision is not None and revision.artifact_directory and revision.artifact_digest
    image_path = Path(revision.artifact_directory) / "images" / "01-cover.png"
    original = image_path.read_bytes()
    image_path.write_bytes(original + b"tampered")
    try:
        service.approve(
            created.id,
            revision_id=revision.id,
            artifact_digest=revision.artifact_digest,
            expected_version=ready.version,
        )
    except ContentRunError:
        pass
    else:
        raise AssertionError("Changed artifact bytes should invalidate approval.")
    image_path.write_bytes(original)
    approved = service.approve(
        created.id,
        revision_id=revision.id,
        artifact_digest=revision.artifact_digest,
        expected_version=ready.version,
    )
    assert approved.status is ContentRunStatus.APPROVED
    assert approved.approved_revision_id == revision.id

    repository = ContentRunRepository(database)
    assert [item.revision_number for item in repository.list_revisions(created.id)] == [1, 2]
    event_types = [item.event_type for item in repository.list_events(created.id)]
    assert event_types == [
        ContentRunEventType.CREATED,
        ContentRunEventType.STARTED,
        ContentRunEventType.INTERRUPTED,
        ContentRunEventType.RESUMED,
        ContentRunEventType.PRODUCED,
        ContentRunEventType.VALIDATED,
        ContentRunEventType.REVISION_REQUESTED,
        ContentRunEventType.STARTED,
        ContentRunEventType.PRODUCED,
        ContentRunEventType.VALIDATED,
        ContentRunEventType.APPROVED,
    ]
    database.close()

print("content_run_service_smoke=passed interrupt=resume revision=2 digest_guard=passed")
