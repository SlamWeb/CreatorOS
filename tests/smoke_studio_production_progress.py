"""Isolated HTTP/SQLite telemetry projection; no Codex or operational writes."""
import json
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from creatoros.integrations.production_progress import ProgressWriter
from creatoros.integrations.producer_skills import ProducerSkillCatalog, skills_root_for
from creatoros.runs import ContentRunService
from creatoros.storage import Database, ContentAttempt, Creator, CreatorPlatform, Series, Topic, TopicSource, upgrade_database
from creatoros.web import create_app
from tests.smoke_pair_production import ControlledPair, install_fixture


def main():
    with TemporaryDirectory(prefix="studio-progress-") as temporary, ExitStack() as cleanup:
        root = Path(temporary)
        url = f"sqlite:///{(root / 'fixture.db').as_posix()}"
        upgrade_database(url)
        db = Database(url)
        cleanup.callback(db.close)
        catalog = ProducerSkillCatalog(skills_root_for(db))
        mind = install_fixture(catalog, root, "knowledge-to-storyboard-deep", "https://github.com/SlamWeb/knowledge-to-storyboard", "mind")
        visual = install_fixture(catalog, root, "xiaobai", "https://github.com/SlamWeb/creatorOS-ip-skills", "production")
        with db.session() as session:
            session.add(Creator(id="account", display_name="Fixture", platform=CreatorPlatform.XIAOHONGSHU))
            session.add(Series(id="series", creator_id="account", name="Fixture", description="tech", audience="beginner", skill_name=None,
                               mind_skill_id=mind["id"], production_skill_id=visual["id"]))
            session.add(Topic(id="topic", series_id="series", title="消息队列", source=TopicSource.MANUAL, position=1))
        producer = ControlledPair(project_root=Path(__file__).parents[1], generated_images_root=root / "generated")
        producer.interrupt_next = False
        service = ContentRunService(db, producer_factory=lambda: producer, output_root=root / "outputs")
        run = service.create("topic")
        app = create_app(database=db, run_service=service)
        with TestClient(app) as client:
            assert client.get(f"/api/runs/{run.id}").json()["production_progress"] is None
            result = service.execute(run.id)
            directory = Path(result.artifact_directory)
            writer = ProgressWriter(directory, "visual", 2)
            writer.observe("item/completed", {"item": {"id": "tool", "type": "mcpToolCall", "arguments": "SECRET"}})
            detail = client.get(f"/api/runs/{run.id}")
            assert detail.status_code == 200
            assert detail.json()["production_progress"]["completed_tool_calls"] == 1
            assert "SECRET" not in detail.text and str(root) not in detail.text
            # Projection is read-only and does not advance the approval version.
            assert service.get(run.id).version == detail.json()["version"]
            progress = directory / "production_progress.json"
            good = progress.read_bytes()
            progress.write_text(json.dumps({"stage": "D:/SECRET", "activity": "bad"}), encoding="utf-8")
            assert client.get(f"/api/runs/{run.id}").json()["production_progress"] is None
            progress.write_bytes(good)
            outside = root / "outside"
            outside.mkdir()
            (outside / "production_progress.json").write_bytes(good)
            with db.session() as session:
                session.get(ContentAttempt, result.attempt_id).output_directory = str(outside)
            assert client.get(f"/api/runs/{run.id}").json()["production_progress"] is None
            with db.session() as session:
                session.get(ContentAttempt, result.attempt_id).output_directory = str(directory)
            service.request_revision(run.id, "new version", expected_version=service.get(run.id).version)
            assert client.get(f"/api/runs/{run.id}").json()["production_progress"] is None
        db.close()
    print("studio_production_progress_smoke=passed safe_projection legacy malformed cross_path active_revision readonly")


if __name__ == "__main__":
    main()
