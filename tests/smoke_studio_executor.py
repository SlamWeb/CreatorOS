from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from concurrent.futures import ThreadPoolExecutor
from time import monotonic, sleep

from PIL import Image

from creatoros.content import CarouselCard, PublicationCopy, SocialContentPack
from creatoros.integrations.codex import CodexUsage, ProducedPack, ProductionSession
from creatoros.runs.ownership import ExecutionOwnershipError
from creatoros.web.queries import StudioQueryService
from creatoros.runs import (
    ContentRunError,
    ContentRunService,
    ManagedRunExecutor,
)
from creatoros.storage import (
    ContentAttemptStatus,
    ContentRepository,
    ContentRunStatus,
    CreatorPlatform,
    Database,
    TopicSource,
    upgrade_database,
)


class ControlledProducer:
    def __init__(self, *, block_first: bool = False):
        self.block_first = block_first
        self.calls = 0
        self.started = Event()
        self.release = Event()

    def produce_to(self, **request) -> ProducedPack:
        self.calls += 1
        request["on_thread_started"](f"thread-{request['topic_id']}")
        self.started.set()
        if self.block_first and self.calls == 1:
            while not self.release.wait(timeout=0.02):
                if request["cancel_event"].is_set():
                    break
        directory = Path(request["directory"])
        directory.mkdir(parents=True, exist_ok=False)
        image_path = directory / "images" / "01-cover.png"
        image_path.parent.mkdir()
        Image.new("RGB", (320, 480), "#f2eee7").save(image_path)
        pack = SocialContentPack(
            pack_id=request["pack_id"],
            creator_id=request["creator_id"],
            series_id=request["series_id"],
            topic_id=request["topic_id"],
            topic_title=request["topic_title"],
            skill_name="knowledge-to-carousel",
            generated_at="2026-09-04T12:00:00+08:00",
            content_summary="Controlled smoke artifact.",
            cards=[CarouselCard(order=1, kind="cover", headline="Smoke", image_path="images/01-cover.png")],
            publish_copy=PublicationCopy(title="Smoke", body="Controlled."),
        )
        (directory / "social_content_pack.json").write_text(pack.model_dump_json(), encoding="utf-8")
        session = ProductionSession(
            thread_id=f"thread-{request['topic_id']}",
            pack_id=request["pack_id"],
            created_at="2026-09-04T12:00:00+08:00",
            usage=CodexUsage(input_tokens=10, cached_input_tokens=5, output_tokens=5),
        )
        (directory / "production_session.json").write_text(session.model_dump_json(), encoding="utf-8")
        return ProducedPack(directory=directory, pack=pack, session=session)


class FailingProducer:
    def __init__(self, error: str = "producer init failed"):
        raise RuntimeError(error)


def wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return
        sleep(0.02)
    raise AssertionError("condition timed out")


def create_fixture(root: Path, *, topic_count: int = 2, name: str = "creatoros"):
    url = f"sqlite:///{(root / f'{name}.db').as_posix()}"
    upgrade_database(url)
    database = Database(url)
    content = ContentRepository(database)
    content.create_creator(creator_id="creator-1", display_name="One", platform=CreatorPlatform.XIAOHONGSHU)
    content.create_series(
        series_id="series-1", creator_id="creator-1", name="Basics",
        description="Runtime cards", audience="beginners", skill_name="knowledge-to-carousel",
    )
    for index in range(topic_count):
        content.add_topic(
            topic_id=f"topic-{index + 1}", series_id="series-1",
            title=f"Topic {index + 1}", source=TopicSource.MANUAL,
        )
    return url, database


def expect_error(code, action):
    try:
        action()
    except ContentRunError as error:
        assert error.code == code, error.code
    else:
        raise AssertionError(f"Expected {code}")


