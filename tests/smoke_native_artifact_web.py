"""Isolated native-v1 artifact and partial-image HTTP projection; no Codex calls."""
import hashlib
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
from PIL import Image

from creatoros.content import CarouselCard, PublicationCopy, SocialContentPack
from creatoros.integrations.native_production import (
    Artifact, Checkpoint, Delivery, SavedArtifact, request_digest,
)
from creatoros.integrations.producer_skills import _digest
from creatoros.runs import ContentRunService
from creatoros.runs.artifacts import validate_artifact
from creatoros.runs.models import ContentRunInput
from creatoros.storage import (
    ContentAttempt, ContentAttemptStatus, ContentRevision, ContentRun, ContentRunStatus,
    Creator, CreatorPlatform, Database, Series, Topic, TopicSource, upgrade_database,
)
from creatoros.web import create_app


def main():
    with TemporaryDirectory(prefix="studio-native-artifacts-") as temporary, ExitStack() as cleanup:
        root = Path(temporary)
        database_url = f"sqlite:///{(root / 'fixture.db').as_posix()}"
        upgrade_database(database_url)
        database = Database(database_url)
        cleanup.callback(database.close)

        output_root = root / "outputs"
        run_id, revision_id = "native-run", "native-revision"
        attempt_dir = output_root / "fixture-account" / "fixture-series" / run_id / "revision-001" / "attempt-001"
        (attempt_dir / "skills" / "single").mkdir(parents=True)
        (attempt_dir / "partial-images").mkdir()
        (attempt_dir / "images").mkdir()
        skill_dir = attempt_dir / "skills" / "single"
        (skill_dir / "SKILL.md").write_text("---\nname: fixture-skill\n---\nFixture skill.\n", encoding="utf-8")
        (attempt_dir / "production_request.txt").write_text("Isolated native-v1 fixture request.\n", encoding="utf-8")

        image_path = attempt_dir / "partial-images" / "01.png"
        Image.new("RGB", (32, 48), "#48536c").save(image_path)
        image_bytes = image_path.read_bytes()
        image_checksum = hashlib.sha256(image_bytes).hexdigest()
        (attempt_dir / "images" / "01.png").write_bytes(image_bytes)

        prompt = "Warm editorial illustration with a clear focal point."
        content = "Native page content appears in the existing page_spec field."
        source_image_path = "isolated-generator/page-1.png"
        delivery_artifact = Artifact(order=1, source_image_path=source_image_path,
                                     image_prompt=prompt, reference_assets=[])
        checkpoint = Checkpoint(
            input_digest=request_digest(attempt_dir),
            thread_id="isolated-native-thread",
            skill_digests={"single": _digest(skill_dir)},
            pages=[SavedArtifact(
                order=1, source_image_path=source_image_path, image_prompt=prompt,
                reference_assets=[], image_path=str(image_path.resolve()), sha256=image_checksum,
                content=content, warnings=["fixture warning"],
            )],
            delivery=Delivery(title="Native fixture", text="Native fixture copy.", complete=True,
                             artifacts=[delivery_artifact]),
            turn_completed=True,
        )
        (attempt_dir / "native_checkpoint.json").write_text(checkpoint.model_dump_json(indent=2), encoding="utf-8")

        pack = SocialContentPack(
            pack_id="native-run-r001", creator_id="fixture-account", series_id="fixture-series",
            topic_id="fixture-topic", topic_title="Native projection fixture", skill_name="fixture-skill",
            generated_at="2026-10-02T12:00:00+08:00", content_summary="Native fixture",
            cards=[CarouselCard(order=1, kind="cover", headline="Native fixture", body=content,
                                visual_brief=prompt, image_path="images/01.png")],
            publish_copy=PublicationCopy(title="Native fixture", body="Native fixture copy."),
        )
        (attempt_dir / "social_content_pack.json").write_text(pack.model_dump_json(indent=2), encoding="utf-8")
        checked = validate_artifact(attempt_dir, production_protocol="native-v1")
        validation = checked.model_dump(mode="json")

        run_input = ContentRunInput(
            production_protocol="native-v1", creator_id="fixture-account", series_id="fixture-series",
            series_name="Native fixture series", series_description="Isolated fixture", audience="Readers",
            skill_name="fixture-skill", topic_id="fixture-topic", topic_title="Native projection fixture",
        )
        snapshot = run_input.model_dump(mode="json", exclude_unset=True)
        with database.session() as session:
            session.add(Creator(id="fixture-account", display_name="Fixture", platform=CreatorPlatform.XIAOHONGSHU))
            session.add(Series(id="fixture-series", creator_id="fixture-account", name="Native fixture series",
                               description="Isolated fixture", audience="Readers", skill_name="fixture-skill"))
            session.add(Topic(id="fixture-topic", series_id="fixture-series", title="Native projection fixture",
                              source=TopicSource.MANUAL, position=1))
            session.add(ContentRun(id=run_id, topic_id="fixture-topic", idempotency_key="native-fixture",
                                   status=ContentRunStatus.APPROVED, input_snapshot_json=snapshot,
                                   approved_revision_id=revision_id,
                                   approved_artifact_digest=checked.artifact_digest))
            session.add(ContentRevision(id=revision_id, content_run_id=run_id, revision_number=1,
                                        production_input_json=snapshot, artifact_directory=str(attempt_dir),
                                        manifest_path=str(attempt_dir / "social_content_pack.json"),
                                        artifact_digest=checked.artifact_digest, validation_json=validation))
            session.add(ContentAttempt(id="native-attempt", revision_id=revision_id, attempt_number=1,
                                       status=ContentAttemptStatus.SUCCEEDED, output_directory=str(attempt_dir),
                                       producer_thread_id=checkpoint.thread_id))

        service = ContentRunService(database, output_root=output_root)
        app = create_app(database=database, run_service=service)
        with TestClient(app) as client:
            before = client.get(f"/api/runs/{run_id}")
            assert before.status_code == 200, before.text
            detail = before.json()
            card = detail["revisions"][0]["cards"][0]
            assert card["page_spec"] == content
            assert card["image_prompt"] == prompt
            assert detail["partial_cards"] == [{
                "order": 1,
                "image_url": f"/api/runs/{run_id}/partial-cards/1?checksum={image_checksum}",
                "warnings": ["fixture warning"],
            }]
            assert str(root) not in before.text and "image_path" not in before.text

            image_response = client.get(detail["partial_cards"][0]["image_url"])
            assert image_response.status_code == 200 and image_response.headers["content-type"] == "image/png"
            assert image_response.headers["cache-control"] == "no-store"
            assert client.get(f"/api/runs/{run_id}/partial-cards/1?checksum={'0' * 64}").status_code == 409
            download = client.get(f"/api/runs/{run_id}/download")
            assert download.status_code == 200 and download.headers["content-type"] == "application/zip"

            with database.session() as session:
                version_before = session.get(ContentRun, run_id).version
            assert client.get(f"/api/runs/{run_id}").status_code == 200
            with database.session() as session:
                assert session.get(ContentRun, run_id).version == version_before

            # A changed frozen Skill invalidates both native partial and final evidence views.
            (skill_dir / "SKILL.md").write_text("tampered skill\n", encoding="utf-8")
            invalid = client.get(f"/api/runs/{run_id}")
            assert invalid.status_code == 200 and invalid.json()["partial_cards"] == []
            assert client.get(f"/api/runs/{run_id}/partial-cards/1?checksum={image_checksum}").status_code == 404
        database.close()
    print("studio_native_artifact_web_smoke=passed native_page_projection=passed partial_image_checksum=passed frozen_skill_tamper=blocked readonly=passed")


if __name__ == "__main__":
    main()
