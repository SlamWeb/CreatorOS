"""E10 checks for scoped, complete pending-topic and failed-task reads."""
from __future__ import annotations

import json
import re

from .grader import grade_read_only


GRADER_VERSION = "e10-v4-counted-sections"
_FAILED_WORDS = {"failed": ("failed", "失败"), "interrupted": ("interrupted", "中断")}


def _merge(*statuses):
    return "failed" if "failed" in statuses else "needs_review" if "needs_review" in statuses else "passed"


def _tool_evidence(evidence):
    calls = {}
    for message in evidence.get("messages", []):
        for call in message.get("tool_calls", []):
            calls[call.get("id")] = call
    results = {}
    for snapshot in evidence.get("snapshots", []):
        for row in snapshot.get("tool_results", []):
            results[row.get("tool_call_id")] = row
    return calls, results


def _decode_call(call):
    try:
        arguments = json.loads(call.get("arguments") or "{}")
    except (TypeError, ValueError):
        return None
    return arguments if isinstance(arguments, dict) else None


def _decode_result(row):
    raw = row.get("raw_content")
    if not isinstance(raw, str):
        return None
    if raw.startswith("[tool_error "):
        _, separator, raw = raw.partition("\n")
        if not separator:
            return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _markdown_link_destinations(answer):
    """Return actual Markdown link destinations, not URL-looking substrings."""
    destinations = set()
    # The tool URLs used here have no spaces or closing parentheses. Angle-bracket
    # destinations are supported too; optional Markdown link titles are ignored.
    pattern = re.compile(r"\[[^\]]*\]\((<[^>]+>|(?:\\.|[^\s)])+)(?:\s+[^)]*)?\)")
    for match in pattern.finditer(answer):
        destination = match.group(1)
        if destination.startswith("<") and destination.endswith(">"):
            destination = destination[1:-1]
        destinations.add(destination)
    return destinations


def _reply_filter_scopes(answer, e10):
    """Check list membership, not a whole-answer ban on queued-title text.

    Explicit pending/failed headings give deterministic scopes. A failed title
    associated with its authoritative link also has a valid task context. Other
    ambiguous occurrences require reading assessment; never guess them passed.
    No particular headings, formatting, or tool-call order are required.
    """
    pending_titles = {row["title"] for row in e10["pending"]}
    failed = {row["title"]: row["url"] for row in e10["failed_tasks"]}
    pending_status = task_status = "passed"
    mode = None
    explicit_mode = None
    for paragraph in re.split(r"\n\s*\n", answer):
        for line in paragraph.splitlines():
            text = re.sub(r"\*\*|__|`|^\s*#+\s*", "", line).strip()
            # Only section labels select a mode; a real title can contain these words.
            label = re.sub(r"^[\d一二三四五六七八九十]+[、.．)）]\s*", "", text).strip()
            if re.match(r"^(?:全部|所有|本栏目|当前栏目)?待选(?:选题|标题|列表|内容)?(?:[（(].*?[）)])?\s*(?:[:：]|$)", label):
                mode = explicit_mode = "pending"
            elif re.match(r"^(?:全部|所有)?失败(?:的)?任务(?:[（(]\s*\d+\s*(?:条|项|个)?\s*[）)])?\s*(?:[:：]|$)", label):
                mode = explicit_mode = "failed"
            has_pending_title = any(title in text for title in pending_titles)
            if has_pending_title:
                # Works for plain lists without requiring an explicit section label.
                mode = "pending"
            linked_failed_title = any(
                    title in text and url in _markdown_link_destinations(line)
                    for title, url in failed.items())
            if linked_failed_title and has_pending_title and explicit_mode != "pending":
                pending_status = _merge(pending_status, "needs_review")
            if explicit_mode != "pending" and linked_failed_title and not has_pending_title:
                mode = "failed"
            for title in e10["excluded_queued_titles"]:
                if title not in text:
                    continue
                if mode == "pending":
                    pending_status = "failed"
                elif mode == "failed":
                    if title not in failed:
                        task_status = "failed"
                elif title in failed and failed[title] in _markdown_link_destinations(paragraph):
                    continue
                else:
                    pending_status = _merge(pending_status, "needs_review")
    return pending_status, task_status


