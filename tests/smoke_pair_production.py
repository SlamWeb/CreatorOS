"""Isolated P4 contract/fault tests; no paid generation or production database writes."""
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from creatoros.integrations.codex import CodexSdkProducer, CodexRun, CodexUsage, CODEX_MODEL, CODEX_EFFORT
from creatoros.integrations.producer_skills import ProducerSkillCatalog, InstallReceipt, skills_root_for
from creatoros.integrations.skill_pair import PairReceipt, snapshot_pair, EVIDENCE_FILE
from creatoros.runs import ContentRunService, ContentRunRepository, ContentRunError
from creatoros.runs.artifacts import validate_artifact
from creatoros.runs.models import ContentRunInput
from creatoros.storage import Database, Creator, CreatorPlatform, Series, Topic, TopicSource, upgrade_database
from creatoros.web import create_app


def install_fixture(catalog, root, name, repo, role):
    workspace = root / name
    source = workspace / "source"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(f"---\nname: {name}\ndescription: isolated P4 fixture\n---\nfixture", encoding="utf-8")
    if role == "production":
        (source / "assets").mkdir()
        Image.new("RGB", (32, 32), "white").save(source / "assets" / "character.png")
    for args in [("init",), ("add", "."), ("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture"),
                 ("remote", "add", "origin", repo)]:
        subprocess.run(["git", "-C", str(source), *args], capture_output=True, check=True)
    return catalog.register(workspace, repo, InstallReceipt(skill_path=".", carousel_compatible=False, compatibility_note="fixture"), role=role)


class ControlledPair(CodexSdkProducer):
    interrupt_next = True
    seen = []

    def _execute(self, prompt, working_directory, *, thread_id=None, skill_refs=None, on_thread_started=None, **kwargs):
        assert self.receipt_model is PairReceipt
        assert len(skill_refs) == 2 and all(path.is_file() for _, path in skill_refs)
        assert "真正提交工具" in prompt and "不要发布" in prompt
        assert "PageSpec" in prompt and "消息队列" in prompt
        assert "transparent_background=false" in prompt
        self.seen.append(thread_id)
        thread = thread_id or "pair-thread"
        on_thread_started(thread)
        if self.interrupt_next:
            self.interrupt_next = False
            raise KeyboardInterrupt
        images = self.generated_images_root / thread
        images.mkdir(parents=True, exist_ok=True)
        cards, pages = [], []
        for i in (1, 2):
            image = images / f"{i}.png"
            Image.new("RGB", (108, 192), "white" if i == 1 else "blue").save(image)
            cards.append(dict(order=i, kind="cover" if i == 1 else "summary", section=None,
                              headline=f"消息队列 {i}", body=None, highlights=[], visual_brief=None, source_image_path=str(image)))
            pages.append(dict(order=i, page_spec=f"PageSpec {i}: 为什么需要队列？", image_prompt=f"Prompt {i}: draw the queue",
                              reference_assets=["assets/character.png"]))
        value = dict(content_summary="MQ", cards=cards, publish_copy=dict(title="MQ", body="body", hashtags=[]), sources=[],
                     research_brief="official docs", causal_chain="任务耗时 -> 解耦", pages=pages)
        self.receipt = PairReceipt.model_validate(value)
        return CodexRun(thread, self.receipt, CodexUsage())


def rejects(fn, error_type=(ValueError, OSError)):
    try:
        fn()
    except error_type:
        return
    raise AssertionError("Invalid artifact/request should be rejected")


