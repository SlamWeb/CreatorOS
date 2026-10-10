"""E10 grading tests use local evidence mutations; no model or service calls."""
from copy import deepcopy
import hashlib
import json
import unittest

from creatoros.evaluation.grader_e10 import grade_e10
from creatoros.tools.model_projection import project_model_content
from tests.test_account_eval_grader import good_evidence


def e10_evidence(*, limit=20):
    evidence = good_evidence()
    series_id = "series-a"
    pending = [{"id": f"pending-{index:02}", "title": f"待选标题 {index:02}"} for index in range(21)]
    failed = {"id": "failed-run", "task_id": "failed-run", "kind": "production", "status": "failed",
              "series_id": series_id, "title": "失败作品", "url": "/runs/failed-run"}
    failed_research = {"id": "failed-batch", "task_id": "failed-batch", "kind": "research", "status": "failed",
              "series_id": series_id, "title": "调研失败项", "url": "/series/series-a?research=failed-batch"}
    evidence["oracle"]["e10"] = {"series_id": series_id, "pending": pending,
        "excluded_queued_titles": ["已入队排除项", failed["title"]], "failed_tasks": [failed, failed_research],
        "nonfailed_task_ids": ["queued-run"]}
    answer = "待选标题：\n" + "\n".join(row["title"] for row in pending) + \
        "\n失败任务：production failed-run（failed）[查看运行](/runs/failed-run)；" + \
        "research failed-batch（failed）[查看调研](/series/series-a?research=failed-batch)。"
    query = "只列当前账号四格词汇栏目的全部待选标题和失败任务；待选列表不要混入已入队标题，失败任务保留原题并给出查看入口。"
    user = {"role": "user", "content": query}
    evidence["messages"] = [user]
    first_context = deepcopy(evidence["requests"][0]["context"])
    first_context["messages"][-1] = deepcopy(user)
    first_calls = [
        {"id": "series-call", "name": "list_creator_series", "arguments": "{}"},
        {"id": "pending-first", "name": "list_series_topics", "arguments": json.dumps(
            {"series_id": series_id, "state": "pending", "offset": 0, "limit": limit})},
        {"id": "pending-second", "name": "list_series_topics", "arguments": json.dumps(
            {"series_id": series_id, "state": "pending", "offset": 20, "limit": 20})},
        {"id": "tasks-call", "name": "get_creator_tasks", "arguments": json.dumps({"series_id": series_id})},
    ]
    first_results = [
        {"creator_id": "creator-a", "creator_name": "词汇实验室",
         "items": [{"id": series_id, "name": "四格词汇"}]},
        {"items": [{**row, "series_id": series_id, "selection_state": "pending", "status": "pending_selection"}
                    for row in pending[:limit]], "page": {"offset": 0, "limit": limit, "total": len(pending)}},
        {"items": [{**row, "series_id": series_id, "selection_state": "pending", "status": "pending_selection"}
                    for row in pending[20:]], "page": {"offset": 20, "limit": 20, "total": len(pending)}},
        {"items": [
            {"id": failed["id"], "run_id": failed["task_id"], "kind": failed["kind"],
             "status": failed["status"], "series_id": series_id, "title": failed["title"], "url": failed["url"]},
            {"id": failed_research["id"], "kind": failed_research["kind"],
             "status": failed_research["status"], "series_id": series_id,
             "title": failed_research["title"], "url": failed_research["url"]},
            {"id": "queued-run", "run_id": "queued-run", "kind": "production", "status": "queued",
             "series_id": series_id, "title": "仍在队列", "url": "/runs/queued-run"}],
         "summary": {"active": 1, "awaiting_approval": 0, "failed": 2}},
    ]
    if limit >= len(pending):
        # A legal larger page is a complete read, not proof of multi-page resume.
        first_calls.pop(2)
        first_results.pop(2)
    assistant_call_message = {"role": "assistant", "content": None, "tool_calls": first_calls}
    evidence["messages"].append(assistant_call_message)
    tool_messages, snapshot_results = [], []
    for call, data in zip(first_calls, first_results):
        raw = json.dumps(data, ensure_ascii=False)
        model = project_model_content(call["name"], raw)
        tool_messages.append({"role": "tool", "tool_call_id": call["id"], "content": raw})
        snapshot_results.append({"tool_call_id": call["id"], "name": call["name"], "raw_content": raw,
                                 "content": model, "is_error": False, "error_type": None})
    evidence["messages"].extend(deepcopy(tool_messages))
    evidence["messages"].append({"role": "assistant", "content": answer})
    second_context = deepcopy(first_context)
    second_context["messages"].extend([deepcopy(assistant_call_message)])
    for call, message, row in zip(first_calls, tool_messages, snapshot_results):
        projected = deepcopy(message)
        projected["content"] = row["content"]
        second_context["messages"].append(projected)

    def api_messages(context):
        messages = deepcopy(context["messages"])
        for message in messages:
            if message.get("role") == "assistant" and message.get("tool_calls"):
                message["tool_calls"] = [{"id": call["id"], "type": "function", "function": {
                    "name": call["name"], "arguments": call["arguments"]}}
                    for call in message["tool_calls"]]
        return messages

    schemas = deepcopy(first_context["tools"])
    first_wire_calls = [{"index": index, "id": call["id"], "function": {
        "name": call["name"], "arguments": call["arguments"]}} for index, call in enumerate(first_calls)]
    evidence["requests"] = [
        {"method": "stream", "trace_request_id": "e10-r1", "context": first_context,
         "finish_reason": "tool_calls", "events": [
             {"type": "ToolCallDelta", "index": index, **call} for index, call in enumerate(first_calls)] +
             [{"type": "StreamEnd", "finish_reason": "tool_calls", "usage": None}]},
        {"method": "stream", "trace_request_id": "e10-r2", "context": second_context,
         "finish_reason": "stop", "events": [{"type": "TextDelta", "content": answer},
             {"type": "StreamEnd", "finish_reason": "stop", "usage": None}]},
    ]
    evidence["transport"] = [
        {"trace_request_id": "e10-r1", "request": {"messages": api_messages(first_context), "tools": schemas, "stream": True},
         "finish_reason": "tool_calls", "public_outputs": [{"choices": [{"index": 0, "finish_reason": "tool_calls",
             "output": {"content": None, "tool_calls": first_wire_calls}}]}]},
        {"trace_request_id": "e10-r2", "request": {"messages": api_messages(second_context), "tools": schemas, "stream": True},
         "finish_reason": "stop", "public_outputs": [{"choices": [{"index": 0, "finish_reason": "stop",
             "output": {"content": answer, "tool_calls": None}}]}]},
    ]
    evidence["snapshots"] = [
        {"request_id": "e10-r1", "context": deepcopy(first_context),
         "response": {"role": "assistant", "content": None, "tool_calls": deepcopy(first_calls)},
         "tool_results": snapshot_results},
        {"request_id": "e10-r2", "context": deepcopy(second_context),
         "response": {"role": "assistant", "content": answer}, "tool_results": []},
    ]
    evidence["trace"] = [
        {"event": "finished", "sent": True, "snapshot_available": True, "request_id": "e10-r1", "request_kind": "main"},
        {"event": "finished", "sent": True, "snapshot_available": True, "request_id": "e10-r2", "request_kind": "main"},
    ]
    evidence["final_answer"] = answer
    return evidence


