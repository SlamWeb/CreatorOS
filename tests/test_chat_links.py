"""Local navigation transformations / persistence. No model quality claims."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from creatoros.ai.types import TextDelta
from creatoros.events import AgentEvent
from creatoros.web.chat import AgentChatService
from creatoros.web.chat_links import tool_links
from creatoros.integrations.studio import StudioClient, StudioClientError

ORIGIN = "http://127.0.0.1:8878"
BATCH = "a" * 32
SERIES = "series-a"
RUN = "12345678-1234-1234-1234-123456789012"


class ChatLinksTests(unittest.TestCase):
    def links(self, tool="get_topic_research", **changes):
        data = {"id": BATCH, "series_id": SERIES, "status": "ready",
                "url": f"/series/{SERIES}?research={BATCH}"} | changes
        return tool_links(tool, json.dumps(data), studio_url=ORIGIN,
                          is_error=data.get("status") in {"failed", "unknown"})

    def test_failed_unknown_keep_same_batch_link_not_success(self):
        for state in ("ready", "failed", "unknown", "interrupted"):
            with self.subTest(state=state):
                links = self.links(status=state)
                self.assertEqual(links[0]["url"], f"/series/{SERIES}?research={BATCH}")

    def test_denied_or_other_tool_does_not_authorize_an_address(self):
        self.assertEqual(self.links(status="forbidden"), [])
        self.assertEqual(self.links(tool="get_producer_skill", status="ready"), [])
        data = json.dumps({"series_id": SERIES, "url": f"/series/{SERIES}"})
        self.assertEqual(tool_links("list_series_topics", data, is_error=True), [])
        data = json.dumps({"id": BATCH, "status": "unknown", "url": f"/series/{SERIES}?research={BATCH}"})
        self.assertEqual(tool_links("get_topic_research", data, is_error=True, error_type="agent_scope_rejected"), [])

    def test_route_identity_query_origin_and_protocol_are_strict(self):
        bad = [f"/series/series-b?research={BATCH}", f"/series/{SERIES}?research={'b'*32}",
            f"/series/{SERIES}?research={BATCH}&research={BATCH}",
            f"/series/{SERIES}?research={BATCH}&redirect=evil", f"/series/{SERIES}?research={BATCH}#frag",
            "javascript:alert(1)", f"//127.0.0.1:8878/series/{SERIES}?research={BATCH}",
            f"http://127.0.0.1:8765/series/{SERIES}?research={BATCH}",
            f"https://example.com/series/{SERIES}?research={BATCH}", f"series/{SERIES}?research={BATCH}",
            f"/series/../{SERIES}?research={BATCH}", f"/series/{SERIES}%2f?research={BATCH}",
            f"/series/{SERIES}\\?research={BATCH}", f"{ORIGIN.replace('://', '://u:p@')}/series/{SERIES}?research={BATCH}"]
        for url in bad:
            with self.subTest(url=url):
                self.assertEqual(self.links(url=url), [])
        self.assertEqual(self.links(url=f"{ORIGIN}/series/{SERIES}?research={BATCH}")[0]["url"],
                         f"/series/{SERIES}?research={BATCH}")

    def test_sources_candidate_and_skill_body_are_not_navigation(self):
        data = {"items": [{"url": f"/runs/{RUN}"}], "content": f"/runs/{RUN}",
                "candidates": [{"url": f"/runs/{RUN}"}], "sources": [{"url": f"/runs/{RUN}"}]}
        for tool in ("get_producer_skill", "list_series_topics", "get_topic_research", "read_tool_result"):
            self.assertEqual(tool_links(tool, json.dumps(data)), [])

    def test_task_receipts_check_row_objects_and_never_invent_a_domain(self):
        items = [{"id": RUN, "kind": "production", "run_id": RUN, "url": f"{ORIGIN}/runs/{RUN}"},
                 {"id": BATCH, "kind": "research", "series_id": SERIES, "url": f"/series/{SERIES}?research={BATCH}"},
                 {"id": "discussion-a", "kind": "discussion", "run_id": RUN, "revision_id": "revision-a",
                  "url": f"/runs/{RUN}?discussion=discussion-a&revision=revision-a"}]
        self.assertEqual(len(tool_links("get_creator_tasks", json.dumps({"items": items}), studio_url=ORIGIN)), 3)
        items[0]["run_id"] = "different"
        items[1]["series_id"] = "different"
        items[2]["revision_id"] = "different"
        self.assertEqual(tool_links("get_creator_tasks", json.dumps({"items": items}), studio_url=ORIGIN), [])

    def test_multiple_steps_persist_raw_and_no_turn_or_session_carryover(self):
        with TemporaryDirectory() as root:
            service = AgentChatService(Path(root))
            doc = service._read(service.create()["id"])
            doc["requests"].append({"id": "turn-a", "text": "query"})
            for index in (1, 2):
                service._emit(doc, AgentEvent("turn_start", {"turn_id": "turn-a"}))
                service._emit(doc, AgentEvent("tool_call", {"name": "list_series_topics"}))
                service._emit(doc, AgentEvent("tool_result", {"name": "list_series_topics", "content": json.dumps({
                    "series_id": f"series-{index}", "url": f"/series/series-{index}"}), "is_error": False}))
            service._emit(doc, AgentEvent("turn_start", {"turn_id": "turn-a"}))
            raw = "错误模型链接 [查看](https://example.com/series/series-1)"
            service._emit(doc, TextDelta(raw))
            service._emit(doc, AgentEvent("model_response", {"turn_id": "turn-a", "request_id": "request-a", "complete": True}))
            answer = doc["entries"][-1]
            self.assertEqual(answer["text"], raw)
            self.assertEqual(len(answer["links"]), 2)
            restarted = AgentChatService(Path(root))
            self.assertEqual(restarted.get(doc["id"])["entries"][-1], answer)
            doc["requests"].append({"id": "turn-b", "text": "next"})
            service._emit(doc, AgentEvent("turn_start", {"turn_id": "turn-b"}))
            service._emit(doc, AgentEvent("model_response", {"turn_id": "turn-b", "request_id": "request-b", "complete": True}))
            self.assertEqual(doc["entries"][-1]["links"], [])
            self.assertEqual(service.create()["entries"], [])

    def test_unexpected_shapes_fail_closed_not_business_failure(self):
        for body in ("broken", "null", "[]", '{"items": null}', '{"items":[null]}'):
            self.assertEqual(tool_links("get_creator_tasks", body), [])

    def test_preview_checks_research_and_operation_identity(self):
        data = {"batch_id": BATCH, "series_id": SERIES, "operation_id": "operation-a",
            "url": f"/series/{SERIES}?research={BATCH}&operation=operation-a"}
        self.assertEqual(tool_links("prepare_topic_selection", json.dumps(data))[0]["label"], "查看入队预览")
        data["operation_id"] = "operation-b"
        self.assertEqual(tool_links("prepare_topic_selection", json.dumps(data)), [])

    def test_busy_other_account_id_never_becomes_navigation(self):
        class BusyClient(StudioClient):
            # Declared transport fault, not a model or end-to-end evaluation.
            def request(self, method, path, **kwargs):
                if path == "/api/runs":
                    return {"id": RUN, "status": "queued", "active_revision_number": 1,
                            "version": 1, "revisions": [{"attempts": []}]}
                raise StudioClientError("busy", "producer_busy", run_id="foreign-run")
        client = BusyClient(ORIGIN)
        try:
            with self.assertRaises(StudioClientError) as raised:
                client.start("own-topic")
            self.assertEqual(raised.exception.run_id, RUN)
        finally:
            client.close()
        self.assertEqual(tool_links("start_content_run", json.dumps({"run_id": RUN,
            "code": "producer_busy"}), is_error=True), [])

    def test_discussion_receipt_is_from_guarded_record_not_reply(self):
        row = {"id": "discussion-a", "run_id": RUN, "revision_id": "revision-a",
               "status": "completed", "reply": f"[fake](/runs/foreign)"}
        link = tool_links("get_content_discussion", json.dumps({"items": [row]}))[0]
        self.assertEqual(link["url"], f"/runs/{RUN}?discussion=discussion-a&revision=revision-a")
        row.update(status="failed")
        self.assertEqual(tool_links("discuss_content_run", json.dumps(row), is_error=True)[0], link | {"source_tool": "discuss_content_run"})
        self.assertEqual(tool_links("discuss_content_run", json.dumps(row), is_error=True,
                                    error_type="agent_scope_rejected"), [])

    def test_progress_uses_same_receipts_and_failed_run_has_no_link(self):
        with TemporaryDirectory() as root:
            service = AgentChatService(Path(root))
            doc = service._read(service.create()["id"])
            doc["requests"].append({"id": "turn-a"})
            service._emit(doc, AgentEvent("tool_call", {"name": "get_topic_research"}))
            service._emit(doc, AgentEvent("research_progress", {"id": BATCH,
                "status": "researching", "url": f"{ORIGIN}/series/{SERIES}?research={BATCH}"}), ORIGIN)
            self.assertEqual(len(doc["entries"][-1]["links"]), 1)
            service._emit(doc, AgentEvent("tool_call", {"name": "start_content_run"}))
            service._emit(doc, AgentEvent("tool_result", {"name": "start_content_run",
                "content": json.dumps({"run_id": RUN, "code": "producer_busy"}), "is_error": True}), ORIGIN)
            self.assertEqual(doc["entries"][-1]["links"], [])

    def test_queue_real_scoped_receipt_has_identity_on_success_and_replay(self):
        # Actual SQLite + guarded loopback HTTP DTO, no model or production.
        from creatoros.context import RuntimeContext
        from creatoros.runs import ContentRunService
        from creatoros.storage import ContentRepository, Database, upgrade_database
        from creatoros.tools.studio import queue_topics
        from creatoros.web.app import create_app
        from tests.agent_studio_support import serve

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            database_url = f"sqlite:///{(root / 'queue-links.db').as_posix()}"
            upgrade_database(database_url)
            db = Database(database_url)
            try:
                repo = ContentRepository(db)
                for identity in ("a", "b"):
                    repo.create_creator(creator_id=f"creator-{identity}", display_name=identity)
                    repo.create_series(series_id=f"series-{identity}", creator_id=f"creator-{identity}",
                        name=identity, description="fixture", audience="fixture", skill_name="knowledge-to-carousel")
                chat_root = root / "chat"
                app = create_app(database=db, chat_root=chat_root, eval_root=root / "eval",
                    run_service=ContentRunService(db, output_root=root / "outputs"))
                with serve(app) as base:
                    session_id = app.state.chat.create("creator-a")["id"]
                    context = RuntimeContext(project_root=root, studio_url=base,
                        session_file=chat_root / session_id / "messages.json", creator_id="creator-a",
                        agent_session_id=session_id, user_request_id="queue-link-turn")
                    first = queue_topics(SERIES, [{"title": "回执导航测试"}], context=context)
                    replay = queue_topics(SERIES, [{"title": "回执导航测试"}], context=context)
                    rows = [json.loads(result.content) for result in (first, replay)]
                    for result, row in zip((first, replay), rows):
                        self.assertFalse(result.is_error, result.content)
                        self.assertEqual(row["series_id"], SERIES)
                        self.assertEqual(tool_links("queue_topics", result.content, studio_url=base),
                            [{"url": f"/series/{SERIES}", "label": "查看栏目", "source_tool": "queue_topics"}])
                    self.assertTrue(rows[1]["deduplicated"])
                    self.assertEqual(rows[0]["request_id"], rows[1]["request_id"])
                    self.assertEqual(rows[0]["topic_ids"], rows[1]["topic_ids"])
                    self.assertEqual(len(repo.list_topics(SERIES)), 1)
                    mismatch = rows[0] | {"url": "/series/series-b"}
                    self.assertEqual(tool_links("queue_topics", json.dumps(mismatch), studio_url=base), [])
                    denied = queue_topics("series-b", [{"title": "不得跨账号"}], context=context)
                    self.assertTrue(denied.is_error)
                    self.assertEqual(tool_links("queue_topics", denied.content, is_error=True,
                        error_type=denied.error_type, studio_url=base), [])
                    self.assertEqual(repo.list_topics("series-b"), ())
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