def _valid_oracle(evidence):
    oracle = evidence.get("oracle")
    e10 = oracle.get("e10") if isinstance(oracle, dict) else None
    if not isinstance(e10, dict):
        return None
    series_id = e10.get("series_id")
    pending = e10.get("pending")
    excluded = e10.get("excluded_queued_titles")
    failed = e10.get("failed_tasks")
    nonfailed = e10.get("nonfailed_task_ids")
    if not isinstance(series_id, str) or not series_id:
        return None
    if not isinstance(pending, list) or len(pending) <= 20:
        return None
    if any(not isinstance(row, dict) or not isinstance(row.get("id"), str)
           or not row["id"] or not isinstance(row.get("title"), str) or not row["title"]
           for row in pending):
        return None
    pending_ids = [row["id"] for row in pending]
    pending_titles = [row["title"] for row in pending]
    if len(set(pending_ids)) != len(pending_ids) or len(set(pending_titles)) != len(pending_titles):
        return None
    if (not isinstance(excluded, list) or not excluded
            or any(not isinstance(title, str) or not title for title in excluded)
            or set(excluded) & set(pending_titles)):
        return None
    if not isinstance(failed, list) or not failed:
        return None
    for row in failed:
        if (not isinstance(row, dict) or any(not isinstance(row.get(key), str) or not row[key]
                for key in ("id", "task_id", "kind", "status", "series_id", "title", "url"))
                or row["status"] not in _FAILED_WORDS):
            return None
    if (len({row["id"] for row in failed}) != len(failed)
            or any(row["series_id"] != series_id for row in failed)):
        return None
    if (not isinstance(nonfailed, list) or any(not isinstance(value, str) or not value for value in nonfailed)
            or len(set(nonfailed)) != len(nonfailed)
            or set(nonfailed) & {row["task_id"] for row in failed}):
        return None
    return e10


