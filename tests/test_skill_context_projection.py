"""Pure local projection tests for repeated Skill authoring context."""
from copy import deepcopy
import json
import unittest

from creatoros.tools.model_projection import project_model_data, project_tool_messages


class SkillContextProjectionTests(unittest.TestCase):
    def test_ready_author_keeps_shared_prompt_once_and_preserves_distinct_source(self):
        authored_text = "用户提供的完整方法正文。\n第二段正文。"
        skill_md = (
            "---\nname: my-skill\ndescription: 一份自写 Skill\n---\n"
            + authored_text
            + "\n\n## 交付约定\n正文继续完整保留。"
        )
        ready_job = {
            "id": "job-author-1",
            "status": "ready",
            "task_kind": "author",
            "operation": None,
            "content": authored_text,
            "source_text": authored_text,
            "digest": "job-digest-1",
            "revision": 2,
            "actions": {"request-hash-1": {
                "operation": "edit", "digest": "draft-digest", "value": "private duplicate"}},
            "allowed_actions": ["save", "edit"],
            "uploads": [{"id": "upload-1", "name": "reference.png", "url": "/uploads/1"}],
            "files": [{"path": "mind/SKILL.md", "kind": "text", "size": len(skill_md)}],
            "skills": [{"name": "my-skill", "role": "mind", "output_kind": "text",
                        "digest": "skill-digest-1", "skill_md": skill_md}],
        }
        original = deepcopy(ready_job)

        projected = project_model_data("get_skill_extraction", ready_job)

        self.assertEqual(projected["skills"][0]["skill_md"], skill_md)
        self.assertNotIn("content", projected)
        self.assertNotIn("source_text", projected)
        self.assertEqual(projected["id"], "job-author-1")
        self.assertEqual(projected["digest"], "job-digest-1")
        self.assertNotIn("actions", projected)
        self.assertEqual(projected["allowed_actions"], ["save", "edit"])
        self.assertEqual(projected["uploads"], ready_job["uploads"])
        self.assertEqual(projected["files"], ready_job["files"])
        self.assertEqual(ready_job, original)

        separate_source = deepcopy(ready_job)
        separate_source["content"] = "模型制作要求，与最终 Skill 正文不同。"
        separate_source["source_text"] = "用户另外提供、未收进 Skill 的上下文。"
        projected_separate = project_model_data("get_skill_extraction", separate_source)
        self.assertEqual(projected_separate["content"], separate_source["content"])
        self.assertEqual(projected_separate["source_text"], separate_source["source_text"])
        self.assertEqual(separate_source["content"], "模型制作要求，与最终 Skill 正文不同。")

    def test_skill_file_write_receipt_keeps_identity_without_echoing_saved_body(self):
        write_body = "已保存的长 Skill 正文。"
        receipt = {
            "skill_id": "mind-skill--abc123",
            "family_id": "family-1",
            "parent_id": "parent-1",
            "version_number": 4,
            "version_status": "candidate",
            "digest": "digest-v4",
            "path": "mind/SKILL.md",
            "kind": "text",
            "operation": "write_text",
            "content": write_body,
            "message": "文件已保存。",
        }

        projected = project_model_data("update_producer_skill_file", receipt)

        self.assertNotIn("content", projected)
        for field in ("skill_id", "family_id", "parent_id", "version_number",
                      "version_status", "digest", "path", "operation"):
            self.assertEqual(projected[field], receipt[field], field)

    def test_explicit_producer_skill_read_keeps_the_requested_full_page(self):
        full_page = "---\nname: my-skill\n---\n完整正文与操作步骤。\n" * 30
        page = {"offset": 2000, "limit": 4000, "total_chars": 12000,
                "has_more": True, "next_offset": 6000}
        read_result = {"skill_id": "skill-a", "name": "my-skill",
                       "path": "mind/SKILL.md", "content": full_page,
                       "digest": "skill-digest-v2", "page": page}

        projected = project_model_data("get_producer_skill", read_result)

        self.assertEqual(projected["content"], full_page)
        self.assertEqual(projected["digest"], "skill-digest-v2")
        self.assertEqual(projected["page"], page)

    def test_active_extraction_read_keeps_unique_input_and_current_draft(self):
        active_job = {
            "id": "job-running-1",
            "status": "running",
            "operation": "revise",
            "task_kind": "extract",
            "last_known_status": "ready",
            "next_action": "wait_background",
            "digest": "draft-digest-2",
            "instruction": "只保留这段有辨识度的修改要求。",
            "source_text": "用户提供的唯一参考原文，供核对任务输入。",
            "actions": {"private-key": {"operation": "revise", "value": "私有签名"}},
            "allowed_actions": ["cancel"],
            "skills": [{"name": "my-skill", "role": "mind", "skill_md": "当前可读草稿"}],
        }

        projected = project_model_data("get_skill_extraction", active_job)

        for field in ("id", "status", "operation", "last_known_status", "next_action",
                      "digest", "instruction", "source_text"):
            self.assertEqual(projected[field], active_job[field], field)
        self.assertEqual(projected["skills"][0]["skill_md"], "当前可读草稿")
        self.assertNotIn("actions", projected)

    def test_active_extraction_submission_receipt_does_not_echo_its_inputs(self):
        submission = {
            "id": "job-queued-1",
            "status": "queued",
            "operation": "extract",
            "task_kind": "extract",
            "source_text": "PRIVATE_SUBMITTED_REFERENCE",
            "instruction": "PRIVATE_SUBMITTED_INSTRUCTION",
            "upload_ids": ["private-upload-hash"],
            "digest": "input-digest-1",
        }

        projected = project_model_data("extract_skill_from_images", submission)

        self.assertEqual(projected["id"], "job-queued-1")
        self.assertEqual(projected["status"], "queued")
        self.assertEqual(projected["operation"], "extract")
        self.assertEqual(projected["digest"], "input-digest-1")
        self.assertEqual(projected["next_action"], "wait_background")
        for field in ("source_text", "instruction", "upload_ids"):
            self.assertNotIn(field, projected)
        self.assertNotIn("PRIVATE_SUBMITTED", json.dumps(projected))

    def test_extraction_list_contains_index_fields_not_full_drafts(self):
        listing = {
            "page": {"offset": 0, "limit": 20, "total": 1, "has_more": False},
            "items": [{
                "id": "job-ready-1",
                "status": "ready",
                "digest": "draft-digest-3",
                "source_text": "重复的大段输入。",
                "instruction": "重复的大段修改说明。",
                "actions": {"private-key": {"value": "private signature"}},
                "skills": [{"name": "my-skill", "description": "方法说明", "role": "mind",
                            "output_kind": "text", "skill_md": "PRIVATE FULL SKILL"}],
                "saved_skills": [{"id": "saved-skill-1", "name": "my-skill",
                                  "digest": "saved-digest-1", "local_path": "private/path"}],
            }],
        }

        projected = project_model_data("get_skill_extraction", listing)
        item = projected["items"][0]

        self.assertEqual(projected["page"], listing["page"])
        self.assertEqual(item["id"], "job-ready-1")
        self.assertEqual(item["status"], "ready")
        self.assertEqual(item["digest"], "draft-digest-3")
        self.assertNotIn("source_text", item)
        self.assertNotIn("instruction", item)
        self.assertNotIn("actions", item)
        self.assertNotIn("skill_md", item["skills"][0])
        self.assertEqual(item["saved_skills"][0], {
            "id": "saved-skill-1", "name": "my-skill", "digest": "saved-digest-1"})

    def test_extraction_edit_save_and_cancel_keep_receipts_without_skill_bodies(self):
        body = "PRIVATE_SKILL_BODY"
        cases = (
            ("edit_extracted_skills", {
                "id": "job-a", "status": "ready", "operation": None,
                "digest": "edited-digest", "allowed_actions": ["edit", "save", "cancel"],
                "skills": [{"name": "my-skill", "role": "mind", "output_kind": "text",
                            "skill_md": body}], "source_text": "repeated input"}),
            ("save_extracted_skills", {
                "id": "job-a", "status": "saved", "operation": None,
                "digest": "saved-digest", "allowed_actions": ["view_saved_skill"],
                "saved_skills": [{"id": "exact-saved-id", "name": "my-skill",
                                  "digest": "saved-skill-digest", "local_path": "private/path"}],
                "skills": [{"name": "my-skill", "skill_md": body}]}),
            ("cancel_skill_extraction", {
                "id": "job-a", "status": "interrupted", "operation": None,
                "digest": "draft-digest", "allowed_actions": [],
                "instruction": "PRIVATE_INSTRUCTION", "skills": [{"skill_md": body}]}),
        )

        for tool_name, receipt in cases:
            with self.subTest(tool_name=tool_name):
                projected = project_model_data(tool_name, receipt)
                self.assertEqual(projected["id"], "job-a")
                self.assertEqual(projected["status"], receipt["status"])
                self.assertEqual(projected["digest"], receipt["digest"])
                self.assertEqual(projected["allowed_actions"], receipt["allowed_actions"])
                self.assertNotIn("source_text", projected)
                self.assertNotIn("instruction", projected)
                self.assertNotIn("PRIVATE_SKILL_BODY", json.dumps(projected))
                self.assertNotIn("skill_md", json.dumps(projected))
                self.assertNotIn("actions", projected)
                if tool_name == "save_extracted_skills":
                    self.assertEqual(projected["saved_skills"][0]["id"], "exact-saved-id")
                    self.assertEqual(projected["saved_skills"][0]["digest"], "saved-skill-digest")
                    self.assertNotIn("local_path", projected["saved_skills"][0])

    def test_repeated_identical_skill_read_uses_first_visible_body_and_is_idempotent(self):
        body = "FULL REQUESTED SKILL BODY\n" * 8
        args = json.dumps({"skill_id": "skill-a", "path": "mind/SKILL.md", "offset": 0, "limit": 2000})
        result = json.dumps({"skill_id": "skill-a", "path": "mind/SKILL.md",
                             "digest": "digest-1", "page": {"offset": 0, "limit": 2000,
                             "total_chars": len(body), "has_more": False}, "content": body})
        ledger = [
            {"role": "assistant", "reasoning_content": "opaque reasoning stays unchanged",
             "tool_calls": [{"id": "read-1", "type": "function",
                             "function": {"name": "get_producer_skill", "arguments": args}}]},
            {"role": "tool", "tool_call_id": "read-1", "content": result},
            {"role": "assistant", "reasoning_content": "second opaque reasoning",
             "tool_calls": [{"id": "read-2", "type": "function",
                             "function": {"name": "get_producer_skill", "arguments": args}}]},
            {"role": "tool", "tool_call_id": "read-2", "content": result},
        ]
        original = deepcopy(ledger)

        projected = project_tool_messages(ledger)
        first, second = json.loads(projected[1]["content"]), json.loads(projected[3]["content"])

        self.assertEqual(first["content"], body)
        self.assertNotIn("content", second)
        self.assertEqual(second["content_ref"], {"tool_call_id": "read-1", "field": "content"})
        self.assertEqual(projected[0]["reasoning_content"], ledger[0]["reasoning_content"])
        self.assertEqual(projected[2]["reasoning_content"], ledger[2]["reasoning_content"])
        self.assertEqual(projected[0]["tool_calls"], ledger[0]["tool_calls"])
        self.assertEqual(projected[2]["tool_calls"], ledger[2]["tool_calls"])
        self.assertEqual(ledger, original)
        self.assertEqual(project_tool_messages(projected), projected)

    def test_repeated_extraction_read_references_same_visible_job_text(self):
        args = lambda job_id: json.dumps({"job_id": job_id})

        def tool_result(job_id, digest):
            return json.dumps({
                "id": job_id,
                "status": "ready",
                "task_kind": "extract",
                "operation": None,
                "digest": digest,
                "revision": 3,
                "source_text": "Original reference excerpt.",
                "instruction": "Preserve this exact requested perspective.",
                "content": "Current authored brief.",
                "skills": [{"name": "my-skill", "role": "mind",
                            "skill_md": "Draft body that includes Original reference excerpt."}],
            })

        messages = []
        for call_id, job_id, digest in (
            ("same-1", "job-a", "digest-a"),
            ("same-2", "job-a", "digest-a"),
            ("other-job", "job-b", "digest-a"),
            ("new-digest", "job-a", "digest-b"),
        ):
            messages.extend([
                {"role": "assistant", "tool_calls": [{"id": call_id, "type": "function",
                 "function": {"name": "get_skill_extraction", "arguments": args(job_id)}}]},
                {"role": "tool", "tool_call_id": call_id, "content": tool_result(job_id, digest)},
            ])
        original = deepcopy(messages)

        projected = project_tool_messages(messages)
        views = [json.loads(projected[index]["content"]) for index in (1, 3, 5, 7)]

        self.assertEqual(views[0]["source_text"], "Original reference excerpt.")
        self.assertEqual(views[0]["instruction"], "Preserve this exact requested perspective.")
        self.assertEqual(views[0]["content"], "Current authored brief.")
        self.assertEqual(views[1]["source_text_ref"], {"tool_call_id": "same-1", "field": "source_text"})
        self.assertEqual(views[1]["instruction_ref"], {"tool_call_id": "same-1", "field": "instruction"})
        self.assertEqual(views[1]["content_ref"], {"tool_call_id": "same-1", "field": "content"})
        self.assertEqual(views[0]["skills"][0]["skill_md"],
                         "Draft body that includes Original reference excerpt.")
        self.assertEqual(views[1]["skills"][0]["skill_md_ref"],
                         {"tool_call_id": "same-1", "field": "skills[0].skill_md"})
        for view in (views[2], views[3]):
            self.assertIn("source_text", view)
            self.assertIn("instruction", view)
            self.assertIn("content", view)
            self.assertNotIn("source_text_ref", view)
        self.assertEqual(messages, original)

    def test_skill_read_dedup_requires_same_file_version_page_and_exact_body(self):
        page = {"offset": 0, "limit": 2000, "total_chars": 40, "has_more": False}
        base = {"skill_id": "skill-a", "path": "mind/SKILL.md", "digest": "version-a",
                "page": page, "content": "exact body A"}
        variants = (
            ("same", deepcopy(base)),
            ("other-skill", {**base, "skill_id": "skill-b"}),
            ("other-file", {**base, "path": "production/SKILL.md"}),
            ("other-version", {**base, "digest": "version-b"}),
            ("other-page", {**base, "page": {**page, "offset": 2000}}),
            ("other-body", {**base, "content": "exact body B"}),
        )
        messages = []
        for call_id, data in (("read-first", base), *(variants[0:1]), *variants[1:]):
            messages.extend([
                {"role": "assistant", "tool_calls": [{"id": call_id, "type": "function",
                 "function": {"name": "get_producer_skill", "arguments": json.dumps({
                     "skill_id": data["skill_id"], "path": data["path"],
                     "offset": data["page"]["offset"], "limit": data["page"]["limit"]})}}]},
                {"role": "tool", "tool_call_id": call_id, "content": json.dumps(data)},
            ])

        projected = project_tool_messages(messages)
        views = [json.loads(projected[index]["content"]) for index in range(1, len(projected), 2)]

        self.assertIn("content", views[0])
        self.assertEqual(views[1]["content_ref"], {"tool_call_id": "read-first", "field": "content"})
        for view in views[2:]:
            self.assertIn("content", view)
            self.assertNotIn("content_ref", view)

        # The first full body may be outside a compacted request; a lone later read stays full.
        later_only = project_tool_messages([
            {"role": "assistant", "tool_calls": [{"id": "later", "type": "function",
             "function": {"name": "get_producer_skill", "arguments": json.dumps({"skill_id": "skill-a"})}}]},
            {"role": "tool", "tool_call_id": "later", "content": json.dumps(base)},
        ])
        self.assertEqual(json.loads(later_only[1]["content"])["content"], "exact body A")

    def test_reused_tool_call_id_does_not_create_cross_step_references(self):
        args = json.dumps({"skill_id": "skill-a", "path": "mind/SKILL.md"})
        body = json.dumps({"skill_id": "skill-a", "path": "mind/SKILL.md",
                           "digest": "digest-1", "content": "full body"})
        messages = [
            {"role": "assistant", "reasoning_content": "first reasoning",
             "tool_calls": [{"id": "legacy-id", "type": "function",
                             "function": {"name": "get_producer_skill", "arguments": args}}]},
            {"role": "tool", "tool_call_id": "legacy-id", "content": body},
            {"role": "assistant", "reasoning_content": "second reasoning",
             "tool_calls": [{"id": "legacy-id", "type": "function",
                             "function": {"name": "get_producer_skill", "arguments": args}}]},
            {"role": "tool", "tool_call_id": "legacy-id", "content": body},
        ]

        projected = project_tool_messages(messages)

        for index in (1, 3):
            view = json.loads(projected[index]["content"])
            self.assertEqual(view["content"], "full body")
            self.assertNotIn("content_ref", view)
        self.assertEqual(projected[0]["reasoning_content"], "first reasoning")
        self.assertEqual(projected[2]["reasoning_content"], "second reasoning")
        self.assertEqual(projected[0]["tool_calls"], messages[0]["tool_calls"])
        self.assertEqual(projected[2]["tool_calls"], messages[2]["tool_calls"])

    def test_version_errors_are_never_deduplicated_as_skill_content(self):
        error = '[tool_error type=skill_version_conflict]\n' + json.dumps({
            "error": "version_conflict", "message": "重新读取当前版本。",
            "skill_id": "skill-a", "current_digest": "digest-2",
        }, ensure_ascii=False)
        args = json.dumps({"skill_id": "skill-a", "path": "mind/SKILL.md"})
        messages = []
        for call_id in ("error-1", "error-2"):
            messages.extend([
                {"role": "assistant", "tool_calls": [{"id": call_id, "type": "function",
                 "function": {"name": "get_producer_skill", "arguments": args}}]},
                {"role": "tool", "tool_call_id": call_id, "content": error},
            ])

        projected = project_tool_messages(messages)

        for index in (1, 3):
            self.assertTrue(projected[index]["content"].startswith(
                "[tool_error type=skill_version_conflict]"))
            self.assertNotIn("content_ref", projected[index]["content"])
            self.assertIn("current_digest", projected[index]["content"])

    def test_failed_extraction_reads_do_not_gain_text_references(self):
        failed = '[tool_error type=temporary_read_error]\n' + json.dumps({
            "id": "job-a", "status": "unknown", "digest": "digest-a",
            "error": "read unavailable", "message": "稍后重新查询。",
            "source_text": "same source", "instruction": "same requirement",
        }, ensure_ascii=False)
        args = json.dumps({"job_id": "job-a"})
        messages = []
        for call_id in ("failed-1", "failed-2"):
            messages.extend([
                {"role": "assistant", "tool_calls": [{"id": call_id, "type": "function",
                 "function": {"name": "get_skill_extraction", "arguments": args}}]},
                {"role": "tool", "tool_call_id": call_id, "content": failed},
            ])

        projected = project_tool_messages(messages)

        for index in (1, 3):
            self.assertTrue(projected[index]["content"].startswith(
                "[tool_error type=temporary_read_error]"))
            self.assertNotIn("source_text_ref", projected[index]["content"])
            self.assertNotIn("instruction_ref", projected[index]["content"])
            self.assertIn("read unavailable", projected[index]["content"])


class SkillBatchProjectionTests(unittest.TestCase):
    def test_success_and_partial_batch_keep_actual_progress_without_write_echoes(self):
        from creatoros.tools.model_projection import project_model_data
        for error in (False, True):
            receipt = {"skill_id": "actual-candidate", "digest": "d" * 64,
                "status": "partial" if error else "completed", "partial": error,
                "completed": not error, "applied_count": 1, "total_count": 2,
                "outcome_unknown": error, "failed_index": 2 if error else None,
                "applied": [{"index": 1, "operation": "add_image", "path": "assets/new.png"}],
                "failed": {"index": 2, "operation": "write_text", "path": "SKILL.md"},
                "content": "Do not echo the written body", "error_type": "studio_outcome_unknown" if error else None}
            projected = project_model_data("update_producer_skill_file", receipt, is_error=error)
            for key in ("skill_id", "digest", "status", "partial", "completed", "applied_count", "total_count",
                        "outcome_unknown", "failed_index", "applied", "failed"):
                self.assertEqual(projected[key], receipt[key])
            self.assertNotIn("content", projected)


if __name__ == "__main__":
    unittest.main()
