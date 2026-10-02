from __future__ import annotations

import json
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, sleep

from fastapi.testclient import TestClient

from creatoros.integrations.codex import CodexProducerError
from creatoros.integrations.native_production import (
    CHECKPOINT,
    Checkpoint,
    CompositionReview,
    request_digest,
    skill_refs,
)
from creatoros.integrations.producer_skills import ProducerSkillCatalog, _digest, skills_root_for
from creatoros.integrations.skill_pair import freeze_pair
from creatoros.integrations.visual_production import atomic_json
from creatoros.runs import ContentRunService, ManagedRunExecutor
from creatoros.storage import Creator, CreatorPlatform, Database, Series, Topic, TopicSource, upgrade_database
from creatoros.web.app import create_app


CONFLICT = (
    "Skill composition needs input: Mind requires X; Visualize requires Y. "
    "Choose which requirement to follow before image generation."
)
CLARIFICATION = "Use Mind's requirement X; keep Visualize's compatible constraints."
TIMEOUT = "The clarified production attempt timed out after saving its review."


class ConflictProducer:
    def __init__(self):
        self.requests: list[dict] = []
        self.clarified_call = Event()

    def produce_to(self, **request):
        self.requests.append(request)
        if request.get("revision_instruction"):
            self.clarified_call.set()
        if request.get("topic_title") == "Invalid review":
            raise CodexProducerError(
                "The composition reviewer did not return valid JSON.",
                error_type="skill_composition_invalid",
            )
        directory = Path(request["directory"])
        directory.mkdir(parents=True, exist_ok=False)
        (directory / "work").mkdir()
        catalog = ProducerSkillCatalog(request["skills_root"])
        freeze_pair(catalog, request["composition"], directory,
                    source_root=directory.parent.parent / "skill-snapshot")
        payload = {key: request.get(key) for key in (
            "pack_id", "creator_id", "series_id", "topic_id", "topic_title",
            "topic_brief", "series_description", "audience", "revision_instruction", "previous_pages",
        )}
        (directory / "production_request.txt").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        refs = skill_refs(directory)
        checkpoint = Checkpoint(
            input_digest=request_digest(directory),
            thread_id=f"conflict-thread-{len(self.requests)}",
            skill_digests={role: _digest(path.parent) for role, path in refs},
            composition_review=CompositionReview(
                status="needs_input" if len(self.requests) <= 2 else "ready",
                note=CONFLICT if len(self.requests) <= 2 else "Clarification accepted.",
            ),
        )
        atomic_json(directory / CHECKPOINT, checkpoint)
        request["on_thread_started"](checkpoint.thread_id)
        if len(self.requests) >= 3:
            raise CodexProducerError(TIMEOUT, error_type="codex_timeout")
        raise CodexProducerError(
            CONFLICT,
            error_type="skill_composition_needs_input",
        )


def wait_for_status(client: TestClient, run_id: str, status: str) -> dict:
    deadline = monotonic() + 5
    while monotonic() < deadline:
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200, response.text
        detail = response.json()
        if detail["status"] == status:
            return detail
        sleep(0.02)
    raise AssertionError(f"Run did not reach {status!r} within 5 seconds")


