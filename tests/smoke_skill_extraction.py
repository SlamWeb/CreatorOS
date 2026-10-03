"""Isolated HTTP/catalog tests. Controlled extractor injects faults; no paid calls."""
import asyncio
import base64
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from creatoros.integrations.producer_skills import ProducerSkillCatalog, SkillInstallService, _write
from creatoros.integrations.skill_extraction import (
    MODE_ROLES, DraftSkill, ExtractionResult, SkillExtractionService,
)
from creatoros.runs import ContentRunService
from creatoros.storage import Database, upgrade_database
from creatoros.web import create_app


def image_data():
    data = io.BytesIO()
    Image.new("RGB", (12, 12), "white").save(data, format="PNG")
    return data.getvalue()


async def controlled(directory, images, mode, instruction, cancel, on_thread):
    on_thread("isolated-extraction-thread")
    assert all(path.is_file() for path in images)
    if instruction == "wait":
        while not cancel.is_set():
            await asyncio.sleep(0.01)
        raise RuntimeError("cancelled")
    if instruction == "fail":
        raise RuntimeError("sensitive test diagnostic C:\\secret\\not-public")
    roles = MODE_ROLES[mode] if instruction != "wrong-role" else ["legacy_end_to_end"]
    return ExtractionResult(note="受控夹具，不代表提炼质量。", skills=[DraftSkill(
        name=f"reference-{role.replace('_', '-')}", role=role,
        skill_md=f"---\nname: reference-{role.replace('_', '-')}\ndescription: 从参考学习可复用方法\n---\n\n"
                 "先读 assets/reference-01.png。内容按主题确定，不固定页数。\n") for role in roles])


