"""Presentation/event identity regressions; no paid model calls."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from creatoros.events import AgentEvent
from creatoros.tools.model_projection import project_model_data
from creatoros.web.chat import AgentChatService
from creatoros.web.chat_links import tool_links
from creatoros.web.chat_stream import session_patch


class ChatStreamTests(unittest.TestCase):
    def test_real_loopback_sse_snapshot_then_append_patch(self):
        import httpx
        from fastapi import FastAPI
        from creatoros.web.chat_routes import chat_routes
        from tests.agent_studio_support import serve

        with TemporaryDirectory() as temporary:
            service = AgentChatService(Path(temporary))
            doc = service._read(service.create()["id"])
            doc["entries"] = [{"id": "answer", "kind": "assistant", "text": "第一段"}]
            service._save(doc)
            app = FastAPI()
            app.include_router(chat_routes(service))
            with serve(app) as base:
                with httpx.stream("GET", f"{base}/api/agent/sessions/{doc['id']}/events", timeout=5) as response:
                    response.raise_for_status()
                    lines = response.iter_lines()
                    def next_event():
                        kind, data = None, None
                        for line in lines:
                            if line.startswith("event: "):
                                kind = line[7:]
                            elif line.startswith("data: "):
                                data = json.loads(line[6:])
                            elif not line and kind:
                                return kind, data
                    event, snapshot = next_event()
                    self.assertEqual(event, "snapshot")
                    doc["entries"][0]["text"] += "，第二段"
                    service._save(doc)
                    event, patch = next_event()
                    self.assertEqual(event, "patch")
                    self.assertEqual(patch["base_revision"], snapshot["stream_revision"])
                    self.assertEqual(patch["entries_text_append"], [{"id": "answer", "text": "，第二段"}])
                    self.assertNotIn("entries", patch)

    def test_patch_only_appends_suffix_and_updates_exact_tool(self):
        old = {"id": "session", "version": 1, "stream_revision": 4, "status": "running", "entries": [
            {"id": "answer", "kind": "assistant", "text": "你好"},
            {"id": "call-a", "kind": "tool", "status": "running"}]}
        new = deepcopy(old)
        new["stream_revision"] = 7
        new["entries"][0]["text"] += "，正在查询。"
        new["entries"][1]["status"] = "done"
        patch = session_patch(old, new)
        self.assertEqual(patch["entries_text_append"], [{"id": "answer", "text": "，正在查询。"}])
        self.assertEqual(patch["entries_upsert"], [{"id": "call-a", "status": "done"}])
        self.assertEqual(patch["base_revision"], 4)
        self.assertNotIn("version", patch)
        self.assertNotIn("entry_order", patch)
        self.assertEqual(old["entries"][0]["text"], "你好")

    def test_new_entries_full_replacements_trimming_and_metadata(self):
        old = {"id": "s", "stream_revision": 1, "entries": [{"id": "a", "kind": "assistant", "text": "旧文"}]}
        new = {"id": "s", "stream_revision": 2, "entries": [{"id": "b", "kind": "assistant", "text": "新文"}], "status": "idle"}
        patch = session_patch(old, new)
        self.assertEqual(patch["entry_order"], ["b"])
        self.assertEqual(patch["entries_upsert"], new["entries"])
        self.assertEqual(patch["status"], "idle")
        new["entries"][0]["id"] = "a"
        self.assertEqual(session_patch(old, new)["entries_upsert"][0]["text"], "新文")

    def test_legacy_ids_stay_stable_across_bounded_views_without_writes(self):
        with TemporaryDirectory() as temporary:
            service = AgentChatService(Path(temporary))
            doc = service._read(service.create()["id"])
            doc["entries"] = [{"kind": "user", "text": str(i)} for i in range(205)]
            service._save(doc)
            before_bytes = service._path(doc["id"]).read_bytes()
            first = service.get(doc["id"])
            self.assertEqual(service._path(doc["id"]).read_bytes(), before_bytes)
            self.assertNotIn("id", doc["entries"][5])
            doc["entries"].append({"kind": "assistant", "text": "最后"})
            second = service._view(doc)
            self.assertEqual(first["entries"][1]["id"], second["entries"][0]["id"])

    def test_tool_results_match_call_identity_not_latest_entry_or_tool_name(self):
        with TemporaryDirectory() as temporary:
            service = AgentChatService(Path(temporary))
            doc = service._read(service.create()["id"])
            doc["requests"].append({"id": "turn"})
            for call in ("a", "b"):
                service._emit(doc, AgentEvent("tool_call", {"name": "manage_topics", "turn_id": "turn",
                    "tool_call_id": call, "model_request_id": "request"}))
            service._emit(doc, AgentEvent("tool_result", {"name": "manage_topics", "content": "{}",
                "tool_call_id": "a", "model_request_id": "request", "is_error": False}))
            self.assertEqual([e["status"] for e in doc["entries"]], ["done", "running"])
            revision = doc["stream_revision"]
            usage = {"input_tokens": 10, "output_tokens": 2, "turn_id": "turn", "model_request_id": "request"}
            service._emit(doc, AgentEvent("model_usage", usage))
            service._emit(doc, AgentEvent("model_usage", usage))
            self.assertEqual(len([e for e in doc["entries"] if e["kind"] == "usage"]), 1)
            self.assertGreater(doc["stream_revision"], revision)
            self.assertEqual(doc["version"], 0)

    def test_rsi_projection_preserves_actual_submission_and_recovery(self):
        raw = {"id": "a" * 64, "stage": "judge", "status": "failed", "can_resume": True,
               "next_action": "resume_current_stage", "active_round": {"number": 1, "status": "completed",
               "production_submitted": True, "production_status": "completed", "image_count": 1,
               "trial_url": "/skills?extraction=" + "b" * 64, "private": "not model context"},
               "model_calls": [{"raw": "large"}]}
        result = project_model_data("manage_skill_iteration", raw)
        self.assertTrue(result["active_round"]["production_submitted"])
        self.assertEqual(result["active_round"]["image_count"], 1)
        self.assertEqual(result["stage"], "judge")
        self.assertTrue(result["can_resume"])
        self.assertNotIn("model_calls", result)
        self.assertNotIn("private", result["active_round"])

    def test_named_links_distinguish_fresh_runs_and_guard_links_are_strict(self):
        links = []
        for identity in ("aaaaaa-123", "bbbbbb-123"):
            links.extend(tool_links("start_content_run", json.dumps({"run_id": identity,
                "topic_title": "同一选题", "active_revision_number": 1, "url": "/runs/" + identity})))
        self.assertNotEqual(links[0]["label"], links[1]["label"])
        self.assertTrue(all("同一选题" in link["label"] and link["kind"] == "production" for link in links))
        error = {"error": "skill_iteration_active", "iteration_id": "a" * 64,
                 "url": "/skills?iteration=" + "a" * 64}
        self.assertEqual(tool_links("start_content_run", json.dumps(error), is_error=True)[0]["kind"], "iteration")
        error["url"] = "/skills?iteration=" + "b" * 64
        self.assertEqual(tool_links("start_content_run", json.dumps(error), is_error=True), [])
        rejected = {"items": [{"accepted": False, "error_code": "skill_iteration_active",
                    "iteration_id": "a" * 64, "url": "/skills?iteration=" + "a" * 64}]}
        self.assertEqual(tool_links("start_content_run", json.dumps(rejected))[0]["kind"], "iteration")


if __name__ == "__main__":
    unittest.main()
