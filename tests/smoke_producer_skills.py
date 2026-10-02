"""Isolated catalog, binding and production-input regression; no model calls."""
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from creatoros.integrations.codex import CodexProducer
from creatoros.integrations.producer_skills import (
    ProducerSkillCatalog, SkillInstallService, InstallReceipt, _inspect_checkout, github_url, skills_root_for,
)
from creatoros.runs import ContentRunService
from creatoros.storage import Database, upgrade_database, Topic, TopicSource
from creatoros.web import create_app


def fixture(workspace, compatible=True):
    source = workspace / "source"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: test-carousel\ndescription: Test image carousel\ncreatoros-output: social-content-pack.image-carousel\n---\nSPECIAL_SKILL_MARKER\n", encoding="utf-8")
    for args in [("init",), ("add", "."), ("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture"),
                 ("remote", "add", "origin", "https://github.com/example/test")]:
        subprocess.run(["git", "-C", str(source), *args], check=True, capture_output=True)
    return InstallReceipt(skill_path=".", carousel_compatible=compatible, compatibility_note="Local test fixture")


def main():
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        url = f"sqlite:///{(root / 'test.db').as_posix()}"
        upgrade_database(url)
        db = Database(url)
        catalog = ProducerSkillCatalog(skills_root_for(db))
        workspace = root / "fixture"
        receipt = fixture(workspace)
        inspected = _inspect_checkout(workspace / "source", None)
        assert inspected.carousel_compatible and inspected.skill_path == "."
        record = catalog.register(workspace, "https://github.com/example/test", receipt, role="production")
        skill_id = record["id"]
        assert catalog.resolve(skill_id).is_dir()
        producer = CodexProducer.from_defaults()
        prompt = producer._build_prompt("creator", "series", "topic", "test", skill_name=skill_id, skills_root=catalog.root)
        assert "SPECIAL_SKILL_MARKER" in prompt and str(catalog.resolve(skill_id)) in prompt
        skill_file = catalog.resolve(skill_id) / "SKILL.md"
        original = skill_file.read_text(encoding="utf-8")
        skill_file.write_text(original.replace("Test image carousel", "Edited local description")
                              + "LOCAL_EDIT_MARKER\n", encoding="utf-8")
        current = catalog.describe(str(skill_file.parent))
        assert current["id"] == skill_id and current["description"] == "Edited local description"
        assert current["digest"] != current["source_digest"]
        assert "LOCAL_EDIT_MARKER" in producer._build_prompt(
            "creator", "series", "topic", "test", skill_name=skill_id, skills_root=catalog.root)
        assert "LOCAL_EDIT_MARKER" not in (catalog.root / "versions" / skill_id / "SKILL.md").read_text()
        for bad in ["../../.env", "https://evil.test/x/y", "https://github.com/x/y?token=secret"]:
            try:
                github_url(bad)
                raise AssertionError("bad URL accepted")
            except ValueError:
                pass
        runs = ContentRunService(db, output_root=root / "outputs", production_protocol="legacy")
        app = create_app(database=db, run_service=runs)
        with TestClient(app) as client:
            creator = client.post("/api/creators", json={"display_name": "隔离测试"}).json()
            series = client.post(f"/api/creators/{creator['id']}/series", json={"name": "技能测试"}).json()
            with db.session() as session:
                session.add(Topic(id="before", series_id=series["id"], title="before", source=TopicSource.MANUAL, position=1))
                session.add(Topic(id="after", series_id=series["id"], title="after", source=TopicSource.MANUAL, position=2))
            old = runs.create("before")
            endpoint = f"/api/series/{series['id']}/skill"
            body = {"skill_id": current["local_path"], "expected_skill_name": "knowledge-to-carousel"}
            assert client.post(endpoint, json=body).status_code == 200
            assert client.post(endpoint, json=body).status_code == 409
            new = runs.create("after")
            assert old.input_snapshot_json["skill_name"] == "knowledge-to-carousel"
            assert new.input_snapshot_json["skill_name"] == skill_id
            assert client.get(f"/api/series/{series['id']}").json()["skill_name"] == skill_id
            class InspectAndInterrupt:
                seen = []
                def produce_to(self, **request):
                    self.seen.append((request["skill_directory"] / "SKILL.md").read_text(encoding="utf-8"))
                    raise KeyboardInterrupt
            inspector = InspectAndInterrupt()
            runs.producer_factory = lambda: inspector
            skill_file.write_text(skill_file.read_text(encoding="utf-8") + "\nBEFORE_SINGLE_START", encoding="utf-8")
            for edit in (False, True):
                if edit:
                    skill_file.write_text(skill_file.read_text(encoding="utf-8") + "\nAFTER_SINGLE_START", encoding="utf-8")
                try:
                    runs.execute(new.id)
                except KeyboardInterrupt:
                    pass
            assert len(inspector.seen) == 2
            assert all("BEFORE_SINGLE_START" in text and "AFTER_SINGLE_START" not in text for text in inspector.seen)
            with db.session() as session:
                session.add(Topic(id="fresh", series_id=series["id"], title="fresh", source=TopicSource.MANUAL, position=3))
            fresh = runs.create("fresh")
            try:
                runs.execute(fresh.id)
            except KeyboardInterrupt:
                pass
            assert "AFTER_SINGLE_START" in inspector.seen[-1]
            assert client.post(endpoint, json={**body, "skill_id": "../../secret"}).status_code == 422
            assert len(client.get("/api/producer-skills").json()["items"]) == 2
            edited = skill_file.read_text(encoding="utf-8")
            skill_file.write_text(edited.replace("creatoros-output: social-content-pack.image-carousel\n", ""), encoding="utf-8")
            assert client.post(endpoint, json={**body, "expected_skill_name": skill_id}).status_code == 422
            skill_file.write_text(edited, encoding="utf-8")
            (catalog.resolve(skill_id) / "SKILL.md").write_text("tampered", encoding="utf-8")
            assert client.post(endpoint, json={**body, "expected_skill_name": skill_id}).status_code == 422
        db.close()
        class LocalInstaller:
            calls = 0
            def install(self, url, directory, cancel):
                self.calls += 1
                return fixture(directory)
        local = LocalInstaller()
        service = SkillInstallService(ProducerSkillCatalog(root / "jobs-test"), installer=local)
        job = service.submit("https://github.com/example/test")
        service.thread.join(10)
        assert service.get(job["id"])["status"] == "installed"
        assert service.submit("https://github.com/example/test")["id"] == job["id"]
        assert local.calls == 1
        # Failure injection is local only; it must not silently retry a paid installation.
        class BrokenInstaller:
            def install(self, *args):
                raise RuntimeError("local injected failure")
        broken = SkillInstallService(ProducerSkillCatalog(root / "failed-jobs"), installer=BrokenInstaller())
        failed = broken.submit("https://github.com/example/test")
        broken.thread.join(10)
        assert broken.submit("https://github.com/example/test")["attempt"] == 1
        retried = broken.submit("https://github.com/example/test", retry=True)
        broken.thread.join(10)
        assert retried["attempt"] == 2
        assert broken.get(failed["id"])["status"] == "failed"
        service.shutdown()
        broken.shutdown()
    print("producer_skills_smoke=passed binding=cas local_edit=visible path_alias=passed original=preserved invalid=blocked")


if __name__ == "__main__":
    main()