def main():
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        url = f"sqlite:///{(root / 'test.db').as_posix()}"
        upgrade_database(url)
        db = Database(url)
        catalog = ProducerSkillCatalog(root / "skills")
        service = SkillExtractionService(catalog, controlled)
        app = create_app(database=db, run_service=ContentRunService(db, output_root=root / "output"),
                         skill_install_service=SkillInstallService(catalog), skill_extraction_service=service)
        with TestClient(app) as client:
            base = "/api/skill-extractions"
            assert client.post(base + "/uploads", json={"name": "bad.png", "data_base64": "!!!"}).status_code == 422
            raw = image_data()
            upload = {"name": "../../reference.png", "data_base64": base64.b64encode(raw).decode()}
            first = client.post(base + "/uploads", json=upload)
            assert first.status_code == 201, first.text
            image = first.json()
            assert image["name"] == "reference.png"
            assert client.get(image["url"]).content == raw
            assert client.post(base + "/uploads", json=upload).json()["id"] == image["id"]
            assert client.get(base + "/uploads/not-an-id").status_code == 404
            assert client.post(base, json={"request_id": "empty", "upload_ids": []}).status_code == 422
            assert client.post(base, json={"request_id": "duplicate", "upload_ids": [image["id"]] * 2}).status_code == 422
            assert client.post(base, json={"request_id": "missing", "upload_ids": ["0" * 64]}).status_code == 404
            initial = len(catalog.list())
            for mode in MODE_ROLES:
                body = {"request_id": mode, "mode": mode, "upload_ids": [image["id"]], "instruction": ""}
                started = client.post(base, json=body)
                assert started.status_code == 202, started.text
                identifier = started.json()["id"]
                service.worker.join(5)
                job = client.get(base + "/" + identifier).json()
                assert job["status"] == "ready", job
                assert [s["role"] for s in job["skills"]] == MODE_ROLES[mode]
                assert len(catalog.list()) == initial  # preview has no catalog writes
                assert client.post(base, json=body).json()["id"] == identifier
                assert client.post(base, json={**body, "instruction": "changed"}).status_code == 409
                endpoint = base + "/" + identifier + "/save"
                assert client.post(endpoint, json={"expected_digest": "0" * 64}).status_code == 409
                saved = client.post(endpoint, json={"expected_digest": job["digest"]})
                assert saved.status_code == 200, saved.text
                result = saved.json()
                assert result["status"] == "saved"
                initial += len(MODE_ROLES[mode])
                assert len(catalog.list()) == initial
                assert client.post(endpoint, json={"expected_digest": job["digest"]}).json() == result
                for skill in result["saved_skills"]:
                    assert root in Path(skill["local_path"]).parents  # local isolated registry only
                    if skill["role"] == "mind":
                        assert not (Path(skill["local_path"]) / "assets").exists()
                    else:
                        assert (Path(skill["local_path"]) / "assets/reference-01.png").read_bytes() == raw
                    if skill["role"] != "mind":
                        assert skill["producible"]
                        assert catalog.resolve(skill["id"]).is_dir()

            # The service default is one complete Skill; browser/API callers may omit mode.
            default_job = service.submit("default-mode", [image["id"]])
            service.worker.join(5)
            default_job = service.get(default_job["id"])
            assert default_job["status"] == "ready", default_job
            assert default_job["mode"] == "single"
            assert [skill["role"] for skill in default_job["skills"]] == ["legacy_end_to_end"]

            for instruction, status in [("fail", "failed"), ("wrong-role", "failed"), ("wait", "interrupted")]:
                body = {"request_id": instruction, "mode": "pair", "upload_ids": [image["id"]], "instruction": instruction}
                job = client.post(base, json=body).json()
                if instruction == "wait":
                    assert client.post(base, json={**body, "request_id": "busy"}).status_code == 409
                    assert client.post(base + "/" + job["id"] + "/cancel", json={}).status_code == 200
                service.worker.join(5)
                final = client.get(base + "/" + job["id"]).json()
                assert final["status"] == status, final
                assert "secret" not in json.dumps(final)
                assert len(catalog.list()) == initial
            # No creator/series/run was created, nor can foreign origins submit paid jobs.
            assert client.get("/api/creators").json()["page"]["total"] == 0
            assert client.post(base, json=body, headers={"Origin": "https://evil.example"}).status_code == 403
            assert len(client.get(base).json()["items"]) == 8

            partial = service.submit("partial-register", [image["id"]], mode="pair")
            service.worker.join(5)
            partial = service.get(partial["id"])
            register = catalog.register_local
            def fail_second(path, **kwargs):
                if kwargs["role"] == "production":
                    raise ValueError("injected registry failure")
                return register(path, **kwargs)
            catalog.register_local = fail_second
            try:
                service.save(partial["id"], partial["digest"])
                raise AssertionError("injected failure not raised")
            except ValueError:
                pass
            finally:
                catalog.register_local = register
            assert service.get(partial["id"])["status"] == "ready"
            service.save(partial["id"], partial["digest"])
            assert len(catalog.list()) == initial + 2  # no duplicate first Skill on replay

            job = service.submit("tamper", [image["id"]], mode="pair")
            service.worker.join(5)
            ready = service.get(job["id"])
            role = ready["skills"][0]["role"]
            (service._draft_root(ready) / role / "SKILL.md").write_text("changed", encoding="utf-8")
            assert client.post(base + "/" + job["id"] + "/save", json={"expected_digest": ready["digest"]}).status_code == 409
            # Simulate a process death: restart reports interrupted, never starts a model.
            path = service.root / "jobs" / job["id"] / "job.json"
            stale = json.loads(path.read_text(encoding="utf-8"))
            _write(path, {**stale, "status": "running"})
            restarted = SkillExtractionService(catalog, controlled)
            restarted.start()
            assert restarted.get(job["id"])["status"] == "interrupted"
            assert restarted.worker is None
            restarted.shutdown()
        # A shutdown failure must not strand the executor/other managed services.
        app = create_app(database=db, run_service=ContentRunService(db, output_root=root / "output"),
                         skill_install_service=SkillInstallService(catalog), skill_extraction_service=service)
        with patch.object(service, "shutdown", side_effect=RuntimeError("injected shutdown timeout")), \
             patch.object(app.state.topic_research, "shutdown", wraps=app.state.topic_research.shutdown) as research_stop, \
             patch.object(app.state.skill_installs, "shutdown", wraps=app.state.skill_installs.shutdown) as install_stop, \
             patch.object(app.state.executor, "shutdown", wraps=app.state.executor.shutdown) as executor_stop:
            try:
                with TestClient(app):
                    pass
                raise AssertionError("shutdown failure was swallowed")
            except RuntimeError as error:
                assert "injected shutdown timeout" in str(error)
            assert research_stop.called and install_stop.called and executor_stop.called
        db.close()
    print("skill_extraction=passed modes=4 default-single/upload/idempotency/save/assets/failure/cancel/restart/tamper/http")


if __name__ == "__main__":
    main()
