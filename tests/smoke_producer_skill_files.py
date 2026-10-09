"""Read-only HTTP browsing for registered Producer Skill working copies."""
from __future__ import annotations

import hashlib
import json
import shutil
from contextlib import ExitStack
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from PIL import Image

from creatoros.storage import ContentRepository, CreatorPlatform, Database, upgrade_database
from creatoros.web.app import create_app
from tests.agent_studio_support import serve


def snapshot_tree(directory: Path):
    return {
        path.relative_to(directory).as_posix(): (
            path.stat().st_size, path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
        )
        for path in directory.rglob("*")
    }


with TemporaryDirectory() as temporary, ExitStack() as cleanup:
    root = Path(temporary)
    database_path = root / "skills.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    upgrade_database(database_url)
    database = Database(database_url)
    cleanup.callback(database.close)
    ContentRepository(database).create_creator(creator_id="creator-files", display_name="Files",
                                               platform=CreatorPlatform.XIAOHONGSHU)
    app = create_app(database=database, chat_root=root / "sessions")
    catalog = app.state.skill_installs.catalog
    source = root / "source-skill"
    (source / "assets").mkdir(parents=True)
    (source / "scripts").mkdir()
    (source / "notes").mkdir()
    skill_text = "---\nname: file-browser-skill\ndescription: Browse registered skill files\n---\n" + ("长正文内容\n" * 900)
    (source / "SKILL.md").write_text(skill_text, encoding="utf-8")
    skill_text = (source / "SKILL.md").read_bytes().decode("utf-8")
    (source / "scripts" / "example.py").write_text("print('hello')\n", encoding="utf-8")
    (source / "scripts" / "example.js").write_text("throw new Error('SOURCE_ONLY');\n", encoding="utf-8")
    (source / "notes" / "example.html").write_text("<script>throw 'SOURCE_ONLY'</script>", encoding="utf-8")
    (source / "notes" / "readme.txt").write_text("UTF-8 预览：你好，世界。\n", encoding="utf-8")
    (source / "manual.pdf").write_bytes(b"not an actual PDF")
    (source / "assets" / "bad-image.png").write_bytes(b"not a raster image")
    (source / ".env").write_text("SHOULD_NOT_BE_LISTED=secret\n", encoding="utf-8")
    (source / ".git").mkdir()
    (source / ".git" / "config").write_text("secret\n", encoding="utf-8")
    Image.new("RGB", (3, 2), (35, 125, 220)).save(source / "assets" / "diagram.png")
    Image.new("RGB", (4, 3), (210, 80, 35)).save(source / "assets" / "character.png", format="JPEG")
    item = catalog.register_local(source, role="mind")
    skill_id = item["id"]
    working = catalog.root / "working" / skill_id
    (root / "foreign.txt").write_text("foreign", encoding="utf-8")
    with serve(app) as studio_url, httpx.Client(base_url=studio_url, timeout=5,
                                                 trust_env=False) as client:
        before_files = snapshot_tree(catalog.root)
        before_db = hashlib.sha256(database_path.read_bytes()).hexdigest()
        listing = client.get(f"/api/producer-skills/{skill_id}/files")
        assert listing.status_code == 200, listing.text
        body = listing.json()
        assert body["id"] == skill_id and body["name"] == "file-browser-skill"
        assert body["description"] == "Browse registered skill files" and body["role"] == "mind"
        files = {entry["path"]: entry for entry in body["files"]}
        assert files["SKILL.md"]["kind"] == "markdown" and files["SKILL.md"]["size"] > 4_000
        assert files["scripts/example.py"]["kind"] == "text"
        assert files["assets/diagram.png"]["kind"] == "image"
        assert files["assets/character.png"]["kind"] == "image"
        assert files["manual.pdf"]["kind"] == "unsupported"
        assert ".env" not in files and ".git/config" not in files

        full_skill = client.get(f"/api/producer-skills/{skill_id}/files/content",
                                params={"path": "SKILL.md"})
        assert full_skill.status_code == 200 and full_skill.json() == {
            "path": "SKILL.md", "kind": "markdown", "content": skill_text,
            "digest": body["digest"], "editable": True,
        }, (full_skill.status_code, full_skill.text[:500])
        script = client.get(f"/api/producer-skills/{skill_id}/files/content",
                            params={"path": "scripts/example.py"})
        assert script.status_code == 200 and script.json()["content"] == (
            working / "scripts" / "example.py").read_bytes().decode("utf-8")
        for source_path in ("scripts/example.js", "notes/example.html"):
            code = client.get(f"/api/producer-skills/{skill_id}/files/content", params={"path": source_path})
            assert code.status_code == 200 and code.headers["content-type"].startswith("application/json")
            assert code.json()["kind"] == "text" and "SOURCE_ONLY" in code.json()["content"]
        note = client.get(f"/api/producer-skills/{skill_id}/files/content",
                          params={"path": "notes/readme.txt"})
        assert note.status_code == 200 and note.json()["content"] == (
            working / "notes" / "readme.txt").read_bytes().decode("utf-8")
        image = client.get(f"/api/producer-skills/{skill_id}/files/content",
                           params={"path": "assets/diagram.png"})
        assert image.status_code == 200 and image.headers["content-type"] == "image/png"
        assert image.content == (working / "assets" / "diagram.png").read_bytes()
        with Image.open(BytesIO(image.content)) as opened:
            assert opened.format == "PNG" and opened.size == (3, 2)
        disguised_jpeg = client.get(f"/api/producer-skills/{skill_id}/files/content",
                                    params={"path": "assets/character.png"})
        assert disguised_jpeg.status_code == 200
        assert disguised_jpeg.headers["content-type"] == "image/jpeg"
        with Image.open(BytesIO(disguised_jpeg.content)) as opened:
            assert opened.format == "JPEG" and opened.size == (4, 3)
        bad_image = client.get(f"/api/producer-skills/{skill_id}/files/content",
                               params={"path": "assets/bad-image.png"})
        assert bad_image.status_code == 404

        unsupported = client.get(f"/api/producer-skills/{skill_id}/files/content",
                                 params={"path": "manual.pdf"})
        unsupported_message = unsupported.json().get("error", {}).get("message", unsupported.text)
        assert unsupported.status_code == 415 and "不支持预览" in unsupported_message
        for invalid_path in ("../foreign.txt", "assets/../../foreign.txt", ".env", ".git/config",
                             "assets//diagram.png", "C:/Windows/win.ini"):
            response = client.get(f"/api/producer-skills/{skill_id}/files/content",
                                  params={"path": invalid_path})
            assert response.status_code == 404, (invalid_path, response.status_code, response.text)
        assert client.get("/api/producer-skills/unregistered--0000000000000000/files").status_code == 404
        assert hashlib.sha256(database_path.read_bytes()).hexdigest() == before_db
        after_files = snapshot_tree(catalog.root)
        assert after_files == before_files, {
            key: (before_files.get(key), after_files.get(key))
            for key in before_files.keys() | after_files.keys()
            if before_files.get(key) != after_files.get(key)
        }

        oversized_text = working / "notes" / "oversized.txt"
        oversized_text.write_bytes(b"x" * (catalog.MAX_TEXT_BYTES + 1))
        too_large = client.get(f"/api/producer-skills/{skill_id}/files/content",
                               params={"path": "notes/oversized.txt"})
        assert too_large.status_code == 413
        oversized_text.unlink()

        saved_skill = (working / "SKILL.md").read_bytes()
        (working / "SKILL.md").unlink()
        assert client.get(f"/api/producer-skills/{skill_id}/files").status_code == 404
        (working / "SKILL.md").write_bytes(saved_skill)
        (working / "SKILL.md").write_bytes(b"invalid metadata")
        assert client.get(f"/api/producer-skills/{skill_id}/files").status_code == 404
        (working / "SKILL.md").write_bytes(saved_skill)

        saved_registry = (catalog.root / "registry" / f"{skill_id}.json").read_bytes()
        (catalog.root / "registry" / f"{skill_id}.json").write_text("{broken", encoding="utf-8")
        assert client.get(f"/api/producer-skills/{skill_id}/files").status_code == 404
        (catalog.root / "registry" / f"{skill_id}.json").write_bytes(saved_registry)

        missing = catalog.root / "working" / skill_id
        shutil.rmtree(missing)
        missing_response = client.get(f"/api/producer-skills/{skill_id}/files")
        assert missing_response.status_code == 404 and not missing.exists()
        (catalog.root / "working").mkdir(exist_ok=True)
        shutil.copytree(source, missing, ignore=shutil.ignore_patterns(".env", ".git"))

        external = root / "foreign.txt"
        symlink = missing / "foreign-link.txt"
        try:
            symlink.symlink_to(external)
        except (OSError, NotImplementedError):
            symlink = None
        if symlink is not None:
            assert client.get(f"/api/producer-skills/{skill_id}/files").status_code == 404
            symlink.unlink()

    database.close()

print("producer_skill_files_smoke=passed catalog=readonly files=bounded text=complete image=decoded_mime=actual unsupported=415 paths=restricted")
