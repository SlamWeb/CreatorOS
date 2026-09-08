"""Isolated catalog, binding and production-input regression; no model calls."""
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from fastapi.testclient import TestClient

from creatoros.integrations.codex import CodexProducer, CodexUsage
from creatoros.integrations.producer_skills import (
    ProducerSkillCatalog, SkillInstallService, InstallReceipt, github_url, skills_root_for,
)
from creatoros.runs import ContentRunService
from creatoros.storage import Database, upgrade_database, Topic, TopicSource
from creatoros.web import create_app


def fixture(workspace, compatible=True):
    source = workspace / "source"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: test-carousel\ndescription: Test image carousel\n---\nSPECIAL_SKILL_MARKER\n", encoding="utf-8")
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
        record = catalog.register(workspace, "https://github.com/example/test", receipt)
        skill_id = record["id"]
        assert catalog.resolve(skill_id).is_dir()
        producer = CodexProducer.from_defaults()
        prompt = producer._build_prompt("creator", "series", "topic", "test", skill_name=skill_id, skills_root=catalog.root)
        assert "SPECIAL_SKILL_MARKER" in prompt and str(catalog.resolve(skill_id)) in prompt
        for bad in ["../../.env", "https://evil.test/x/y", "https://github.com/x/y?token=secret"]:
            try:
                github_url(bad)
                raise AssertionError("bad URL accepted")
            except ValueError:
                pass
        runs = ContentRunService(db, output_root=root / "outputs")
        app = create_app(database=db, run_service=runs)
        with TestClient(app) as client:
            creator = client.post("/api/creators", json={"display_name": "隔离测试"}).json()
            series = client.post(f"/api/creators/{creator['id']}/series", json={"name": "技能测试"}).json()
            with db.session() as session:
                session.add(Topic(id="before", series_id=series["id"], title="before", source=TopicSource.MANUAL, position=1))
                session.add(Topic(id="after", series_id=series["id"], title="after", source=TopicSource.MANUAL, position=2))
            old = runs.create("before")
            endpoint = f"/api/series/{series['id']}/skill"
            body = {"skill_id": skill_id, "expected_skill_name": "knowledge-to-carousel"}
            assert client.post(endpoint, json=body).status_code == 200
            assert client.post(endpoint, json=body).status_code == 409
            new = runs.create("after")
            assert old.input_snapshot_json["skill_name"] == "knowledge-to-carousel"
            assert new.input_snapshot_json["skill_name"] == skill_id
            assert client.get(f"/api/series/{series['id']}").json()["skill_name"] == skill_id
            assert client.post(endpoint, json={**body, "skill_id": "../../secret"}).status_code == 422
            assert len(client.get("/api/producer-skills").json()["items"]) == 2
            registered = catalog.root / "registry" / f"{skill_id}.json"
            registered.write_text(json.dumps({**record, "carousel_compatible": False}), encoding="utf-8")
            assert client.post(endpoint, json={**body, "expected_skill_name": skill_id}).status_code == 422
            registered.write_text(json.dumps(record), encoding="utf-8")
            (catalog.resolve(skill_id) / "SKILL.md").write_text("tampered", encoding="utf-8")
            assert client.post(endpoint, json={**body, "expected_skill_name": skill_id}).status_code == 422
        db.close()
        class LocalInstaller:
            calls = 0
            def install(self, url, directory, cancel):
                self.calls += 1
                assert (directory / ".git").is_dir()
                return SimpleNamespace(receipt=fixture(directory), thread_id="test", usage=CodexUsage())
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
    print("producer_skills_smoke=passed binding=cas snapshot=stable prompt=dynamic tamper=blocked")


if __name__ == "__main__":
    main()
