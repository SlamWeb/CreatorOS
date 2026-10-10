"""Pure DTO transformations; no model request, HTTP call or business write."""
from copy import deepcopy
import json
import unittest

from creatoros.tools.model_projection import project_model_content, project_model_data, project_tool_messages


class ModelProjectionTests(unittest.TestCase):
    def test_directory_preserves_binding_ids_and_revision_not_ui_payload(self):
        data = {"creator_id": "creator-a", "creator_name": "账号 A", "items": [{
            "id": "series-a", "name": "英语", "revision": 3, "is_active": True,
            "mind_skill_id": "mind--123", "production_skill_id": "visual--123",
            "topic_count": 4, "cover_url": "/image", "producer_thread_id": "codex-thread",
        }], "message": "重复协议文案"}
        self.assertEqual(project_model_data("list_creator_series", data), {
            "creator_id": "creator-a", "creator_name": "账号 A", "items": [{
                "id": "series-a", "name": "英语", "revision": 3, "is_active": True,
                "mind_skill_id": "mind--123", "production_skill_id": "visual--123", "topic_count": 4,
            }],
        })

    def test_topic_page_retains_complete_sources_and_actions(self):
        source = {"title": "Oxford", "url": "https://example.com/dictionary"}
        topic = {"id": "topic-a", "title": "四个词", "brief": "切入点", "status": "queued",
                 "position": 2, "selection_state": "queued", "batch_id": "a" * 32,
                 "candidate_id": "c2", "sources": [source], "research_angle": "讲区别",
                 "existing_run_id": "run-a", "existing_run_version": 5,
                 "available_actions": ["view_run"], "cover_url": "/cover", "card_count": 9}
        page = {"offset": 20, "limit": 20, "total": 43}
        result = project_model_data("list_series_topics", {"items": [topic], "page": page})
        self.assertEqual(result["page"], page)
        self.assertEqual(result["items"][0]["sources"], [source])
        self.assertEqual(result["items"][0]["existing_run_version"], 5)
        self.assertEqual(result["items"][0]["available_actions"], ["view_run"])
        self.assertNotIn("cover_url", result["items"][0])
        self.assertNotIn("card_count", result["items"][0])

    def test_research_keeps_candidates_but_not_sdk_execution_history(self):
        result = project_model_data("get_topic_research", {
            "id": "a" * 32, "series_id": "series-a", "status": "ready", "stale": False,
            "note": "仅找到一条", "count": 10, "instructions": "常考",
            "series_context": {"name": "英语", "description": "词义", "audience": "考生", "skill_name": "words"},
            "candidates": [{"id": "c1", "title": "affect / effect", "angle": "词性",
                            "rationale": "常混淆", "sources": [{"title": "词典", "url": "https://example.com"}],
                            "queued": False}],
            "thread_id": "sdk-thread", "attempt": 2, "attempts": [{"usage": {"total": 1}}],
            "progress": {"events": ["完整诊断"]}, "url": "/series/series-a?research=aaa",
        })
        self.assertEqual(result["id"], "a" * 32)
        self.assertEqual(result["requested_count"], 10)
        self.assertEqual(result["returned_count"], 1)
        self.assertNotIn("count", result)
        self.assertEqual(result["candidates"][0]["queued"], False)
        self.assertEqual(result["candidates"][0]["rationale"], "常混淆")
        self.assertNotIn("thread_id", result)
        self.assertNotIn("attempts", result)
        self.assertNotIn("progress", result)

    def test_research_candidate_cardinality_is_actual_and_never_truncated_or_padded(self):
        for count in (0, 8, 10, 30):
            for tool in ("research_series_topics", "get_topic_research"):
                with self.subTest(count=count, tool=tool):
                    candidates = [{"id": f"c{index}", "title": f"常用词组 {index}", "angle": "完整切入点" * 60,
                        "rationale": "教学依据", "queued": False,
                        "sources": [{"title": "词典", "url": f"https://example.com/entry/{index}"}]} for index in range(count)]
                    original = {"id": "a" * 32, "count": 30, "status": "ready", "candidates": candidates,
                                "url": "/series/series-a?research=" + "a" * 32, "thread_id": "raw-trace-only"}
                    before = deepcopy(original)
                    projected = project_model_data(tool, original)
                    self.assertEqual(projected["requested_count"], 30)
                    self.assertEqual(projected["returned_count"], count)
                    self.assertEqual(projected["candidates"], candidates)
                    self.assertEqual(projected["url"], original["url"])
                    self.assertEqual(project_model_data(tool, projected), projected)
                    self.assertEqual(original, before)

    def test_research_outbound_view_preserves_raw_trace_and_same_batch_unknown_url(self):
        url = "/series/series-a?research=" + "b" * 32
        data = {"id": "b" * 32, "status": "unknown", "last_known_status": "researching",
                "count": 10, "candidates": [], "error": "无法确认状态", "url": url, "thread_id": "raw-only"}
        content = json.dumps(data, ensure_ascii=False)
        for error in (False, True):
            with self.subTest(error=error):
                raw = "[tool_error type=research_wait_timeout]\n" + content if error else content
                messages = [{"role": "assistant", "content": "", "tool_calls": [{"id": "call-a",
                    "name": "get_topic_research", "arguments": json.dumps({"batch_id": data["id"]})}]},
                    {"role": "tool", "tool_call_id": "call-a", "content": raw}]
                original = deepcopy(messages)
                result = project_tool_messages(messages)[-1]["content"]
                model = json.loads(result.split("\n", 1)[1] if error else result)
                self.assertEqual(model["id"], data["id"])
                self.assertEqual(model["status"], "unknown")
                self.assertEqual(model["last_known_status"], "researching")
                self.assertEqual(model["requested_count"], 10)
                self.assertEqual(model["returned_count"], 0)
                self.assertEqual(model["url"], url)
                self.assertNotIn("thread_id", model)
                self.assertEqual(messages, original)

    def test_research_description_explains_counts_unknown_and_original_url(self):
        from creatoros.tools.definitions import tool_registry
        for name in ("research_series_topics", "get_topic_research"):
            description = tool_registry[name].description
            self.assertIn("requested_count", description)
            self.assertIn("returned_count", description)
            self.assertIn("unknown", description)
            self.assertIn("不等于失败或远端已停止", description)
            self.assertIn("原样使用返回的 url", description)

    def test_run_summary_and_revision_cas_remain_usable(self):
        data = {"run_id": "run-a", "status": "awaiting_approval", "version": 7,
                "allowed_actions": ["request_revision"], "url": "/runs/run-a", "accepted": False,
                "revisions": [{"revision_id": "revision-a", "revision_number": 2,
                               "artifact_digest": "b" * 64, "artifact_available": True,
                               "attempts": [{"producer_thread_id": "thread"}], "cards": ["large"]}],
                "message": "终态说明", "input_snapshot": {"large": "data"}, "card_count": 9}
        result = project_model_data("get_content_run", data)
        self.assertEqual(result["version"], 7)
        self.assertEqual(result["allowed_actions"], ["request_revision"])
        self.assertEqual(result["revisions"][0]["artifact_digest"], "b" * 64)
        self.assertNotIn("attempts", result["revisions"][0])
        self.assertNotIn("input_snapshot", result)
        self.assertNotIn("message", result)

    def test_skill_read_and_edit_keep_full_text_digest_and_paging(self):
        body = "说明 local_path/thread_id 都是本文示例，不应修改\n" * 3000
        data = {"id": "skill-a", "name": "words", "description": "教学", "role": "mind",
                "path": "SKILL.md", "kind": "markdown", "content": body,
                "digest": "c" * 64, "editable": True, "local_path": "D:/private/working",
                "source_digest": "d" * 64,
                "page": {"offset": 0, "limit": 2000, "total_chars": 20000, "has_more": True, "next_offset": 2000}}
        for tool in ("get_producer_skill", "update_producer_skill_file"):
            with self.subTest(tool=tool):
                result = project_model_data(tool, data)
                self.assertEqual(result["content"], body)
                self.assertEqual(result["digest"], "c" * 64)
                self.assertEqual(result["page"], data["page"])
                self.assertNotIn("local_path", result)
                self.assertNotIn("source_digest", result)

    def test_skill_files_are_not_cut_or_interpreted_as_images(self):
        files = [{"path": "SKILL.md", "kind": "markdown", "size": 10},
                 {"path": "assets/reference.png", "kind": "image", "size": 100},
                 {"path": "scripts/render.py", "kind": "text", "size": 50}]
        data = {"id": "skill-a", "digest": "a" * 64, "editable": True, "files": files}
        self.assertEqual(project_model_data("get_producer_skill", data), data)

    def test_catalog_uses_ids_not_local_paths_or_original_digest(self):
        result = project_model_data("list_producer_skills", {"items": [{
            "id": "skill-a", "name": "words", "description": "词汇", "role": "legacy_end_to_end",
            "producible": False, "carousel_compatible": False, "compatibility_note": "不支持图片",
            "local_path": "D:/secret", "source_digest": "a" * 64, "digest": "b" * 64,
        }]})
        self.assertEqual(result["items"][0]["id"], "skill-a")
        self.assertEqual(result["items"][0]["compatibility_note"], "不支持图片")
        self.assertNotIn("local_path", result["items"][0])
        self.assertNotIn("digest", result["items"][0])

    def test_errors_preserve_actual_cause_and_cas_but_not_debug_fields(self):
        for data in ({"error": "skill_digest_conflict", "message": "目录已变化", "current_digest": "a" * 64,
                      "trace_path": "D:/private", "exception_type": "OSError"},
                     {"error": {"code": "agent_scope_rejected", "message": "不能访问", "debug": "x"}},
                     {"error": "studio_outcome_unknown", "message": "结果未知", "run_id": "run-a", "url": "/runs/run-a"}):
            result = project_model_data("get_content_run", data, is_error=True)
            if isinstance(data["error"], dict):
                self.assertEqual(result["error"], {"code": "agent_scope_rejected", "message": "不能访问"})
            else:
                self.assertEqual(result["error"], data["error"])
                self.assertEqual(result["message"], data["message"])
            self.assertNotIn("trace_path", result)
            self.assertNotIn("exception_type", result)

    def test_research_observation_error_preserves_same_batch_unknown(self):
        data = {"id": "a" * 32, "status": "unknown", "last_known_status": "researching",
                "error_type": "research_wait_timeout", "error": "请查询同一批次，不要重新提交。",
                "url": "/series/a?research=aaa", "thread_id": "hidden"}
        result = project_model_data("get_topic_research", data, is_error=True)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["last_known_status"], "researching")
        self.assertEqual(result["error"], data["error"])
        self.assertNotIn("thread_id", result)

    def test_discussion_keeps_actual_input_boundary_and_full_reply(self):
        context = {"revision_number": 2, "image_count": 4, "history_mode": "forked_production",
                   "includes": ["冻结版本图片", "同一作品既有历史"], "excludes": ["其他账号"],
                   "images": [{"sha256": "a" * 64}], "source_thread_id": "thread",
                   "skills": [{"role": "production", "name": "小白", "digest": "d" * 64}]}
        record = {"id": "discussion-a", "request_id": "request-a", "run_id": "run-a",
                  "revision_id": "revision-a", "status": "completed", "reply": "完整回答\n" * 1000,
                  "context": context, "thread_id": "thread", "events": ["工具详细事件"]}
        for data in (record, {"items": [record]}):
            result = project_model_data("get_content_discussion", data)
            projected = result["items"][0] if "items" in result else result
            self.assertEqual(projected["reply"], record["reply"])
            self.assertEqual(projected["context"]["image_count"], 4)
            self.assertEqual(projected["context"]["includes"], context["includes"])
            self.assertEqual(projected["context"]["skills"], [{"role": "production", "name": "小白"}])
            self.assertNotIn("thread_id", projected)
            self.assertNotIn("images", projected["context"])

    def test_tasks_keep_real_activity_times_and_summary(self):
        data = {"items": [{"id": "task-a", "kind": "production", "title": "词汇",
                           "status": "queued", "series_id": "series-a", "run_id": "run-a",
                           "url": "/runs/run-a", "updated_at": "2026-10-11", "last_activity_at": None,
                           "thread_id": "hidden"}],
                "summary": {"active": 0, "awaiting_approval": 0, "failed": 0}, "as_of": "2026-10-11"}
        result = project_model_data("get_creator_tasks", data)
        self.assertEqual(result["summary"], data["summary"])
        self.assertEqual(result["items"][0]["status"], "queued")
        self.assertEqual(result["items"][0]["updated_at"], "2026-10-11")
        self.assertNotIn("thread_id", result["items"][0])

    def test_series_write_and_queue_receipts_keep_exact_deduplication_facts(self):
        data = {"request_id": "request-a", "deduplicated": False,
                "series": {"id": "series-a", "name": "英语", "revision": 5}, "message": "协议文案"}
        self.assertEqual(project_model_data("compose_series", data), {
            "request_id": "request-a", "deduplicated": False, "series": data["series"],
        })
        queued = {"request_id": "r-a", "deduplicated": True, "operation_id": "op-a",
                  "series_id": "s-a", "topic_ids": ["t-a"], "url": "/series/s-a", "message": "长文"}
        self.assertEqual(project_model_data("queue_topics", queued), {k: v for k, v in queued.items() if k != "message"})

    def test_preview_never_advertises_a_confirmation_token(self):
        changes = [{"action": "add_topics", "series_id": "s-a", "before_order": [],
                    "after_order": ["t-a"], "after_topics": [{"topic_id": "t-a", "title": "词汇"}]}]
        data = {"operation_id": "op-a", "status": "awaiting_approval", "url": "/series/s-a",
                "preview": {"changes": changes, "confirmation_token": "must-not-advertise"}, "message": "协议文案"}
        self.assertEqual(project_model_data("prepare_topic_selection", data), {
            "operation_id": "op-a", "status": "awaiting_approval", "url": "/series/s-a", "preview": {"changes": changes},
        })

    def test_installation_retains_failed_message_and_registered_skill_identity(self):
        failed = {"id": "job-a", "status": "failed", "message": "请核对 Git ref", "attempt": 2}
        self.assertEqual(project_model_data("get_skill_install", failed), {
            "id": "job-a", "status": "failed", "message": "请核对 Git ref",
        })
        installed = {"id": "job-a", "status": "installed", "skill": {
            "id": "skill-a", "name": "小白", "producible": True, "local_path": "D:/private",
        }, "message": "重复协议说明"}
        result = project_model_data("get_skill_install", installed)
        self.assertEqual(result["skill"], {"id": "skill-a", "name": "小白", "producible": True})
        self.assertNotIn("message", result)

    def test_research_and_discussion_errors_do_not_leak_nested_debug_fields(self):
        data = {"id": "record-a", "status": "unknown", "error": {
            "code": "studio_read_failed", "message": "稍后查询", "thread_id": "debug-thread",
        }}
        for tool in ("get_topic_research", "get_content_discussion"):
            with self.subTest(tool=tool):
                self.assertEqual(project_model_data(tool, data, is_error=True)["error"], {
                    "code": "studio_read_failed", "message": "稍后查询",
                })

    def test_malformed_optional_collections_do_not_turn_business_success_into_exception(self):
        for tool, data in (("get_content_run", {"status": "queued", "revisions": None}),
                           ("get_topic_research", {"status": "ready", "candidates": None}),
                           ("list_series_topics", {"items": [{"sources": None}]}),
                           ("get_producer_skill", {"files": None}),
                           ("get_content_discussion", {"context": None}),
                           ("list_producer_skills", {"items": None})):
            with self.subTest(tool=tool):
                self.assertEqual(project_model_data(tool, data), data)

    def test_projection_cannot_modify_original_trace_evidence(self):
        original = {"items": [{"id": "t-a", "title": "词汇", "sources": [{"title": "词典", "url": "https://example.com"}],
                               "cover_url": "/cover"}], "page": {"offset": 0, "limit": 20, "total": 1}}
        before = deepcopy(original)
        result = project_model_data("list_series_topics", original)
        result["items"][0]["sources"][0]["title"] = "改动投影"
        self.assertEqual(original, before)

    def test_unknown_tool_and_non_json_keep_original_contract(self):
        content = ' { "custom": "original bytes", "thread_id": "retained for legacy" } '
        self.assertEqual(project_model_content("custom_tool", content), content)
        self.assertEqual(project_model_content("get_content_run", "原始文本错误"), "原始文本错误")
        self.assertEqual(project_model_content("get_content_run", "null"), "null")

    def test_json_content_projects_without_dropping_unicode_or_raw_source(self):
        original = json.dumps({"id": "batch-a", "status": "ready", "note": "不足十条",
                               "thread_id": "thread"}, ensure_ascii=False)
        result = project_model_content("get_topic_research", original)
        self.assertEqual(json.loads(result), {"id": "batch-a", "status": "ready", "note": "不足十条"})
        self.assertIn("thread_id", json.loads(original))


if __name__ == "__main__":
    unittest.main()