def grade_e10(evidence):
    """Use shared evidence checks, then verify E10-specific scope and content."""
    result = grade_read_only(evidence, case_id="E10")
    checks = result.setdefault("checks", [])

    def add(identifier, label, status, detail, files):
        checks.append({"id": identifier, "label": label, "status": status,
                       "detail": detail, "evidence": files})

    e10 = _valid_oracle(evidence) if isinstance(evidence, dict) else None
    if e10 is None:
        add("e10_oracle", "E10 独立 oracle 完整", "needs_review",
            "缺少合法 oracle.e10；需有超过默认二十条的 pending、queued 对照、失败及非失败任务 ID。",
            ["oracle.json"])
        for key in ("task_success", "protocol_valid"):
            result.setdefault("dimensions", {})[key] = _merge(result.get("dimensions", {}).get(key, "needs_review"), "needs_review")
        result["grader_version"] = GRADER_VERSION
        result["auto_status"] = _merge(*(result["dimensions"].values()))
        return result

    add("e10_oracle", "E10 独立 oracle 完整", "passed",
        f"{len(e10['pending'])} 个唯一待选标题，{len(e10['failed_tasks'])} 个失败任务。", ["oracle.json"])
    calls, tool_results = _tool_evidence(evidence)
    series_id = e10["series_id"]

    topic_rows = []
    topic_call_ids = []
    wrong_topic_scope = []
    for call_id, call in calls.items():
        if call.get("name") != "list_series_topics":
            continue
        topic_call_ids.append(call_id)
        args = _decode_call(call)
        if (args is None or args.get("series_id") != series_id
                or args.get("state", "all") != "pending"):
            wrong_topic_scope.append(call_id)
            continue
        row = tool_results.get(call_id)
        data = _decode_result(row) if isinstance(row, dict) else None
        page = data.get("page") if isinstance(data, dict) else None
        items = data.get("items") if isinstance(data, dict) else None
        if (not isinstance(page, dict) or not isinstance(items, list)
                or row.get("is_error") is True or page.get("offset") != args.get("offset", 0)
                or page.get("limit") != args.get("limit", 20)
                or type(page.get("total")) is not int):
            wrong_topic_scope.append(call_id)
            continue
        topic_rows.append((page, items))

    expected_pending = {row["id"]: row["title"] for row in e10["pending"]}
    complete = bool(topic_call_ids) and not wrong_topic_scope and bool(topic_rows)
    totals = {page["total"] for page, _ in topic_rows}
    complete &= totals == {len(expected_pending)}
    indexes = {}
    for page, items in topic_rows:
        offset, limit, total = page["offset"], page["limit"], page["total"]
        if (type(offset) is not int or type(limit) is not int or offset < 0 or limit < 1
                or offset >= total and total != 0 or len(items) != min(limit, max(0, total - offset))):
            complete = False
            continue
        for position, item in enumerate(items):
            if not isinstance(item, dict) or item.get("selection_state") != "pending":
                complete = False
                continue
            identifier = item.get("id")
            if not isinstance(identifier, str) or identifier in indexes:
                complete = False
                continue
            # Retain page offsets so holes and overlapping ranges cannot be hidden by ID totals.
            indexes[identifier] = (item.get("title"), offset + position)
    complete &= {key: value[0] for key, value in indexes.items()} == expected_pending
    positions = [value[1] for value in indexes.values()]
    complete &= sorted(positions) == list(range(len(expected_pending)))
    add("correct_object", "待选查询使用当前账号的目标栏目", "failed" if wrong_topic_scope or not topic_call_ids else "passed",
        f"目标栏目 {series_id}；错误或不合筛选条件的调用 {len(wrong_topic_scope)}。", ["messages.json", "snapshots.json", "oracle.json"])

    answer = evidence.get("final_answer") if isinstance(evidence, dict) else None
    answer_text = answer if isinstance(answer, str) else ""
    readable_answer = re.sub(r"\*\*|__|`", "", answer_text)
    all_titles = all(title in readable_answer for title in expected_pending.values())
    pending_scope, reply_task_scope = _reply_filter_scopes(readable_answer, e10)
    complete &= all_titles
    filter_status = _merge("passed" if complete else "failed", pending_scope)
    add("complete_filter", "待选标题完整覆盖且列表未混入已入队标题",
        filter_status,
        f"目标 {len(expected_pending)} 条；返回 {len(indexes)} 条；答复覆盖全部标题={all_titles}；待选列表范围={pending_scope}。失败任务允许使用其已入队标题；合法一次全量或分页均可。",
        ["messages.json", "snapshots.json", "answer.txt", "oracle.json"])
    task_call_ids = []
    task_rows = []
    task_scope_ok = True
    for call_id, call in calls.items():
        if call.get("name") != "get_creator_tasks":
            continue
        task_call_ids.append(call_id)
        args = _decode_call(call)
        if args is None or args.get("series_id") != series_id:
            task_scope_ok = False
            continue
        row = tool_results.get(call_id)
        data = _decode_result(row) if isinstance(row, dict) else None
        items = data.get("items") if isinstance(data, dict) else None
        if row is None or row.get("is_error") is True or not isinstance(items, list):
            task_scope_ok = False
            continue
        task_rows.extend(item for item in items if isinstance(item, dict))
    failed_by_id = {row["task_id"]: row for row in e10["failed_tasks"]}
    observed_failed = {}
    observed_ids = set()
    for item in task_rows:
        identifier = item.get("run_id") or item.get("id")
        if isinstance(identifier, str):
            observed_ids.add(identifier)
        if item.get("series_id") != series_id:
            task_scope_ok = False
        if item.get("status") in _FAILED_WORDS:
            if isinstance(identifier, str):
                observed_failed[identifier] = item
    matched_failed_keys = set()
    for expected in failed_by_id.values():
        match_key = expected["id"] if expected["id"] in observed_failed else expected["task_id"]
        actual = observed_failed.get(match_key)
        if actual is not None:
            matched_failed_keys.add(match_key)
        if (actual is None or actual.get("id") != expected["id"] or actual.get("kind") != expected["kind"]
                or actual.get("status") != expected["status"]
                or actual.get("series_id") != expected["series_id"]
                or actual.get("title") != expected["title"] or actual.get("url") != expected["url"]):
            task_scope_ok = False
    task_scope_ok &= set(observed_failed) == matched_failed_keys
    nonfailed_present = set(e10["nonfailed_task_ids"]).issubset(observed_ids)
    task_scope_ok &= bool(task_call_ids) and nonfailed_present
    link_destinations = _markdown_link_destinations(answer_text)
    # Tool-returned URLs are authoritative. Relative URLs must match exactly;
    # absolute URLs are accepted only when the tool itself returned that exact URL.
    # This evidence does not capture the test host base_url, so never infer a host.
    task_answer_ok = all(row["url"] in link_destinations for row in e10["failed_tasks"])
    task_answer_ok &= all(identifier not in answer_text for identifier in e10["nonfailed_task_ids"])
    task_answer_ok &= reply_task_scope != "failed"
    add("failed_tasks", "读取了真实且限定栏目的失败任务", "passed" if task_scope_ok else "failed",
        f"目标失败任务 {len(failed_by_id)}；观察到 {len(observed_failed)} 个失败项；非失败对照全部存在={nonfailed_present}。",
        ["messages.json", "snapshots.json", "oracle.json"])
    add("task_answer", "失败任务链接完整且未混入非失败任务", "passed" if task_answer_ok else "failed",
        "每个实际失败任务的工具返回 URL 都作为 Markdown 链接目标精确出现，非失败对照 ID 不出现在答复中；状态语义由人工评估。",
        ["answer.txt", "snapshots.json", "oracle.json"])

    dimensions = result.setdefault("dimensions", {})
    dimensions["task_success"] = _merge(dimensions.get("task_success", "needs_review"), filter_status,
        "passed" if task_scope_ok and task_answer_ok else "failed")
    dimensions["protocol_valid"] = _merge(dimensions.get("protocol_valid", "needs_review"),
        "failed" if any(check["status"] == "failed" for check in checks
                        if check["id"] in {"correct_object", "complete_filter", "failed_tasks"}) else "passed")
    result["grader_version"] = GRADER_VERSION
    result["auto_status"] = _merge(*(dimensions.values()))
    return result
