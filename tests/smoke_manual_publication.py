"""Manual publication is tested with isolated data; no platform post is created."""

from io import BytesIO
from pathlib import Path
from contextlib import contextmanager
import shutil
from uuid import uuid4
from zipfile import ZipFile

from fastapi.testclient import TestClient
from sqlalchemy import select
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from creatoros.storage import Base, ManualPublication, PublicationMetric, Topic, TopicStatus
from creatoros.config import PROJECT_ROOT
from tests.studio_review_fixtures import make_fixture


@contextmanager
def workspace_fixture():
    root = PROJECT_ROOT / "tmp" / f"manual-publication-{uuid4().hex}"
    root.mkdir()
    try:
        yield root
    finally:
        if root.resolve().parent == (PROJECT_ROOT / "tmp").resolve():
            try:
                shutil.rmtree(root)
            except PermissionError:
                pass  # Failed assertion may keep SQLite open; retain ignored fixture for diagnosis.


with workspace_fixture() as root:
    db, service, _producer, app = make_fixture(root)
    with db.engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    run = service.create("review-1")
    with TestClient(app) as client:
        base = f"/api/runs/{run.id}"
        empty = {"expected_version": run.version, "revision_id": "wrong", "artifact_digest": "0" * 64,
                 "post_url": "https://www.xiaohongshu.com/explore/test-note"}
        assert client.post(base + "/publication", json=empty).status_code == 409
        assert client.get(base + "/download").status_code == 409
        service.execute(run.id)
        pending = client.get(base).json()
        revision = pending["revisions"][0]
        payload = {"expected_version": pending["version"], "revision_id": revision["id"],
                   "artifact_digest": revision["review_digest"], "post_url": empty["post_url"]}
        assert client.post(base + "/publication", json=payload).status_code == 409
        assert client.post(base + "/approve", json={key: payload[key] for key in
               ("expected_version", "revision_id", "artifact_digest")}).status_code == 200
        approved = client.get(base).json()
        assert approved["publication"] is None
        payload["expected_version"] = approved["version"]
        assert client.post(base + "/publication", json={**payload, "post_url": "https://example.com/a"}).status_code == 422
        assert client.post(base + "/publication", json={**payload, "artifact_digest": "0" * 64}).status_code == 409
        archive_response = client.get(base + "/download")
        assert archive_response.status_code == 200
        with ZipFile(BytesIO(archive_response.content)) as archive:
            assert len([name for name in archive.namelist() if name.startswith("images/")]) == 5
            assert "publish_copy.txt" in archive.namelist()
        saved = client.post(base + "/publication", json=payload)
        assert saved.status_code == 200 and saved.json()["publication"]["post_url"] == payload["post_url"]
        assert client.post(base + "/publication", json=payload).status_code == 200
        assert client.post(base + "/publication", json={**payload, "post_url": "https://xhslink.com/other"}).status_code == 409
        metric_url = base + "/publication/metrics"
        assert client.post(metric_url, json={"request_id": "empty-123"}).status_code == 422
        assert client.post(metric_url, json={"request_id": "negative-123", "views": -1}).status_code == 422
        first = {"request_id": "day-1-123", "views": 100, "likes": 5}
        assert client.post(metric_url, json=first).status_code == 201
        assert client.post(metric_url, json=first).status_code == 201
        assert client.post(metric_url, json={**first, "views": 999}).status_code == 409
        assert client.post(metric_url, json={"request_id": "day-7-123", "views": 300, "likes": 15}).status_code == 201
        detail = client.get(base).json()
        assert [item["views"] for item in detail["publication"]["metrics"]] == [100, 300]
        assert detail["status"] == "approved"
        with db.session() as session:
            assert session.get(Topic, "review-1").status is TopicStatus.PUBLISHED
            assert len(list(session.scalars(select(ManualPublication)))) == 1
            assert len(list(session.scalars(select(PublicationMetric)))) == 2
    db.close()

print("manual_publication_smoke=passed isolated_db=passed zip=passed idempotent=passed")
