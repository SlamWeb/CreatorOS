from pathlib import Path
import hashlib
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
from creatoros.runs import ContentRunRepository
from creatoros.runs.artifacts import validate_artifact
from creatoros.storage import ContentRevision
from tests.studio_review_fixtures import make_fixture


with TemporaryDirectory() as temporary:
    root = Path(temporary)
    db, service, producer, app = make_fixture(root)
    run = service.create("review-1")
    service.execute(run.id)
    other = service.create("review-2")
    producer.count = 1
    service.execute(other.id)
    repository = ContentRunRepository(db)
    with TestClient(app) as client:
        base = f"/api/runs/{run.id}"
        before = len(repository.list_events(run.id))
        detail = client.get(base).json()
        first = detail["revisions"][0]
        assert len(first["cards"]) == 5 and first["review_digest"]
        assert str(root) not in str(detail)
        assert first["sources"][1]["url"] is None
        for card in first["cards"]:
            response = client.get(card["url"])
            assert response.status_code == 200 and response.headers["content-type"] == "image/png"
            assert response.headers["cache-control"] == "no-store"
        assert len(client.get(f"/api/runs/{other.id}").json()["revisions"][0]["cards"]) == 1
        assert len(repository.list_events(run.id)) == before and producer.calls == 2
        wrong_run = first["cards"][0]["url"].replace(run.id, other.id)
        assert client.get(wrong_run).status_code == 404
        assert client.get(first["cards"][0]["url"].replace("/cards/1?", "/cards/99?")).status_code == 404
        approval = {"revision_id": first["id"], "artifact_digest": first["review_digest"], "expected_version": detail["version"]}
        assert client.post(base + "/approve", json={**approval, "expected_version": 999}).status_code == 409
        assert client.post(base + "/approve", json={**approval, "artifact_digest": "0" * 64}).status_code == 409
        changed = client.post(base + "/revisions", json={"instruction": "用点餐来解释", "expected_version": detail["version"]})
        assert changed.status_code == 201 and changed.json()["status"] == "queued"
        assert producer.calls == 2 and changed.json()["revisions"][1]["attempts"] == []
        assert client.post(base + "/revisions", json={"instruction": "重复请求", "expected_version": detail["version"]}).status_code == 409
        producer.count = 6
        service.execute(run.id)
        current = client.get(base).json()
        second = current["revisions"][1]
        assert len(second["cards"]) == 6 and len(current["revisions"][0]["cards"]) == 5
        assert client.post(base + "/approve", json={**approval, "expected_version": current["version"]}).status_code == 409
        directory = Path(repository.get_revision(second["id"]).artifact_directory)
        image = directory / "images/01.png"
        original = image.read_bytes()
        expected = {"revision_id": second["id"], "artifact_digest": second["review_digest"], "expected_version": current["version"]}
        image.write_bytes(original + b"modified")
        assert client.get(second["cards"][0]["url"]).status_code == 409
        forged = second["cards"][0]["url"].split("&checksum=")[0] + "&checksum=" + hashlib.sha256(image.read_bytes()).hexdigest()
        assert client.get(forged).status_code == 409
        assert client.get(base).json()["revisions"][1]["artifact_error"]
        assert client.post(base + "/approve", json=expected).status_code == 409
        image.write_bytes(b"bad image")
        assert client.get(second["cards"][0]["url"]).status_code == 409
        assert client.post(base + "/approve", json=expected).status_code == 409
        image.unlink()
        assert client.get(base).json()["revisions"][1]["artifact_error"]
        assert client.post(base + "/approve", json=expected).status_code == 409
        image.write_bytes(original)
        manifest = directory / "social_content_pack.json"
        manifest_original = manifest.read_text(encoding="utf-8")
        manifest.write_text(manifest_original.replace("images/01.png", "../outside.png"), encoding="utf-8")
        assert client.get(second["cards"][0]["url"]).status_code == 409
        manifest.write_text(manifest_original, encoding="utf-8")
        # A symlink outside the package must fail even when it points to valid image bytes.
        outside = root / "outside.png"
        outside.write_bytes(original)
        image.unlink()
        image.symlink_to(outside)
        assert client.get(second["cards"][0]["url"]).status_code == 409
        try:
            validate_artifact(directory)
        except ValueError:
            pass
        else:
            raise AssertionError("symlink escape was accepted")
        image.unlink()
        image.write_bytes(original)
        external_manifest = root / "outside.json"
        external_manifest.write_text(manifest_original, encoding="utf-8")
        manifest.unlink()
        manifest.symlink_to(external_manifest)
        assert client.get(second["cards"][0]["url"]).status_code == 409
        manifest.unlink()
        manifest.write_text(manifest_original, encoding="utf-8")
        with db.session() as session:
            session.get(ContentRevision, second["id"]).artifact_directory = repository.get_revision(first["id"]).artifact_directory
        assert client.get(second["cards"][0]["url"]).status_code == 409
        with db.session() as session:
            session.get(ContentRevision, second["id"]).artifact_directory = str(directory)
            # Old saved validation remains usable without rewriting historical JSON.
            revision = session.get(ContentRevision, second["id"])
            revision.validation_json = {**revision.validation_json, "images": [
                {key: value for key, value in item.items() if key != "sha256"} for item in revision.validation_json["images"]]}
        assert client.get(second["cards"][0]["url"]).status_code == 200
        approved = client.post(base + "/approve", json=expected)
        assert approved.status_code == 200 and approved.json()["status"] == "approved", approved.text
        assert client.post(base + "/approve", json=expected).status_code == 409
        assert client.get(first["cards"][0]["url"]).status_code == 200
        assert producer.calls == 3
    db.close()
print("studio_artifacts_smoke=passed cards=1,5,6 version=digest=guarded missing=bad=escape=blocked history=retained approval=not_publishing")
