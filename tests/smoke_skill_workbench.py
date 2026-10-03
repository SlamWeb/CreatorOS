"""Isolated workbench HTTP checks; the controlled producer creates one local PNG."""
from __future__ import annotations

import asyncio
import base64
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
from PIL import Image

from creatoros.content import CarouselCard, PublicationCopy, SocialContentPack
from creatoros.integrations.producer_skills import ProducerSkillCatalog, SkillInstallService, _write
from creatoros.integrations.skill_extraction import DraftSkill, ExtractionResult, SkillExtractionService
from creatoros.runs import ContentRunService
from creatoros.storage import Database, upgrade_database
from creatoros.web import create_app


def image_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (24, 32), "#e8e1d7").save(buffer, format="PNG")
    return buffer.getvalue()


def skill(name="workbench-skill", output_kind="image-carousel"):
    return DraftSkill(name=name, role="legacy_end_to_end", output_kind=output_kind,
        skill_md=f"---\nname: {name}\ndescription: Workbench smoke fixture\n---\n\n"
                 "Use the reference as guidance and adapt the content to a new topic.\n")


async def controlled(directory, images, mode, instruction, cancel, on_thread):
    on_thread("workbench-extraction-thread")
    kind = "text" if not images else "image-carousel"
    return ExtractionResult(note="受控夹具。", skills=[skill(output_kind=kind)])


async def revise(directory, images, mode, instruction, cancel, on_thread, current_skills):
    on_thread("workbench-revision-thread")
    if instruction == "wait":
        while not cancel.is_set():
            await asyncio.sleep(0.01)
        raise RuntimeError("cancelled revision")
    if instruction == "fail":
        raise RuntimeError("injected revision failure")
    old = current_skills[0]
    return ExtractionResult(note="受控改稿。", suggested_topic="沿用参考作品的内容试做，保留内容与呈现特点。",
        skills=[DraftSkill(name="revised-skill", role=old["role"], output_kind=old.get("output_kind", "image-carousel"),
                           skill_md=old["skill_md"].replace("Use the reference", "Adapt the reference"))])


class TrialProducer:
    def __init__(self):
        self.calls = []
        self.started = __import__("threading").Event()

    def produce_to(self, **request):
        self.calls.append(request)
        self.started.set()
        if request["topic_title"] == "cancel trial":
            while not request["cancel_event"].wait(0.01):
                pass
            raise RuntimeError("cancelled trial")
        directory = Path(request["directory"])
        directory.mkdir(parents=True)
        image_path = directory / "images" / "01-cover.png"
        image_path.parent.mkdir()
        Image.new("RGB", (24, 32), "#d9e5ee").save(image_path, format="PNG")
        pack = SocialContentPack(
            pack_id=request["pack_id"], creator_id=request["creator_id"],
            series_id=request["series_id"], topic_id=request["topic_id"],
            topic_title=request["topic_title"], skill_name="workbench-skill",
            generated_at="2026-10-03T12:00:00+08:00", content_summary="Controlled trial.",
            cards=[CarouselCard(order=1, kind="cover", headline="Trial", image_path="images/01-cover.png")],
            publish_copy=PublicationCopy(title="Trial", body="Controlled."),
        )
        (directory / "social_content_pack.json").write_text(pack.model_dump_json(), encoding="utf-8")
        return pack


