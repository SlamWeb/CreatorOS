"""Checksummed partial-card HTTP projection on an isolated database; no Codex calls."""
import hashlib
import shutil
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
from PIL import Image

from creatoros.integrations.producer_skills import ProducerSkillCatalog, skills_root_for
from creatoros.integrations.skill_pair import StoryboardReceipt, VisualPage
from creatoros.integrations.visual_production import SavedPage, VisualCheckpoint, VisualPlan, atomic_json, input_digest
from creatoros.runs import ContentRunService
from creatoros.storage import Database, Creator, CreatorPlatform, Series, Topic, TopicSource, upgrade_database
from creatoros.web import create_app
from tests.smoke_pair_production import ControlledPair, install_fixture


def main():
    with TemporaryDirectory(prefix="studio-partial-cards-") as temporary, ExitStack() as cleanup:
        root = Path(temporary)
        database_url = f"sqlite:///{(root / 'fixture.db').as_posix()}"
        upgrade_database(database_url)
        db = Database(database_url)
        cleanup.callback(db.close)
        catalog = ProducerSkillCatalog(skills_root_for(db))
        mind = install_fixture(catalog, root, "knowledge-to-storyboard-deep", "https://github.com/SlamWeb/knowledge-to-storyboard", "mind")
        visual = install_fixture(catalog, root, "xiaobai", "https://github.com/SlamWeb/creatorOS-ip-skills", "production")
        with db.session() as session:
            session.add(Creator(id="account", display_name="Fixture", platform=CreatorPlatform.XIAOHONGSHU))
            session.add(Series(id="series", creator_id="account", name="Fixture", description="tech", audience="beginner",
                               skill_name=None, mind_skill_id=mind["id"], production_skill_id=visual["id"]))
            session.add(Topic(id="topic", series_id="series", title="消息队列", source=TopicSource.MANUAL, position=1))
        producer = ControlledPair(project_root=Path(__file__).parents[1], generated_images_root=root / "generated")
        producer.interrupt_next = False
        service = ContentRunService(db, producer_factory=lambda: producer, output_root=root / "outputs")
        run = service.create("topic")
        app = create_app(database=db, run_service=service)
        with TestClient(app) as client:
            result = service.execute(run.id)
            attempt_dir = Path(result.artifact_directory)
            partial_root = attempt_dir / "partial-images"
            partial_root.mkdir()
            image_path = partial_root / "01.png"
            Image.new("RGB", (32, 48), "#48536c").save(image_path)
            checksum = hashlib.sha256(image_path.read_bytes()).hexdigest()

            storyboard = StoryboardReceipt.model_validate_json((attempt_dir / "storyboard.json").read_text(encoding="utf-8"))
            pair = service.get(run.id).input_snapshot_json["composition"]
            plan = VisualPlan(pages=[VisualPage(order=page.order, image_prompt=f"Fixture prompt {page.order}",
                                                reference_assets=["assets/character.png"])
                                     for page in storyboard.pages])
            (attempt_dir / "visual_plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
            prompt = (attempt_dir / "production_request.txt").read_text(encoding="utf-8")
            skill_refs = [(pair["mind"]["name"], attempt_dir / "skills" / "mind" / "SKILL.md"),
                          (pair["production"]["name"], attempt_dir / "skills" / "production" / "SKILL.md")]
            checkpoint = VisualCheckpoint(
                input_digest=input_digest(prompt, skill_refs), storyboard=storyboard, plan=plan,
                visual_thread_id="isolated-visual-thread",
                pages=[SavedPage(order=1, image_path=str(image_path.resolve()), source_image_path=str(image_path.resolve()),
                                 source_thread_id="isolated-visual-thread", sha256=checksum,
                                 image_prompt="Fixture prompt 1", reference_assets=["assets/character.png"],
                                 warnings=["此文字需要人工检查。"], attempts=1)],
            )
            atomic_json(attempt_dir / "visual_checkpoint.json", checkpoint)

            version = service.get(run.id).version
            detail_response = client.get(f"/api/runs/{run.id}")
            assert detail_response.status_code == 200, detail_response.text
            detail = detail_response.json()
            assert detail["partial_cards"] == [{
                "order": 1,
                "image_url": f"/api/runs/{run.id}/partial-cards/1?checksum={checksum}",
                "warnings": ["此文字需要人工检查。"],
            }]
            assert str(root) not in detail_response.text and str(attempt_dir) not in detail_response.text
            assert "image_path" not in detail_response.text and "source_image_path" not in detail_response.text
            image_response = client.get(detail["partial_cards"][0]["image_url"])
            assert image_response.status_code == 200 and image_response.headers["content-type"] == "image/png"
            assert image_response.headers["cache-control"] == "no-store"
            assert client.get(f"/api/runs/{run.id}/partial-cards/1?checksum={'0' * 64}").status_code == 409
            assert client.get(f"/api/runs/{run.id}/partial-cards/99?checksum={checksum}").status_code == 404
            assert service.get(run.id).version == version and producer.seen

            # A path outside attempt/partial-images invalidates the whole checkpoint projection.
            outside = root / "outside.png"
            shutil.copy2(image_path, outside)
            checkpoint.pages[0].image_path = str(outside.resolve())
            atomic_json(attempt_dir / "visual_checkpoint.json", checkpoint)
            assert client.get(f"/api/runs/{run.id}").json()["partial_cards"] == []
            assert client.get(f"/api/runs/{run.id}/partial-cards/1?checksum={checksum}").status_code == 404
            assert service.get(run.id).version == version
        db.close()
    print("studio_partial_cards_smoke=passed projection checksum route boundary old-safe readonly")


if __name__ == "__main__":
    main()