def main():
    assert CODEX_MODEL == "gpt-6-luna" and CODEX_EFFORT == "xhigh"
    with TemporaryDirectory() as temp:
        root = Path(temp)
        url = f"sqlite:///{(root / 'creatoros.db').as_posix()}"
        upgrade_database(url)
        db = Database(url)
        catalog = ProducerSkillCatalog(skills_root_for(db))
        mind = install_fixture(catalog, root, "knowledge-to-storyboard-deep", "https://github.com/SlamWeb/knowledge-to-storyboard", "mind")
        visual = install_fixture(catalog, root, "xiaobai", "https://github.com/SlamWeb/creatorOS-ip-skills", "production")
        pair = snapshot_pair(catalog, mind["id"], visual["id"])
        with db.session() as session:
            session.add(Creator(id="account", display_name="Test", platform=CreatorPlatform.XIAOHONGSHU))
            session.add(Series(id="series", creator_id="account", name="Test", description="tech", audience="beginner",
                               skill_name=None, mind_skill_id=mind["id"], production_skill_id=visual["id"]))
            session.add(Topic(id="topic", series_id="series", title="消息队列", brief="source + angle", source=TopicSource.MANUAL, position=1))
        producer = ControlledPair(project_root=Path(__file__).parents[1], generated_images_root=root / "generated")
        service = ContentRunService(db, producer_factory=lambda: producer, output_root=root / "outputs")
        app = create_app(database=db, run_service=service)
        # HTTP create shares exactly the same frozen input/idempotency path as Agent tools.
        with TestClient(app) as client:
            response = client.post("/api/runs", json={"topic_id": "topic"})
            assert response.status_code == 201, response.text
            run_id = response.json()["id"]
            assert client.post("/api/runs", json={"topic_id": "topic"}).json()["id"] == run_id
        run = service.get(run_id)
        frozen = ContentRunInput.model_validate(run.input_snapshot_json)
        assert frozen.composition == pair and frozen.creator_name == "Test"
        with db.session() as session:
            s = session.get(Series, "series")
            s.description = "future configuration"
            s.mind_skill_id = "other--0000000000000000"
        assert ContentRunInput.model_validate(service.get(run_id).input_snapshot_json) == frozen
        try:
            service.execute(run_id)
        except KeyboardInterrupt:
            pass
        else:
            raise AssertionError("first attempt must interrupt")
        result = service.execute(run_id)
        assert result.status == "awaiting_approval" and producer.seen == [None, "pair-thread"]
        repository = ContentRunRepository(db)
        revision = repository.get_revision(result.revision_id)
        directory = Path(revision.artifact_directory)
        baseline = validate_artifact(directory, composition=pair).artifact_digest
        assert baseline == revision.artifact_digest
        assert len(repository.list_attempts(revision.id)) == 2

        # Wrong count, empty prompt, absent reference and duplicate generated image fail the receipt.
        for change in (lambda v: v["pages"].pop(),
                       lambda v: v["pages"][0].update(image_prompt=""),
                       lambda v: v["pages"][0].update(reference_assets=[]),
                       lambda v: v["cards"][1].update(source_image_path=v["cards"][0]["source_image_path"])):
            value = producer.receipt.model_dump()
            change(value)
            rejects(lambda: PairReceipt.model_validate(value), (ValidationError,))
        # Missing/changed intermediate artifact or reference cannot be approved.
        for file in (directory / EVIDENCE_FILE, directory / "pages/01/prompt.txt",
                     directory / "skills/production/assets/character.png"):
            raw = file.read_bytes()
            file.write_bytes(b"tampered")
            rejects(lambda: service.approve(run_id, revision_id=revision.id, artifact_digest=baseline,
                                           expected_version=service.get(run_id).version))
            file.write_bytes(raw)
            file.rename(file.with_suffix(file.suffix + ".missing"))
            rejects(lambda: validate_artifact(directory, composition=pair))
            file.with_suffix(file.suffix + ".missing").rename(file)
        # A syntactically valid changed evidence record still invalidates the approved digest.
        path = directory / EVIDENCE_FILE
        raw = path.read_bytes()
        value = json.loads(raw)
        value["research_brief"] = "changed research"
        path.write_text(json.dumps(value), encoding="utf-8")
        assert validate_artifact(directory, composition=pair).artifact_digest != baseline
        path.write_bytes(raw)
        with TestClient(create_app(database=db, run_service=service)) as client:
            details = client.get(f"/api/runs/{run_id}").json()
            assert details["revisions"][0]["artifact_available"] is True
            assert details["revisions"][0]["cards"][0]["page_spec"].startswith("PageSpec")
            # Valid JSON with invalid shape/path must fail closed, not produce an HTTP 500.
            for change in (lambda v: v.pop("pages"),
                           lambda v: v["pages"][0].update(image_prompt=""),
                           lambda v: v["pages"][0]["reference_assets"].append("../../../../outside.png")):
                value = json.loads(raw)
                change(value)
                path.write_text(json.dumps(value), encoding="utf-8")
                rejects(lambda: validate_artifact(directory, composition=pair))
                response = client.get(f"/api/runs/{run_id}")
                assert response.status_code == 200, response.text
                broken = response.json()["revisions"][0]
                assert not broken["artifact_available"] and broken["artifact_error"]
                path.write_bytes(raw)
        approved = service.approve(run_id, revision_id=revision.id, artifact_digest=baseline, expected_version=service.get(run_id).version)
        assert approved.status.value == "approved"
        # Existing snapshot cannot silently switch to modified installed resources.
        skill = catalog.locate(mind["id"]) / "SKILL.md"
        skill.write_text("modified", encoding="utf-8")
        rejects(lambda: snapshot_pair(catalog, mind["id"], visual["id"]))
        db.close()
    print("pair_production_smoke=passed snapshot resume receipt evidence digest http legacy_separate")


if __name__ == "__main__":
    main()