def main():
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        url = f"sqlite:///{(root / 'test.db').as_posix()}"
        upgrade_database(url)
        database = Database(url)
        catalog = ProducerSkillCatalog(root / "skills")
        producer = TrialProducer()
        service = SkillExtractionService(catalog, controlled, reviser=revise, producer_factory=lambda: producer)
        app = create_app(database=database,
            run_service=ContentRunService(database, output_root=root / "output"),
            skill_install_service=SkillInstallService(catalog), skill_extraction_service=service)
        with TestClient(app) as client:
            base = "/api/skill-extractions"
            raw = image_bytes()
            uploaded = client.post(base + "/uploads", json={
                "name": "reference.png", "data_base64": base64.b64encode(raw).decode()})
            assert uploaded.status_code == 201, uploaded.text
            upload_id = uploaded.json()["id"]

            # Omitted mode means single; files stay in the extraction's scoped draft workspace.
            started = client.post(base, json={"request_id": "workbench-image", "upload_ids": [upload_id]})
            assert started.status_code == 202, started.text
            job_id = started.json()["id"]
            service.worker.join(5)
            job = client.get(f"{base}/{job_id}").json()
            assert job["status"] == "ready" and job["mode"] == "single", job
            assert job["source_text"] == ""
            assert job["revision"] >= 1 and job["operation"] is None
            assert job["suggested_topic"] == "沿用参考作品的内容试做，保留内容与呈现特点。"
            assert any(item["role"] == "legacy_end_to_end" and item["path"] == "SKILL.md" for item in job["files"])
            skill_url = next(item["url"] for item in job["files"] if item["path"] == "SKILL.md")
            assert "name: workbench-skill" in client.get(skill_url).text
            asset = next(item for item in job["files"] if item["kind"] == "image")
            assert client.get(asset["url"]).content == raw
            escaped = client.get(f"{base}/{job_id}/files", params={"role": "legacy_end_to_end", "path": "../job.json"})
            assert escaped.status_code in {404, 409, 422}, escaped.text

            old_digest = job["digest"]
            changed = client.post(f"{base}/{job_id}/draft", json={
                "expected_digest": old_digest,
                "skills": [{**skill("renamed-skill").model_dump(), "role": "legacy_end_to_end"}],
            })
            assert changed.status_code == 200, changed.text
            edited = changed.json()
            assert edited["skills"][0]["name"] == "renamed-skill"
            assert edited["digest"] != old_digest
            assert client.post(f"{base}/{job_id}/save", json={"expected_digest": old_digest}).status_code == 409

            # A failed revision leaves the last valid draft and its digest intact.
            failed = client.post(f"{base}/{job_id}/revise", json={
                "request_id": "failed-revision", "expected_digest": edited["digest"], "instruction": "fail"})
            assert failed.status_code == 202, failed.text
            service.worker.join(5)
            after_failure = client.get(f"{base}/{job_id}").json()
            assert after_failure["operation"] is None
            assert after_failure["digest"] == edited["digest"]
            assert after_failure["skills"] == edited["skills"]
            assert "原草稿保留" in after_failure["error"]

            cancelled_revision = client.post(f"{base}/{job_id}/revise", json={
                "request_id": "cancel-revision", "expected_digest": edited["digest"], "instruction": "wait"})
            assert cancelled_revision.status_code == 202
            assert client.post(f"{base}/{job_id}/cancel", json={}).status_code == 200
            service.worker.join(5)
            after_cancel = client.get(f"{base}/{job_id}").json()
            assert after_cancel["operation"] is None and after_cancel["digest"] == edited["digest"]
            assert "原草稿保留" in after_cancel["error"]

            trial = client.post(f"{base}/{job_id}/trials", json={
                "request_id": "trial-once", "expected_digest": edited["digest"], "topic": "沿用样例主题试做"})
            assert trial.status_code == 202, trial.text
            trial_id = trial.json()["trials"][-1]["id"]
            duplicate = client.post(f"{base}/{job_id}/trials", json={
                "request_id": "trial-once", "expected_digest": edited["digest"], "topic": "沿用样例主题试做"})
            assert duplicate.status_code == 202, duplicate.text
            conflict = client.post(f"{base}/{job_id}/trials", json={
                "request_id": "trial-once", "expected_digest": edited["digest"], "topic": "changed topic"})
            assert conflict.status_code == 409
            service.worker.join(5)
            done = client.get(f"{base}/{job_id}").json()
            trial_result = next(item for item in done["trials"] if item["id"] == trial_id)
            assert trial_result["status"] == "completed", trial_result
            assert trial_result["digest"] == edited["digest"]
            assert len(producer.calls) == 1
            card = client.get(f"{base}/{job_id}/trials/{trial_id}/cards/1")
            assert card.status_code == 200 and card.headers["content-type"].startswith("image/")
            assert client.get("/api/creators").json()["page"]["total"] == 0
            assert len(catalog.list()) == 1  # built-in only; a trial is not a formal catalog write

            # Trials freeze their Skill snapshot; later edits preserve and label the old result.
            frozen_skill = service.root / "jobs" / job_id / "trials" / trial_id / "skill-snapshot/skills/legacy_end_to_end/SKILL.md"
            frozen_text = frozen_skill.read_text(encoding="utf-8")
            newer = client.post(f"{base}/{job_id}/draft", json={
                "expected_digest": edited["digest"],
                "skills": [{**skill("later-version").model_dump(), "role": "legacy_end_to_end"}],
            })
            assert newer.status_code == 200, newer.text
            current = newer.json()
            assert current["digest"] != trial_result["digest"]
            old_trial = next(item for item in current["trials"] if item["id"] == trial_id)
            assert old_trial["digest"] == edited["digest"]
            assert frozen_skill.read_text(encoding="utf-8") == frozen_text
            assert client.post(f"{base}/{job_id}/save", json={"expected_digest": old_trial["digest"]}).status_code == 409

            cancelled_trial = client.post(f"{base}/{job_id}/trials", json={
                "request_id": "cancel-trial", "expected_digest": current["digest"], "topic": "cancel trial"})
            assert cancelled_trial.status_code == 202
            assert producer.started.wait(5)
            assert client.post(f"{base}/{job_id}/cancel", json={}).status_code == 200
            service.worker.join(5)
            after_trial_cancel = client.get(f"{base}/{job_id}").json()
            interrupted = next(item for item in after_trial_cancel["trials"] if item["topic"] == "cancel trial")
            assert interrupted["status"] == "interrupted"
            assert after_trial_cancel["operation"] is None
            assert len(catalog.list()) == 1

            # Startup marks a previously running operation interrupted without restarting production.
            job_path = service.root / "jobs" / job_id / "job.json"
            stale = json.loads(job_path.read_text(encoding="utf-8"))
            old_trial = next(item for item in stale["trials"] if item["id"] == trial_id)
            old_trial["status"] = "running"
            stale["operation"] = "trial"
            _write(job_path, stale)
            restarted = SkillExtractionService(catalog, controlled, reviser=revise, producer_factory=lambda: producer)
            restarted.start()
            recovered = restarted.get(job_id)
            assert recovered["operation"] is None
            assert next(item for item in recovered["trials"] if item["id"] == trial_id)["status"] == "interrupted"
            assert len(producer.calls) == 2  # canceled trial only; restart never executes it again
            restarted.shutdown()

            # Source text alone is accepted and remains a text-only target without image claims.
            text_job = client.post(base, json={"request_id": "workbench-text", "source_text": "A short sample."})
            assert text_job.status_code == 202, text_job.text
            service.worker.join(5)
            text_result = client.get(f"{base}/{text_job.json()['id']}").json()
            assert text_result["source_text"] == "A short sample."
            assert text_result["skills"][0]["output_kind"] == "text"
            assert "creatoros-output:" not in text_result["skills"][0]["skill_md"]
            assert not any(file["kind"] == "image" for file in text_result["files"])
            assert len(catalog.list()) == 1
        service.shutdown()
        database.close()
    print("skill_workbench_smoke=passed edit/revise/trial/assets/text/isolation/idempotency/cancel/restart")


if __name__ == "__main__":
    main()
