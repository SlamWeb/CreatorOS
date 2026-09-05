import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import time

from fastapi.testclient import TestClient

from creatoros.storage import Database, upgrade_database
from creatoros.web.app import create_app
from creatoros.web.launcher import StudioLaunchError, ensure_studio_build


with TemporaryDirectory() as temporary:
    root = Path(temporary)
    dist = root / "web" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<main>CreatorOS Studio</main>", encoding="utf-8")
    assets = dist / "assets"
    assets.mkdir()
    (assets / "studio.js").write_text("export const ready = true;", encoding="utf-8")

    database_url = f"sqlite:///{(root / 'studio.db').as_posix()}"
    upgrade_database(database_url)
    database = Database(database_url)
    app = create_app(database=database, studio_dist=dist)
    try:
        with TestClient(app) as client:
            assert client.get("/").text == "<main>CreatorOS Studio</main>"
            assert client.get("/creators/direct-refresh").text == "<main>CreatorOS Studio</main>"
            assert client.get("/runs/direct-refresh").text == "<main>CreatorOS Studio</main>"
            assert client.get("/assets/studio.js").headers["content-type"].startswith("text/javascript")
            assert client.get("/api/health").json()["status"] == "ok"
            local = client.post("/api/creators", headers={"Origin": "http://127.0.0.1:8877"}, json={"display_name": "Local"})
            assert local.status_code == 201
            remote = client.post("/api/creators", headers={"Origin": "https://external.example"}, json={"display_name": "Remote"})
            assert remote.status_code == 403
            assert client.get("/api/unknown").status_code == 404
    finally:
        database.close()

    project = root / "fresh"
    source = project / "web" / "src"
    output = project / "web" / "dist"
    source.mkdir(parents=True)
    output.mkdir(parents=True)
    (project / "web" / "package.json").write_text("{}", encoding="utf-8")
    source_index = project / "web" / "index.html"
    source_index.write_text("source", encoding="utf-8")
    (source / "main.tsx").write_text("// ready", encoding="utf-8")
    index = output / "index.html"
    index.write_text("ready", encoding="utf-8")
    future = time() + 2
    os.utime(index, (future, future))
    assert ensure_studio_build(project) == output
    newer = future + 2
    os.utime(source_index, (newer, newer))
    try:
        ensure_studio_build(project)
    except StudioLaunchError as error:
        assert "npm --prefix web ci" in str(error)
    else:
        raise AssertionError("missing frontend dependencies were accepted")

print("studio_delivery_smoke=passed same_origin=spa_refresh mime=module origin=loopback launcher=actionable")
