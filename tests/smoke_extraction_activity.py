"""Public activity and real HTTP/files; SDK doubles only inject timing/privacy faults."""
import asyncio
import json
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from openai_codex.generated import v2_all as sdk
from openai_codex.models import Notification, UnknownNotification

from creatoros.integrations.codex import CodexProducerError
from creatoros.integrations.extraction_activity import ExtractionActivity, MAX_TEXT
from creatoros.integrations.producer_skills import ProducerSkillCatalog, SkillInstallService, _write
from creatoros.integrations.production_progress import ProgressWriter, collect_observed_turn
from creatoros.integrations.skill_extraction import SkillExtractionService, extraction_failure, sdk_extract, extraction_timeout
from creatoros.runs import ContentRunService
from creatoros.storage import Database, upgrade_database
from creatoros.web import create_app
from tests.smoke_production_progress import FakeTurn, assistant_item, completed_item, notification, turn_completed


def main():
    with TemporaryDirectory(prefix="extraction-activity-") as temporary:
        root = Path(temporary)
        activity = ExtractionActivity(root)
        delta = lambda text: notification("item/agentMessage/delta", sdk.AgentMessageDeltaNotification,
            {"threadId": "thread-1", "turnId": "turn-1", "itemId": "message-1", "delta": text})
        tool = {"type": "commandExecution", "id": "tool-1", "command": "check draft",
                "cwd": str(root), "commandActions": [], "status": "completed", "exitCode": 1,
                "aggregatedOutput": "failed check\nAuthorization: Bearer sensitive-value\n" + "x" * 1800}
        events = [delta("检查"), delta("作品"), completed_item(assistant_item("message-1", "检查作品", "commentary")),
                  completed_item(tool), completed_item(tool),
                  completed_item(assistant_item("final", "完成", "final_answer")), turn_completed()]
        # Unknown/raw reasoning must not be serialized, even if it has plausible text.
        activity.observe(Notification("item/reasoning/textDelta", UnknownNotification({"delta": "PRIVATE_REASONING"})))
        activity.observe(Notification("rawResponseItem/completed", UnknownNotification({"text": "PRIVATE_RAW"})))
        turn = FakeTurn(events)
        result = asyncio.run(collect_observed_turn(turn, ProgressWriter(root, "production"), activity.observe))
        assert result.final_response == "完成" and turn.stream_calls == 1 and turn.run_calls == 0
        activity.finish("completed", "文件核验通过")
        index = json.loads((root / "public_events/index.json").read_text(encoding="utf-8"))
        assert len(index["items"]) == 4, "delta/completion and duplicate tools update the same event"
        assert index["items"][0]["text"] == "检查作品"
        assert index["items"][1]["status"] == "failed" and index["items"][1]["truncated"]
        text = "".join(p.read_text(encoding="utf-8") for p in (root / "public_events").glob("*.json"))
        assert all(secret not in text for secret in ("PRIVATE_REASONING", "PRIVATE_RAW", "sensitive-value"))
        with patch.dict(os.environ, {"TEST_API_KEY": "env-secret-key"}):
            activity.put("secret", "tool", "工具", "completed", "env-secret-key")
        assert "env-secret-key" not in (root / "public_events/5.json").read_text()
        activity.put("large", "message", "Codex", "completed", "z" * (MAX_TEXT + 9000))
        assert json.loads((root / "public_events/6.json").read_text())["truncated"]

        # Validate real HTTP isolation and read-only pagination/detail ownership.
        url = f"sqlite:///{(root / 'test.db').as_posix()}"
        upgrade_database(url)
        db = Database(url)
        catalog = ProducerSkillCatalog(root / "catalog")
        service = SkillExtractionService(catalog)
        app = create_app(database=db, run_service=ContentRunService(db, output_root=root / "outputs"),
                         skill_install_service=SkillInstallService(catalog), skill_extraction_service=service)
        with TestClient(app) as client:
            job_id = "a" * 64
            directory = service._path("jobs", job_id)
            directory.mkdir(parents=True)
            doc = {"id": job_id, "status": "failed", "mode": "mind", "skills": [], "uploads": [],
                   "error": "检查 Codex 登录/额度或草稿格式", "saved_skills": [], "digest": None}
            _write(directory / "job.json", doc)
            (directory / "error.txt").write_text("Codex SDK 请求超时。", encoding="utf-8")
            old_bytes = (directory / "job.json").read_bytes()
            recorded = ExtractionActivity(directory)
            recorded.put("public", "message", "Codex", "completed", "正文" * 1200)
            recorded.put("tool", "tool", "校验", "failed", "有实际错误")
            base = f"/api/skill-extractions/{job_id}"
            projected = client.get(base).json()
            assert projected["error_type"] == "codex_timeout" and "不是登录或额度错误" in projected["error"]
            assert (directory / "job.json").read_bytes() == old_bytes
            assert "events" not in client.get("/api/skill-extractions").json()["items"][0]
            page = client.get(base + "/events?limit=1")
            assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
            assert page.json()["has_more"] and page.json()["items"][0]["id"] == 2
            older = client.get(base + "/events?limit=1&before_id=2").json()
            assert older["items"][0]["id"] == 1 and older["items"][0]["truncated"]
            detail = client.get(base + "/events/1?stream_id=.").json()
            assert len(detail["text"]) == 2400
            assert client.get(base + "/events/1?stream_id=../other").status_code == 404
            assert client.get(base + "/events/-1").status_code == 404
            assert client.get(base + "/events?limit=101").status_code == 422
            assert client.get(base + "/events?before_id=-1").status_code == 422
            assert client.get("/api/skill-extractions/" + "b" * 64 + "/events").status_code == 404
            # A linked detail cannot export a foreign file.
            outside = root / "foreign.json"
            outside.write_text('{"text":"FOREIGN"}', encoding="utf-8")
            (directory / "public_events/3.json").symlink_to(outside)
            assert client.get(base + "/events/3").status_code == 404
            assert client.get("/api/creators").json()["page"]["total"] == 0
        db.close()

        # Real deadline/cancellation machinery, controlled SDK generator that stalls after writing a draft.
        async def timing_case(cancel, during=False):
            folder = root / ("cancel-during" if during else "cancel" if cancel.is_set() else "timeout")
            folder.mkdir()
            interrupted = []
            closed = []

            class StalledTurn:
                id = "stalled-turn"
                def stream(self):
                    async def stream():
                        try:
                            yield completed_item(assistant_item("working", "正在核对草稿", "commentary"))
                            if during:
                                cancel.set()
                            await asyncio.sleep(2)
                        finally:
                            closed.append(True)
                    return stream()
                async def interrupt(self):
                    interrupted.append(True)

            async def start_turn(*args, **kwargs):
                return StalledTurn()

            @asynccontextmanager
            async def client(*args):
                async def start_thread(**kwargs):
                    draft = Path(kwargs["cwd"]) / "mind"
                    draft.mkdir()
                    (draft / "SKILL.md").write_text("partial draft", encoding="utf-8")
                    return SimpleNamespace(id="controlled-thread", turn=start_turn)
                yield SimpleNamespace(thread_start=start_thread)

            with patch("creatoros.integrations.skill_extraction._production_client", client), \
                 patch("creatoros.integrations.skill_extraction.extraction_timeout", return_value=0.08):
                try:
                    await sdk_extract(folder, [], "mind", "test", cancel, lambda value: None)
                    raise AssertionError("a partial file must not turn a timeout into success")
                except CodexProducerError as error:
                    assert error.error_type in {"codex_timeout", "codex_interrupted"}
            assert (folder / "draft/mind/SKILL.md").exists() if not cancel.is_set() else True
            if not cancel.is_set() or during:
                assert interrupted and closed
            assert json.loads((folder / "production_progress.json").read_text())["status"] in {"failed", "interrupted"}

        asyncio.run(timing_case(threading.Event()))
        cancel = threading.Event()
        cancel.set()
        asyncio.run(timing_case(cancel))
        asyncio.run(timing_case(threading.Event(), during=True))
        assert extraction_timeout() == 600
        with patch.dict(os.environ, {"CREATOROS_SKILL_EXTRACTION_TIMEOUT_SECONDS": "900"}):
            assert extraction_timeout() == 900
        with patch.dict(os.environ, {"CREATOROS_SKILL_EXTRACTION_TIMEOUT_SECONDS": "0"}):
            try:
                extraction_timeout()
                raise AssertionError("unbounded/invalid timeout accepted")
            except ValueError:
                pass
        assert extraction_failure(RuntimeError("usage limit reached"))[0] == "codex_usage_limit"
        assert "sensitive-value" not in extraction_failure(RuntimeError("API_KEY=sensitive-value"))[1]
    print("extraction_activity=passed public-stream/dedup/redaction/HTTP/pagination/path/old-cause/timeout/cancel")


if __name__ == "__main__":
    main()