def check(result, identifier):
    return next(row["status"] for row in result["checks"] if row["id"] == identifier)


def set_answer(evidence, answer):
    """Keep all recorded views consistent while changing only answer semantics."""
    evidence["final_answer"] = answer
    evidence["messages"][-1]["content"] = answer
    evidence["requests"][1]["events"][0]["content"] = answer
    evidence["snapshots"][1]["response"]["content"] = answer
    evidence["transport"][1]["public_outputs"][0]["choices"][0]["output"]["content"] = answer


class E10GraderTests(unittest.TestCase):
    def test_complete_pages_projection_and_scoped_failed_task_pass(self):
        result = grade_e10(e10_evidence())
        self.assertEqual(result["grader_version"], "e10-v4-counted-sections")
        self.assertEqual(result["auto_status"], "passed", result["checks"])
        for identifier in ("tool_protocol", "correct_object", "complete_filter", "failed_tasks", "task_answer"):
            self.assertEqual(check(result, identifier), "passed", identifier)

    def test_legal_one_page_limit_100_has_same_complete_coverage(self):
        result = grade_e10(e10_evidence(limit=100))
        self.assertEqual(result["auto_status"], "passed", result["checks"])
        self.assertEqual(check(result, "complete_filter"), "passed")

    def test_queued_failed_title_is_allowed_in_failed_task_section(self):
        evidence = e10_evidence()
        set_answer(evidence, evidence["final_answer"].replace(
            "production failed-run（failed）", "失败作品（failed）"))
        result = grade_e10(evidence)
        self.assertEqual(check(result, "complete_filter"), "passed")
        self.assertEqual(check(result, "task_answer"), "passed")

    def test_counted_failed_heading_does_not_extend_the_pending_section(self):
        for label in ("**失败任务（2 条）**", "## 失败任务(2项)", "2. 失败任务（2个）："):
            with self.subTest(label=label):
                evidence = e10_evidence()
                answer = evidence["final_answer"].replace("失败任务：production failed-run（failed）",
                    label + "\n- 生产任务「失败作品」")
                set_answer(evidence, answer)
                result = grade_e10(evidence)
                self.assertEqual(check(result, "complete_filter"), "passed")
                self.assertEqual(check(result, "task_answer"), "passed")
                # A heading is not a permission to list arbitrary queued work.
                set_answer(evidence, answer + "\n- 已入队排除项")
                self.assertEqual(check(grade_e10(evidence), "task_answer"), "failed")

    def test_counted_pending_heading_still_rejects_a_linked_queued_title(self):
        evidence = e10_evidence()
        answer = evidence["final_answer"].replace("待选标题：", "**待选标题（21 条）**").replace(
            "待选标题 20", "待选标题 20\n失败作品 [查看运行](/runs/failed-run)")
        set_answer(evidence, answer)
        self.assertEqual(check(grade_e10(evidence), "complete_filter"), "failed")

    def test_same_failed_title_must_not_be_mixed_into_pending_list(self):
        evidence = e10_evidence()
        set_answer(evidence, evidence["final_answer"].replace("待选标题 20", "待选标题 20\n失败作品"))
        result = grade_e10(evidence)
        self.assertEqual(check(result, "complete_filter"), "failed")
        self.assertEqual(result["dimensions"]["task_success"], "failed")

    def test_plain_task_line_with_real_link_needs_no_fixed_heading(self):
        evidence = e10_evidence()
        set_answer(evidence, evidence["final_answer"].replace("待选标题：\n", "").replace(
            "失败任务：production failed-run（failed）", "失败作品（failed）"))
        self.assertEqual(check(grade_e10(evidence), "complete_filter"), "passed")

    def test_unscoped_queued_mention_is_reviewed_not_whole_answer_banned(self):
        evidence = e10_evidence()
        set_answer(evidence, "背景：已入队排除项。\n" + evidence["final_answer"])
        result = grade_e10(evidence)
        self.assertEqual(check(result, "complete_filter"), "needs_review")
        self.assertEqual(result["dimensions"]["task_success"], "needs_review")

    def test_queued_title_under_pending_heading_fails_even_with_task_link(self):
        evidence = e10_evidence()
        set_answer(evidence, evidence["final_answer"].replace(
            "待选标题 20", "待选标题 20\n失败作品 [查看运行](/runs/failed-run)"))
        self.assertEqual(check(grade_e10(evidence), "complete_filter"), "failed")

    def test_bad_raw_and_projected_views_fail_shared_tool_protocol(self):
        for field in ("raw_content", "content"):
            with self.subTest(field=field):
                evidence = e10_evidence()
                row = evidence["snapshots"][0]["tool_results"][1]
                row[field] = "{}"
                result = grade_e10(evidence)
                self.assertEqual(check(result, "tool_protocol"), "failed")
                self.assertNotEqual(result["auto_status"], "passed")

    def test_markdown_emphasis_does_not_break_literal_title_coverage(self):
        evidence = e10_evidence()
        evidence["final_answer"] = evidence["final_answer"].replace("待选标题 00", "**待选标题 00**")
        evidence["messages"][-1]["content"] = evidence["final_answer"]
        evidence["requests"][1]["events"][0]["content"] = evidence["final_answer"]
        evidence["snapshots"][1]["response"]["content"] = evidence["final_answer"]
        evidence["transport"][1]["public_outputs"][0]["choices"][0]["output"]["content"] = evidence["final_answer"]
        result = grade_e10(evidence)
        self.assertEqual(check(result, "complete_filter"), "passed")

    def test_large_result_marker_requires_matching_index_description_and_raw_archive(self):
        evidence = e10_evidence()
        call_id = "pending-first"
        row = next(row for row in evidence["snapshots"][0]["tool_results"]
                   if row["tool_call_id"] == call_id)
        raw = row["raw_content"]
        digest = hashlib.sha256((call_id + "\0" + raw).encode()).hexdigest()
        data = json.loads(raw)
        facts = {"items_count": len(data["items"])}
        # Externalizer preserves these selected fields, then appends list counts.
        description = f"list_series_topics 返回记录，{len(raw)} 字符；历史字段={json.dumps(facts, ensure_ascii=False)}"
        folder = "messages.tool-results"
        evidence["archives"] = {
            folder + "/index.json": json.dumps({digest: {"result_ref": call_id,
                "description": description, "path": digest + ".txt"}}, ensure_ascii=False),
            folder + "/" + digest + ".txt": raw,
        }
        marker = (f"[external tool result] {description}\nresult_ref={call_id}\n"
                  f"用 read_file 读取原文：C:/eval/session/{folder}/{digest}.txt（unit=chars，可分页）")
        for context in (evidence["requests"][1]["context"], evidence["snapshots"][1]["context"]):
            for message in context["messages"]:
                if message.get("role") == "tool" and message.get("tool_call_id") == call_id:
                    message["content"] = marker
        for message in evidence["transport"][1]["request"]["messages"]:
            if message.get("role") == "tool" and message.get("tool_call_id") == call_id:
                message["content"] = marker
        self.assertEqual(check(grade_e10(evidence), "tool_protocol"), "passed")
        for messages in (evidence["requests"][1]["context"]["messages"],
                         evidence["snapshots"][1]["context"]["messages"],
                         evidence["transport"][1]["request"]["messages"]):
            for message in messages:
                if message.get("role") == "tool" and message.get("tool_call_id") == call_id:
                    message["content"] = marker.replace(description, "unverified description")
        self.assertEqual(check(grade_e10(evidence), "tool_protocol"), "failed")

    def test_missing_second_page_title_fails_completeness(self):
        evidence = e10_evidence()
        evidence["final_answer"] = evidence["final_answer"].replace("待选标题 20", "漏掉了这一项")
        evidence["messages"][-1]["content"] = evidence["final_answer"]
        evidence["requests"][1]["events"][0]["content"] = evidence["final_answer"]
        evidence["snapshots"][1]["response"]["content"] = evidence["final_answer"]
        evidence["transport"][1]["public_outputs"][0]["choices"][0]["output"]["content"] = evidence["final_answer"]
        result = grade_e10(evidence)
        self.assertEqual(check(result, "complete_filter"), "failed")

    def test_wrong_series_and_queued_title_are_rejected(self):
        evidence = e10_evidence()
        call = next(call for message in evidence["messages"] for call in message.get("tool_calls", [])
                    if call["name"] == "list_series_topics")
        call["arguments"] = json.dumps({"series_id": "series-b", "state": "pending", "offset": 0, "limit": 20})
        evidence["final_answer"] += "\n已入队排除项"
        evidence["messages"][-1]["content"] = evidence["final_answer"]
        evidence["requests"][1]["events"][0]["content"] = evidence["final_answer"]
        evidence["snapshots"][1]["response"]["content"] = evidence["final_answer"]
        evidence["transport"][1]["public_outputs"][0]["choices"][0]["output"]["content"] = evidence["final_answer"]
        result = grade_e10(evidence)
        self.assertEqual(check(result, "correct_object"), "failed")
        self.assertEqual(check(result, "complete_filter"), "failed")

        # Correct reads plus an excluded title must fail task success too, not
        # merely a protocol dimension inherited from a wrong-scope call.
        evidence = e10_evidence()
        evidence["final_answer"] += "\n已入队排除项"
        evidence["messages"][-1]["content"] = evidence["final_answer"]
        evidence["requests"][1]["events"][0]["content"] = evidence["final_answer"]
        evidence["snapshots"][1]["response"]["content"] = evidence["final_answer"]
        evidence["transport"][1]["public_outputs"][0]["choices"][0]["output"]["content"] = evidence["final_answer"]
        result = grade_e10(evidence)
        self.assertEqual(check(result, "correct_object"), "passed")
        self.assertEqual(result["dimensions"]["task_success"], "failed")

    def test_failed_task_must_be_read_from_scoped_task_result_and_described(self):
        evidence = e10_evidence()
        call = next(call for message in evidence["messages"] for call in message.get("tool_calls", [])
                    if call["name"] == "get_creator_tasks")
        call["arguments"] = json.dumps({"series_id": "series-b"})
        result = grade_e10(evidence)
        self.assertEqual(check(result, "failed_tasks"), "failed")
        evidence = e10_evidence()
        evidence["final_answer"] = evidence["final_answer"].replace("(/runs/failed-run)", "(/runs/unknown)")
        evidence["messages"][-1]["content"] = evidence["final_answer"]
        evidence["requests"][1]["events"][0]["content"] = evidence["final_answer"]
        evidence["snapshots"][1]["response"]["content"] = evidence["final_answer"]
        evidence["transport"][1]["public_outputs"][0]["choices"][0]["output"]["content"] = evidence["final_answer"]
        result = grade_e10(evidence)
        self.assertEqual(check(result, "task_answer"), "failed")

    def test_absolute_lookalike_host_does_not_match_relative_tool_url(self):
        evidence = e10_evidence()
        evidence["final_answer"] = evidence["final_answer"].replace(
            "(/runs/failed-run)", "(https://creatoros.example.com/runs/failed-run)")
        evidence["messages"][-1]["content"] = evidence["final_answer"]
        evidence["requests"][1]["events"][0]["content"] = evidence["final_answer"]
        evidence["snapshots"][1]["response"]["content"] = evidence["final_answer"]
        evidence["transport"][1]["public_outputs"][0]["choices"][0]["output"]["content"] = evidence["final_answer"]
        result = grade_e10(evidence)
        self.assertEqual(check(result, "task_answer"), "failed")

    def test_exact_relative_markdown_destinations_match_tool_urls(self):
        evidence = e10_evidence()
        self.assertEqual(check(grade_e10(evidence), "task_answer"), "passed")


if __name__ == "__main__":
    unittest.main()
