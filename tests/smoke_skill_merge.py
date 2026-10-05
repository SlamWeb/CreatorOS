"""Isolated HTTP/SQLite checks for native Skill fusion; no Codex SDK calls."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from creatoros.integrations.producer_skills import ProducerSkillCatalog, SkillInstallService, _digest
from creatoros.integrations.skill_merge import prompt_context
from creatoros.integrations.skill_extraction import DraftSkill, ExtractionResult, SkillExtractionService
from creatoros.runs import ContentRunService
from creatoros.storage import Database, upgrade_database
from creatoros.web import create_app


def make_skill(directory: Path, name: str, role: str, body: str, asset_name: str):
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} merge fixture\n---\n\n{body}\n", encoding="utf-8")
    (directory / "assets").mkdir()
    (directory / "assets" / asset_name).write_bytes((name + "-asset").encode())
    (directory / "scripts").mkdir()
    (directory / "scripts" / f"{name}.py").write_text("print('safe fixture')\n", encoding="utf-8")
    (directory / ".env").write_text("DO_NOT_COPY=fixture-secret\n", encoding="utf-8")
    return directory


def fixture_extractor(calls, outside):
    async def extract(directory, images, mode, instruction, cancel, on_thread):
        calls.append({"directory": Path(directory), "images": images, "mode": mode,
                      "instruction": instruction})
        context = json.loads((Path(directory) / "merge_context.json").read_text(encoding="utf-8"))
        assert mode == "single" and images == []
        assert len(context["sources"]) == 2
        for source in context["sources"]:
            assert (Path(directory) / "draft" / "_source_inputs" / source["directory"] / "SOURCE-SKILL.md").is_file()
        output = Path(directory) / "draft" / "skill"
        output.mkdir(parents=True)
        if instruction == "symlink-parent":
            (output / "assets").mkdir()
            (output / "assets" / "fusion-sources").symlink_to(outside, target_is_directory=True)
        document = output / "SKILL.md"
        document.write_text(
            "---\nname: fused-skill\ndescription: Fusion draft fixture\n---\n\n"
            "Follow the combined workflow and adapt it to the new subject.\n", encoding="utf-8")
        result = ExtractionResult(note="冲突与待确认项需要由用户检查。", skills=[DraftSkill(
            name="fused-skill", role="legacy_end_to_end", output_kind=context["output_kind"],
            skill_md=document.read_text(encoding="utf-8"))])
        result._directories = {"legacy_end_to_end": output}
        return result
    return extract


async def fixture_reviser(directory, images, mode, instruction, cancel, on_thread, current_skills):
    directory = Path(directory)
    context = json.loads((directory / "merge_context.json").read_text(encoding="utf-8"))
    assert mode == "single" and images == [] and current_skills[0]["name"] == "fused-skill"
    for source in context["sources"]:
        assert (directory / "draft" / "_source_inputs" / source["directory"] / "SOURCE-SKILL.md").is_file()
    output = directory / "draft" / "skill"
    resources = output / "assets" / "fusion-sources"
    next(resources.rglob("*.py")).unlink()
    document = output / "SKILL.md"
    document.write_text(document.read_text(encoding="utf-8") + "\nRevision preserved the source bundle.\n", encoding="utf-8")
    result = ExtractionResult(note="改稿后的融合草稿。", skills=[DraftSkill(
        name="fused-skill", role="legacy_end_to_end", output_kind="image-carousel",
        skill_md=document.read_text(encoding="utf-8"))])
    result._directories = {"legacy_end_to_end": output}
    return result


def main():
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        database_url = f"sqlite:///{(root / 'test.db').as_posix()}"
        upgrade_database(database_url)
        database = Database(database_url)
        catalog = ProducerSkillCatalog(root / "producer-skills")
        first_dir = make_skill(root / "source-one", "source-one", "mind",
                               "Explain how to organize the subject.", "notes.md")
        second_dir = make_skill(root / "source-two", "source-two", "legacy_end_to_end",
                                "Make a visual story about the subject.", "reference.txt")
        second_skill = second_dir / "SKILL.md"
        second_skill.write_text(second_skill.read_text(encoding="utf-8").replace(
            "description: source-two merge fixture",
            "description: source-two merge fixture\ncreatoros-output: social-content-pack.image-carousel"),
            encoding="utf-8")
        first = catalog.register_local(first_dir, role="mind")
        second = catalog.register_local(second_dir, role="legacy_end_to_end")
        unsupported_dir = make_skill(root / "source-unsupported", "source-unsupported", "mind",
                                     "A source with an opaque attachment.", "unused.txt")
        (unsupported_dir / "reference.pdf").write_bytes(b"fixture PDF bytes")
        unsupported = catalog.register_local(unsupported_dir, role="mind")
        originals = {path: _digest(path) for path in (first_dir, second_dir)}
        catalog_before = {item["id"] for item in catalog.list()}
        calls = []
        outside = root / "outside"
        outside.mkdir()
        sentinel = outside / "sentinel.txt"
        sentinel.write_text("untouched", encoding="utf-8")
        outside_before = _digest(outside)
        service = SkillExtractionService(catalog, fixture_extractor(calls, outside), reviser=fixture_reviser)
        app = create_app(database=database,
            run_service=ContentRunService(database, output_root=root / "output"),
            skill_install_service=SkillInstallService(catalog), skill_extraction_service=service)
        try:
            with TestClient(app) as client:
                base = "/api/skill-extractions"
                body = {"request_id": "merge-two-skills", "skill_ids": [first["id"], second["id"]],
                        "instruction": "融合流程并说明来源之间不一致的规则。"}
                assert client.post(base + "/merge", json={**body, "skill_ids": [first["id"]] * 2}).status_code == 422
                assert client.post(base + "/merge", json={**body, "skill_ids": ["missing--" + "0" * 16, second["id"]]}).status_code == 422
                assert client.post(base + "/merge", json={**body, "request_id": "merge-unsupported",
                    "skill_ids": [first["id"], unsupported["id"]]}).status_code == 422
                started = client.post(base + "/merge", json=body)
                assert started.status_code == 202, started.text
                job_id = started.json()["id"]
                service.worker.join(5)
                job = client.get(f"{base}/{job_id}").json()
                assert job["status"] == "ready" and job["mode"] == "single"
                assert job["task_kind"] == "merge" and len(job["source_skills"]) == 2
                assert job["skills"][0]["output_kind"] == "image-carousel"
                assert {item["id"] for item in job["source_skills"]} == {first["id"], second["id"]}
                assert all(len(item["digest"]) == 64 for item in job["source_skills"])
                assert len(calls) == 1
                merge_prompt = prompt_context(service._path("jobs", job_id),
                    service._path("jobs", job_id) / "draft", body["instruction"])
                for source in job["source_skills"]:
                    namespace = next(item["directory"] for item in
                                     json.loads((service._path("jobs", job_id) / "merge_context.json").read_text(
                                         encoding="utf-8"))["sources"] if item["id"] == source["id"])
                    assert str((service._path("jobs", job_id) / "draft" / "_source_inputs" /
                                namespace / "SOURCE-SKILL.md").resolve()) in merge_prompt
                    assert f"assets/fusion-sources/{namespace}/" in merge_prompt
                assert "静默取舍" in merge_prompt and "从提供的作品" not in merge_prompt
                assert {item["id"] for item in catalog.list()} == catalog_before
                draft_root = service._draft_root(service._read(job_id))
                source_files = list((draft_root / "legacy_end_to_end" / "assets" / "fusion-sources").rglob("*"))
                assert any(path.name == "SOURCE-SKILL.md" for path in source_files)
                assert any(path.name == "notes.md" for path in source_files)
                assert any(path.suffix == ".py" for path in source_files)
                assert not any(path.name == ".env" for path in source_files)
                assert not any(path.name.casefold() == "skill.md" for path in source_files)
                listed_sources = [item for item in job["files"] if "fusion-sources" in item["path"]]
                assert listed_sources
                source_file_digests = {}
                for entry in listed_sources:
                    response = client.get(entry["url"])
                    assert response.status_code == 200, entry
                    source_file_digests[entry["path"]] = hashlib.sha256(response.content).hexdigest()

                # Identical retry returns this task; changed parameters conflict.
                assert client.post(base + "/merge", json=body).json()["id"] == job_id
                assert client.post(base + "/merge", json={**body, "instruction": "changed"}).status_code == 409

                # Revisions receive both the persisted context and fresh read-only model copies.
                revised = client.post(f"{base}/{job_id}/revise", json={
                    "request_id": "merge-revision-1", "expected_digest": job["digest"],
                    "instruction": "保留原有资源并补充冲突说明。"})
                assert revised.status_code == 202, revised.text
                service.worker.join(5)
                current = client.get(f"{base}/{job_id}").json()
                assert current["status"] == "ready" and current["digest"] != job["digest"]
                for entry in current["files"]:
                    if "fusion-sources" in entry["path"]:
                        response = client.get(entry["url"])
                        assert response.status_code == 200
                        assert hashlib.sha256(response.content).hexdigest() == source_file_digests[entry["path"]]
                assert {item["id"] for item in catalog.list()} == catalog_before

                # Only the explicit save endpoint registers the new fusion Skill.
                saved = client.post(f"{base}/{job_id}/save", json={"expected_digest": current["digest"]})
                assert saved.status_code == 200, saved.text
                assert saved.json()["status"] == "saved"
                assert len(catalog.list()) == len(catalog_before) + 1
                assert {path: _digest(path) for path in (first_dir, second_dir)} == originals
                failed = client.post(base + "/merge", json={"request_id": "merge-symlink-parent",
                    "skill_ids": [first["id"], second["id"]], "instruction": "symlink-parent"})
                assert failed.status_code == 202, failed.text
                service.worker.join(5)
                rejected = client.get(f"{base}/{failed.json()['id']}").json()
                assert rejected["status"] == "failed"
                assert _digest(outside) == outside_before and sentinel.read_text(encoding="utf-8") == "untouched"
                assert len(catalog.list()) == len(catalog_before) + 1
                assert client.get(base).status_code == 200
        finally:
            service.shutdown()
            database.close()
    print("skill_merge_smoke=passed http/sqlite/snapshot/idempotency/revise/assets/explicit-save/originals-unchanged")


if __name__ == "__main__":
    main()
