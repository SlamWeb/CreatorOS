"""Real isolated Studio HTTP/SQLite; no model, production or formal data writes."""
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import httpx

from creatoros.runs import ContentRunService
from creatoros.storage import ContentRepository, CreatorPlatform, Database, upgrade_database
from creatoros.web.app import create_app
from tests.agent_studio_support import serve
from tests.test_eval_store import file_snapshot, fixture_report, save_report


def main():
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        url = f"sqlite:///{(root / 'isolated.db').as_posix()}"
        upgrade_database(url)
        database = Database(url)
        try:
            repository = ContentRepository(database)
            repository.create_creator(creator_id="eval-test-owner", display_name="隔离账号",
                                      platform=CreatorPlatform.XIAOHONGSHU)
            runs = ContentRunService(database, output_root=root / "outputs")
            def no_model():
                raise AssertionError("GET/review must not invoke a model")
            app = create_app(database=database, run_service=runs, chat_root=root / "chat",
                             chat_provider_factory=no_model, eval_root=root / "eval")
            with serve(app) as base_url, httpx.Client(base_url=base_url, trust_env=False, timeout=15) as client:
                session_id = app.state.chat.create("eval-test-owner")["id"]
                scoped = {"x-creatoros-agent-session": session_id}
                before = file_snapshot(root)
                overview = client.get("/api/eval")
                assert overview.status_code == 200 and len(overview.json()["cases"]) == 12, overview.text
                cases = overview.json()["cases"]
                assert all(case["status"] == "not_run" and case["run_count"] == 0 for case in cases)
                definition = cases[0]["definition"]
                assert set(definition) == {"steps", "assertions", "manual_checks"}
                assert definition["steps"][0]["kind"] == "user" and definition["assertions"] and definition["manual_checks"]
                assert not (root / "eval").exists()
                assert client.get("/api/eval/runs").json()["items"] == []
                assert file_snapshot(root) == before, "empty GET persisted state"
                report = fixture_report()
                directory = save_report(root / "eval", report)
                failed = fixture_report(auto_status="failed")
                save_report(root / "eval", failed)
                bad = root / "eval" / uuid4().hex
                bad.mkdir()
                (bad / "report.json").write_text("{broken", encoding="utf-8")
                base = f"/api/eval/runs/{report['run_id']}"
                before = file_snapshot(root)
                detail = client.get(base)
                assert detail.status_code == 200 and detail.json()["status"] == "needs_review", detail.text
                assert detail.headers["cache-control"] == "no-store"
                listing = client.get("/api/eval/runs", params={"case_id": "E01"}).json()
                assert len(listing["items"]) == 2 and listing["errors"][0]["run_id"] == bad.name, listing
                assert client.get(f"/api/eval/runs/{bad.name}").status_code == 422
                assert client.get(f"/api/eval/runs/{uuid4().hex}").status_code == 404
                assert client.get("/api/eval/runs", params={"case_id": "E99"}).status_code == 404
                evidence = client.get(base + "/evidence", params={"name": "trace.json"})
                assert evidence.status_code == 200 and "credential-value" not in evidence.text, evidence.text
                assert "合成私有哨兵可检查" in evidence.text
                for name in ("../outside.json", "C:/secret.txt", "report.json", ".env", "unlisted.json"):
                    assert client.get(base + "/evidence", params={"name": name}).status_code == 404
                for path in ("/api/eval", "/api/eval/runs", base, base + "/evidence?name=trace.json"):
                    assert client.get(path, headers=scoped).status_code == 403, path
                assert file_snapshot(root) == before, "GET changed isolated DB or evidence"
                original = (directory / "report.json").read_bytes()
                payload = {"expected_digest": detail.json()["report_digest"], "decision": "passed", "note": "已核对隔离测试证据。"}
                assert client.post(base + "/review", json=payload, headers=scoped).status_code == 403
                assert client.post(base + "/review", json=payload, headers={"origin": "https://external.invalid"}).status_code == 403
                assert client.post(base + "/review", content="not-json").status_code == 415
                assert client.post(base + "/review", json={**payload, "note": "  "}).status_code == 422
                assert not (directory / "review.json").exists()
                reviewed = client.post(base + "/review", json=payload)
                assert reviewed.status_code == 200 and reviewed.json()["status"] == "passed", reviewed.text
                assert client.post(base + "/review", json=payload).status_code == 409
                failed_base = f"/api/eval/runs/{failed['run_id']}"
                failed_detail = client.get(failed_base).json()
                blocked = client.post(failed_base + "/review", json={**payload, "expected_digest": failed_detail["report_digest"]})
                assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "eval_review_blocked", blocked.text
                assert (directory / "report.json").read_bytes() == original
                assert app.state.evaluation.detail(report["run_id"])["review"] == reviewed.json()["review"]
                before = file_snapshot(root)
                assert client.get(base).json()["status"] == "passed"
                assert client.get("/api/eval").json()["cases"][0]["run_count"] == 2
                assert file_snapshot(root) == before, "reviewed GET wrote state"
            # A new service instance reads the saved review, without startup regrading.
            reloaded = create_app(database=database, run_service=runs, chat_root=root / "chat", eval_root=root / "eval")
            with serve(reloaded) as reloaded_url, httpx.Client(base_url=reloaded_url, trust_env=False, timeout=15) as client:
                before = file_snapshot(root)
                restored = client.get(base)
                assert restored.status_code == 200 and restored.json()["status"] == "passed", restored.text
                assert restored.json()["review"] == reviewed.json()["review"]
                assert file_snapshot(root) == before, "restart GET wrote persisted state"
        finally:
            database.close()
    print("smoke_eval_api passed: real loopback HTTP/SQLite, read-only GET, scope, evidence, review/CAS/reload")


if __name__ == "__main__":
    main()
