from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from threading import Event, Thread, current_thread
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx

from creatoros.context import RuntimeContext
from creatoros.integrations import producer_skills as producer_skill_module
from creatoros.storage import ContentRepository, CreatorPlatform, Database, Series, upgrade_database
from creatoros.tools.definitions import tool_registry
from creatoros.tools.studio import update_producer_skill_file
from creatoros.web.app import create_app
from tests.agent_studio_support import serve


with TemporaryDirectory() as temporary:
    root = Path(temporary)
    database_url = f"sqlite:///{(root / 'skills.db').as_posix()}"
    upgrade_database(database_url)
    database = Database(database_url)
    repository = ContentRepository(database)
    repository.create_creator(creator_id="creator-a", display_name="A",
                              platform=CreatorPlatform.XIAOHONGSHU)
    repository.create_creator(creator_id="creator-b", display_name="B",
                              platform=CreatorPlatform.XIAOHONGSHU)
    app = create_app(database=database, chat_root=root / "sessions")
    catalog = app.state.skill_installs.catalog
    source = root / "source-skill"
    source.mkdir()
    original_text = "---\nname: account-skill\ndescription: Original description\n---\nOriginal body\n"
    (source / "SKILL.md").write_text(original_text, encoding="utf-8")
    (source / "notes.md").write_text("Original notes\n", encoding="utf-8")
    registered = catalog.register_local(source, role="production")
    skill_id = registered["id"]
    working = catalog.root / "working" / skill_id
    original = catalog.root / "versions" / skill_id
    historical = root / "historical-run-snapshot"
    shutil.copytree(working, historical)
    original_digest = catalog.describe(skill_id)["source_digest"]

    with database.session() as session:
        session.add(Series(id="series-a", creator_id="creator-a", name="A series",
                           description="", audience="", skill_name=skill_id,
                           mind_skill_id=None, production_skill_id=None))
        session.add(Series(id="series-b", creator_id="creator-b", name="B series",
                           description="", audience="", skill_name="other-skill",
                           mind_skill_id=None, production_skill_id=None))

    with serve(app) as studio_url, httpx.Client(base_url=studio_url, timeout=5, trust_env=False) as client:
        session_a = app.state.chat.create("creator-a")["id"]
        session_b = app.state.chat.create("creator-b")["id"]
        headers_a = {"x-creatoros-agent-session": session_a}
        headers_b = {"x-creatoros-agent-session": session_b}

        files = client.get(f"/api/producer-skills/{skill_id}/files", headers=headers_a)
        assert files.status_code == 200, files.text
        initial_digest = files.json()["digest"]
        assert {entry["path"] for entry in files.json()["files"]} >= {"SKILL.md", "notes.md"}
        read = client.get(f"/api/producer-skills/{skill_id}/files/content",
                          params={"path": "SKILL.md"}, headers=headers_a)
        assert read.status_code == 200 and read.json()["digest"] == initial_digest

        changed = original_text.replace("Original description", "Updated description").replace(
            "Original body", "Updated body")
        context = RuntimeContext(project_root=root, studio_url=studio_url,
                                 agent_session_id=session_a)
        result = json.loads(update_producer_skill_file(
            skill_id, "SKILL.md", changed, initial_digest, context=context).content)
        assert result["description"] == "Updated description"
        assert catalog.describe(skill_id)["description"] == "Updated description"
        assert original.joinpath("SKILL.md").read_text(encoding="utf-8") == original_text
        assert historical.joinpath("SKILL.md").read_text(encoding="utf-8") == original_text
        assert catalog.describe(skill_id)["source_digest"] == original_digest

        # Pause a reader after it captured the old bytes but before it hashes the
        # directory. A concurrent editor must wait, so the returned pair matches.
        read_ready, release_read = Event(), Event()
        update_started, update_done = Event(), Event()
        read_result, update_result, thread_errors = {}, {}, []
        original_digest_fn = producer_skill_module._digest

        def paused_digest(directory):
            if current_thread().name == "skill-read-snapshot" and Path(directory).resolve() == working.resolve():
                read_ready.set()
                if not release_read.wait(5):
                    raise TimeoutError("test did not release paused Skill read")
            return original_digest_fn(directory)

        def read_snapshot():
            try:
                read_result.update(catalog.read_skill_file(skill_id, "notes.md"))
            except Exception as error:
                thread_errors.append(error)

        def concurrent_update():
            update_started.set()
            try:
                update_result.update(catalog.update_skill_file(
                    skill_id, "notes.md", "Concurrent notes\n", result["digest"]))
            except Exception as error:
                thread_errors.append(error)
            finally:
                update_done.set()

        with patch.object(producer_skill_module, "_digest", paused_digest):
            reader = Thread(target=read_snapshot, name="skill-read-snapshot")
            reader.start()
            assert read_ready.wait(5)
            writer = Thread(target=concurrent_update, name="skill-update-snapshot")
            writer.start()
            assert update_started.wait(5)
            assert not update_done.wait(0.1), "writer must wait until the read content+digest snapshot is complete"
            release_read.set()
            reader.join(5)
            writer.join(5)
        assert not reader.is_alive() and not writer.is_alive() and not thread_errors, thread_errors
        assert read_result["content"].replace("\r\n", "\n") == "Original notes\n"
        assert read_result["digest"] == result["digest"]
        assert update_result["content"] == "Concurrent notes\n"

        stale = client.put(f"/api/producer-skills/{skill_id}/files/content", headers=headers_a,
                           json={"path": "notes.md", "content": "lost update\n",
                                 "expected_digest": initial_digest})
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "skill_digest_conflict"
        assert stale.json()["error"]["current_digest"] == update_result["digest"]

        latest = update_result["digest"]
        before_invalid_bytes = (working / "SKILL.md").read_bytes()
        invalid_documents = [
            # A colon followed by a space is a YAML mapping delimiter unless quoted.
            "---\nname: account-skill\ndescription: Invalid: unquoted colon\n---\nBody\n",
            "---\nname: account-skill\ndescription:\n  - list item\n---\nBody\n",
            "---\nname: [account, skill]\ndescription: Valid text\n---\nBody\n",
            # The legacy metadata reader only supports single-line fields; reject rather than save a value it misreads.
            "---\nname: account-skill\ndescription: |\n  Multiline text\n---\nBody\n",
        ]
        for invalid_content in invalid_documents:
            invalid = client.put(f"/api/producer-skills/{skill_id}/files/content", headers=headers_a,
                                 json={"path": "SKILL.md", "content": invalid_content,
                                       "expected_digest": latest})
            assert invalid.status_code == 422, invalid.text
            assert catalog.describe(skill_id)["digest"] == latest
            assert (working / "SKILL.md").read_bytes() == before_invalid_bytes
        if os.name == "nt":
            invalid_case_alias = client.put(
                f"/api/producer-skills/{skill_id}/files/content", headers=headers_a,
                json={"path": "skill.md", "content": invalid_documents[0],
                      "expected_digest": latest})
            assert invalid_case_alias.status_code == 422
            assert catalog.describe(skill_id)["digest"] == latest
            assert (working / "SKILL.md").read_bytes() == before_invalid_bytes

        quoted_description = (
            "---\nname: account-skill\ndescription: \"Updated: quoted description\"\n---\n\n"
            "# Account Skill\n"
        )
        quoted = client.put(f"/api/producer-skills/{skill_id}/files/content", headers=headers_a,
                            json={"path": "SKILL.md", "content": quoted_description,
                                  "expected_digest": latest})
        assert quoted.status_code == 200, quoted.text
        assert quoted.json()["description"] == "Updated: quoted description"
        latest = quoted.json()["digest"]
        unsupported = client.put(f"/api/producer-skills/{skill_id}/files/content", headers=headers_a,
                                 json={"path": "missing.py", "content": "print(1)",
                                       "expected_digest": latest})
        assert unsupported.status_code == 422
        cross_account = client.put(f"/api/producer-skills/{skill_id}/files/content", headers=headers_b,
                                   json={"path": "notes.md", "content": "denied",
                                         "expected_digest": latest})
        assert cross_account.status_code == 403
        builtin = client.put("/api/producer-skills/knowledge-to-carousel/files/content", headers=headers_a,
                             json={"path": "SKILL.md", "content": changed,
                                   "expected_digest": latest})
        assert builtin.status_code in {403, 404}

    assert tool_registry["update_producer_skill_file"].to_schema()["function"]["name"] == "update_producer_skill_file"
    database.close()

print("producer_skill_edit_smoke=passed atomic=passed digest_cas=passed metadata=validated source_and_snapshot=preserved account_scope=bound_skill")