with TemporaryDirectory() as temporary, ExitStack() as cleanup:
    root = Path(temporary)
    database_url = f"sqlite:///{(root / 'creatoros.db').as_posix()}"
    upgrade_database(database_url)
    database = Database(database_url)
    cleanup.callback(database.close)
    catalog = ProducerSkillCatalog(skills_root_for(database))
    skill_records = []
    for name, role in (("mind-notes", "mind"), ("visual-style", "production")):
        source = root / name
        source.mkdir()
        (source / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: composition flow fixture\n"
            "creatoros-output: social-content-pack.image-carousel\n---\n\nUse this test Skill.\n",
            encoding="utf-8",
        )
        skill_records.append(catalog.register_local(source, role=role))

    with database.session() as session:
        session.add(Creator(id="creator-1", display_name="One", platform=CreatorPlatform.XIAOHONGSHU))
        session.add(Series(
            id="series-1", creator_id="creator-1", name="Basics", description="Composition smoke",
            audience="beginners", skill_name=None, mind_skill_id=skill_records[0]["id"],
            production_skill_id=skill_records[1]["id"],
        ))
        session.add(Topic(id="topic-1", series_id="series-1", title="Skill conflict",
                          source=TopicSource.MANUAL, position=1))
        session.add(Topic(id="topic-2", series_id="series-1", title="Missing checkpoint",
                          source=TopicSource.MANUAL, position=2))
        session.add(Topic(id="topic-3", series_id="series-1", title="Invalid review",
                          source=TopicSource.MANUAL, position=3))

    producer = ConflictProducer()
    service = ContentRunService(
        database,
        producer_factory=lambda: producer,
        output_root=root / "outputs",
        production_protocol="native-v1",
    )
    executor = ManagedRunExecutor(service)
    app = create_app(database=database, run_service=service, run_executor=executor)
    with TestClient(app) as client:
        created = client.post("/api/runs", json={"topic_id": "topic-1"})
        assert created.status_code == 201, created.text
        run_id = created.json()["id"]

        started = client.post(
            f"/api/runs/{run_id}/execute",
            json={"expected_version": created.json()["version"]},
        )
        assert started.status_code == 202, started.text
        failed = wait_for_status(client, run_id, "failed")
        assert failed["error_type"] == "skill_composition_needs_input"
        assert failed["error_message"] == CONFLICT
        assert failed["retryable"] is False
        assert set(failed["allowed_actions"]) == {"revise", "cancel"}
        assert len(producer.requests) == 1

        resume = client.post(
            f"/api/runs/{run_id}/resume",
            json={"expected_version": failed["version"]},
        )
        assert resume.status_code == 409, resume.text
        assert len(producer.requests) == 1

        # A recorded conflict is insufficient when its pre-image evidence is missing.
        second_run = client.post("/api/runs", json={"topic_id": "topic-2"})
        assert second_run.status_code == 201, second_run.text
        second_id = second_run.json()["id"]
        second_start = client.post(
            f"/api/runs/{second_id}/execute",
            json={"expected_version": second_run.json()["version"]},
        )
        assert second_start.status_code == 202, second_start.text
        second_failed = wait_for_status(client, second_id, "failed")
        assert len(producer.requests) == 2
        from creatoros.runs import ContentRunRepository

        failed_revision = service.get_active_revision(second_id)
        failed_attempt = ContentRunRepository(database).list_attempts(failed_revision.id)[-1]
        (Path(failed_attempt.output_directory) / CHECKPOINT).unlink()
        second_revision = client.post(
            f"/api/runs/{second_id}/revisions",
            json={"expected_version": second_failed["version"], "instruction": CLARIFICATION},
        )
        assert second_revision.status_code == 201, second_revision.text
        missing_evidence_execute = client.post(
            f"/api/runs/{second_id}/execute",
            json={"expected_version": second_revision.json()["version"]},
        )
        assert missing_evidence_execute.status_code == 409, missing_evidence_execute.text
        assert missing_evidence_execute.json()["error"]["code"] == "invalid_revision_context"
        assert len(producer.requests) == 2

        revised = client.post(
            f"/api/runs/{run_id}/revisions",
            json={"expected_version": failed["version"], "instruction": CLARIFICATION},
        )
        assert revised.status_code == 201, revised.text
        queued = revised.json()
        assert queued["status"] == "queued"
        assert queued["active_revision_number"] == 2
        assert queued["revisions"][-1]["instruction"] == CLARIFICATION
        assert len(producer.requests) == 2  # Saving clarification is not execution.

        next_attempt = client.post(
            f"/api/runs/{run_id}/execute",
            json={"expected_version": queued["version"]},
        )
        assert next_attempt.status_code == 202, next_attempt.text
        assert producer.clarified_call.wait(2)
        assert producer.requests[2]["revision_instruction"] == CLARIFICATION
        assert producer.requests[2]["previous_pages"] is None
        clarified_failed = wait_for_status(client, run_id, "failed")
        assert clarified_failed["error_type"] == "codex_timeout" and clarified_failed["retryable"]

        # A same-Revision retry reuses the original explicit null context.
        retry = client.post(
            f"/api/runs/{run_id}/resume",
            json={"expected_version": clarified_failed["version"]},
        )
        assert retry.status_code == 202, retry.text
        retried = wait_for_status(client, run_id, "failed")
        assert retried["error_type"] == "codex_timeout"
        assert producer.requests[3]["revision_instruction"] == CLARIFICATION
        assert producer.requests[3]["previous_pages"] is None
        assert len(producer.requests) == 4

        # An invalid reviewer response has no decision to clarify; explicitly retry
        # the same Revision so the pre-image review can run again.
        invalid_run = client.post("/api/runs", json={"topic_id": "topic-3"})
        assert invalid_run.status_code == 201, invalid_run.text
        invalid_id = invalid_run.json()["id"]
        invalid_start = client.post(
            f"/api/runs/{invalid_id}/execute",
            json={"expected_version": invalid_run.json()["version"]},
        )
        assert invalid_start.status_code == 202, invalid_start.text
        invalid_failed = wait_for_status(client, invalid_id, "failed")
        assert invalid_failed["error_type"] == "skill_composition_invalid"
        assert invalid_failed["retryable"] is True
        invalid_retry = client.post(
            f"/api/runs/{invalid_id}/resume",
            json={"expected_version": invalid_failed["version"]},
        )
        assert invalid_retry.status_code == 202, invalid_retry.text
        invalid_failed_again = wait_for_status(client, invalid_id, "failed")
        assert invalid_failed_again["error_type"] == "skill_composition_invalid"
        assert len(producer.requests) == 6

print("composition_run_flow_smoke=passed needs_input=nonretryable invalid_review=retryable clarification=explicit_revision")