with TemporaryDirectory() as temporary:
    root = Path(temporary)
    url, database = create_fixture(root)
    producer = ControlledProducer(block_first=True)
    service = ContentRunService(database, producer_factory=lambda: producer, output_root=root / "outputs", lease_seconds=0.6)
    with ThreadPoolExecutor(max_workers=4) as clients:
        ids = list(clients.map(lambda _: service.create("topic-1").id, range(4)))
    assert len(set(ids)) == 1
    first, second = service.get(ids[0]), service.create("topic-2")
    executor = ManagedRunExecutor(service)
    executor.submit(first.id, expected_version=first.version)
    assert producer.started.wait(2)
    expect_error("already_running", lambda: executor.submit(first.id, expected_version=first.version))
    expect_error("producer_busy", lambda: executor.submit(second.id, expected_version=second.version))
    assert service.get(second.id).status is ContentRunStatus.QUEUED
    expect_error("conflict", lambda: service.cancel(first.id, expected_version=service.get(first.id).version))
    before = service.get(first.id)
    event_count = len(service.repository.list_events(first.id))
    sleep(0.25)
    after = service.get(first.id)
    assert after.version == before.version
    assert after.heartbeat_at > before.heartbeat_at
    assert len(service.repository.list_events(first.id)) == event_count
    assert StudioQueryService(database).overview().counts.producing_count == 1
    rival = ContentRunService(database)
    for action in (ManagedRunExecutor(rival).start, rival.recover_inflight):
        try:
            action()
        except ExecutionOwnershipError:
            pass
        else:
            raise AssertionError("second Web/CLI must not acquire or recover")
    producer.release.set()
    wait_until(lambda: not executor.is_submitted(first.id))
    assert service.get(first.id).status is ContentRunStatus.AWAITING_APPROVAL
    assert producer.calls == 1  # The second item never auto-starts.
    expect_error("version_conflict", lambda: executor.submit(second.id, expected_version=99))
    executor.submit(second.id, expected_version=second.version)
    wait_until(lambda: not executor.is_submitted(second.id))
    assert service.get(second.id).status is ContentRunStatus.AWAITING_APPROVAL
    executor.shutdown()
    database.close()

    url, database = create_fixture(root, topic_count=1, name="late")
    late_producer = ControlledProducer(block_first=True)
    late_service = ContentRunService(database, producer_factory=lambda: late_producer, output_root=root / "late-outputs")
    late_run = late_service.create("topic-1")
    late_executor = ManagedRunExecutor(late_service)
    late_executor.submit(late_run.id, expected_version=late_run.version)
    assert late_producer.started.wait(2)
    late_executor.shutdown()  # Producer returns a late result after cancellation.
    interrupted = late_service.get(late_run.id)
    assert interrupted.status is ContentRunStatus.INTERRUPTED
    assert late_service.get_active_revision(late_run.id).artifact_directory is None
    assert not late_service.guard.journal.exists()
    restarted = ManagedRunExecutor(late_service)
    restarted.start()
    assert late_producer.calls == 1
    restarted.submit(late_run.id, expected_version=interrupted.version)
    wait_until(lambda: not restarted.is_submitted(late_run.id))
    attempts = late_service.repository.list_attempts(late_service.get_active_revision(late_run.id).id)
    assert len(attempts) == 2 and attempts[0].producer_thread_id == attempts[1].producer_thread_id
    restarted.shutdown()
    database.close()

    url, database = create_fixture(root, topic_count=1, name="recovery")
    recovery_service = ContentRunService(database, output_root=root / "recovery-outputs")
    recovery_run = recovery_service.create("topic-1")
    recovery_service._begin_attempt(recovery_run.id, owner_id="dead-owner")
    expect_error("conflict", lambda: recovery_service.heartbeat(recovery_run.id, owner_id="wrong-owner"))
    assert recovery_service.recover_inflight()[0] == 1
    assert recovery_service.get(recovery_run.id).status is ContentRunStatus.INTERRUPTED
    with recovery_service.guard:
        recovery_service.guard.begin(owner_id="unknown", run_id=recovery_run.id, attempt_id="unknown")
    try:
        recovery_service.recover_inflight()
    except ExecutionOwnershipError:
        pass
    else:
        raise AssertionError("unclean execution must block recovery")
    with recovery_service.guard:
        recovery_service.guard.finish("unknown")  # Test-only: no child was spawned.
    database.close()

    url, database = create_fixture(root, topic_count=1, name="init-failure")
    failed_service = ContentRunService(database, producer_factory=FailingProducer, output_root=root / "init-failure-outputs")
    failed_run = failed_service.create("topic-1")
    failed_executor = ManagedRunExecutor(failed_service)
    failed_executor.submit(failed_run.id, expected_version=failed_run.version)
    wait_until(lambda: not failed_executor.is_submitted(failed_run.id))
    failed = failed_service.get(failed_run.id)
    assert failed.status is ContentRunStatus.FAILED and failed.error_type == "production_failed"
    assert failed.lease_owner is None and not failed_service.guard.journal.exists()
    failed_executor.shutdown()
    database.close()

    url, database = create_fixture(root, topic_count=1, name="schedule-failure")
    service = ContentRunService(database)
    run = service.create("topic-1")
    executor = ManagedRunExecutor(service)
    executor.start()
    executor._pool.shutdown()  # Fault injection: claim succeeds but scheduler rejects.
    try:
        executor.submit(run.id, expected_version=run.version)
    except RuntimeError:
        pass
    else:
        raise AssertionError("closed scheduler must reject")
    assert service.get(run.id).status is ContentRunStatus.INTERRUPTED
    assert not service.guard.journal.exists()
    executor.shutdown()
    database.close()

    url, database = create_fixture(root, topic_count=1, name="validation-restart")
    service = ContentRunService(database, output_root=root / "validation-outputs")
    run = service.create("topic-1")
    prepared = service._begin_attempt(run.id, owner_id="old-owner")
    producer = ControlledProducer()
    produced = producer.produce_to(directory=prepared["directory"], pack_id="validation", creator_id="creator-1",
                                   series_id="series-1", topic_id="topic-1", topic_title="Topic", on_thread_started=lambda _: None)
    service._mark_produced(run.id, prepared["attempt_id"], produced, 0, owner_id="old-owner")
    database.close()
    database = Database(url)
    service = ContentRunService(database, producer_factory=FailingProducer)
    assert service.recover_inflight() == (0, 1, 0)
    assert service.get(run.id).status is ContentRunStatus.AWAITING_APPROVAL
    assert len(service.repository.list_attempts(prepared["revision_id"])) == 1
    database.close()

print("studio_executor_smoke=passed busy=explicit claim=atomic heartbeat=stable_version restart=explicit validating=rechecked late_writeback=rejected failures=recovered")
