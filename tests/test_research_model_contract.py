"""Real scoped HTTP/SQLite read contract, not a real Codex research claim."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

import httpx
from sqlalchemy import inspect

from creatoros.agent.loop import build_model_context
from creatoros.ai.types import ToolCall
from creatoros.context import RuntimeContext
from creatoros.integrations.producer_skills import ProducerSkillCatalog, skills_root_for
from creatoros.integrations.topic_research import TopicResearchService, _write
from creatoros.session.request_trace import RequestSnapshots
from creatoros.tools import execute_tool_call
from creatoros.web.app import create_app
from creatoros.web.chat import ACCOUNT_TOOLS
from tests.agent_studio_support import serve
from tests.studio_review_fixtures import make_fixture


class NoResearchExecution:
    def preflight(self):
        raise AssertionError("This read regression must not submit research")

    def research(self, *args, **kwargs):
        raise AssertionError("This read regression must not invoke Codex")


class ResearchModelContractTests(unittest.TestCase):
    def test_actual_read_keeps_cardinality_state_url_and_original_trace(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            database, runs, producer, _ = make_fixture(root)
            try:
                research = TopicResearchService(database, ProducerSkillCatalog(skills_root_for(database)),
                                               researcher=NoResearchExecution())
                snapshot = research.snapshot("agent-notes")
                records = []
                for status, count in (("ready", 8), ("ready", 10), ("failed", 0), ("unknown", 0)):
                    record = {"id": uuid4().hex, "series_id": "agent-notes", "snapshot": snapshot,
                        "count": 10, "instructions": "常用常考", "status": status,
                        "created_at": "2026-10-11T00:00:00+00:00", "attempts": [],
                        "thread_id": "historical-fixture-not-a-real-sdk-thread", "note": status,
                        "candidates": [{"id": f"c{i}", "title": f"同义词 {i}", "angle": "真实完整切入点" * 30,
                            "rationale": "教学意义", "sources": [{"title": "词典", "url": f"https://example.com/{i}"}]}
                            for i in range(count)]}
                    if status == "failed":
                        record.update(error_type="codex_not_found", error="无法启动 Codex")
                    elif status == "unknown":
                        record.update(last_known_status="researching", error="执行状态暂时无法确认")
                    _write(research._path(record["id"]), record)
                    records.append(record)
                saved = {path: path.read_bytes() for path in research.root.rglob("*") if path.is_file()}
                def database_rows():
                    with database.engine.connect() as connection:
                        return {table: [tuple(row) for row in connection.exec_driver_sql(f'SELECT * FROM "{table}"')]
                                for table in inspect(database.engine).get_table_names()}
                before_database = database_rows()
                app = create_app(database=database, run_service=runs, topic_research_service=research,
                                 chat_root=root / "chats", chat_provider_factory=lambda: None)
                with serve(app) as url, httpx.Client(base_url=url, trust_env=False) as client:
                    sid = client.post("/api/agent/sessions", json={"creator_id": "review-lab"}).json()["id"]
                    context = RuntimeContext(root, studio_url=url, creator_id="review-lab", agent_session_id=sid,
                                             allowed_tools=ACCOUNT_TOOLS, archive_only_reads=True)
                    for record in records:
                        with self.subTest(status=record["status"], count=len(record["candidates"])):
                            call = ToolCall(uuid4().hex, "get_topic_research", json.dumps({"batch_id": record["id"]}))
                            result = execute_tool_call(call, context, model_requested=True)
                            raw, model = json.loads(result.content), json.loads(result.model_content)
                            self.assertEqual(result.is_error, record["status"] == "failed", result.content)
                            self.assertEqual(raw["count"], 10)
                            self.assertEqual(model["requested_count"], 10)
                            self.assertEqual(model["returned_count"], len(record["candidates"]))
                            self.assertEqual(model["candidates"], raw["candidates"])
                            self.assertEqual(model["status"], record["status"])
                            self.assertEqual(model["url"], f"/series/agent-notes?research={record['id']}")
                            self.assertEqual(model["url"], raw["url"])
                            self.assertIn("thread_id", raw)
                            self.assertNotIn("thread_id", model)
                            if record["status"] == "unknown":
                                self.assertEqual(model["last_known_status"], "researching")
                            trace = RequestSnapshots(root / "trace" / "messages.json")
                            rid = uuid4().hex
                            trace.begin(rid, "read-contract", build_model_context([], []))
                            trace.tool_result(rid, call, result)
                            stored = trace.read(rid)["tool_results"][0]
                            self.assertEqual(stored["raw_content"], result.content)
                            self.assertEqual(stored["content"], result.to_model_content())
                self.assertEqual(saved, {path: path.read_bytes() for path in research.root.rglob("*") if path.is_file()})
                self.assertEqual(before_database, database_rows())
                self.assertEqual(producer.calls, 0)
                self.assertEqual(len(research.repository.list_topics("agent-notes")), 2)
            finally:
                database.engine.dispose()


if __name__ == "__main__":
    unittest.main()
