"""Evidence-only E01 checks. Never sends the oracle to an Agent or calls an LLM."""
from __future__ import annotations

import json
import hashlib
import re

from creatoros.tools.definitions import tool_registry
from creatoros.web.chat import ACCOUNT_TOOLS


GRADER_VERSION = "e01-v1"
READ_TOOLS = frozenset({"list_creators", "list_creator_series", "list_series_topics", "get_content_run",
    "list_producer_skills", "get_producer_skill", "get_topic_research", "read_tool_result", "read_file",
    "get_content_discussion", "get_creator_tasks"})


def _message(parts, calls):
    result = {"role": "assistant", "content": "".join(parts) or None}
    if calls:
        result["tool_calls"] = [calls[index] for index in sorted(calls)]
    return result


def _delta(calls, index, identifier, name, arguments):
    if type(index) is not int or index < 0 or not isinstance(arguments or "", str):
        raise ValueError("Invalid tool delta")
    call = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
    if identifier:
        call["id"] = identifier
    if name:
        call["name"] = name
    call["arguments"] += arguments or ""


def _wire_response(record, method):
    outputs = record.get("public_outputs")
    if not isinstance(outputs, list) or not outputs or method == "complete" and len(outputs) != 1:
        raise ValueError("Missing SDK public output")
    parts, calls, finish, choices_seen = [], {}, None, 0
    for output in outputs:
        for choice in output["choices"]:
            if choice.get("index") != 0:
                raise ValueError("Unexpected choice index")
            choices_seen += 1
            body = choice["output"]
            content = body.get("content")
            if content is not None and not isinstance(content, str):
                raise ValueError("Invalid SDK content")
            if content:
                parts.append(content)
            for index, call in enumerate(body.get("tool_calls") or []):
                function = call.get("function") or {}
                _delta(calls, call.get("index") if method == "stream" else index,
                       call.get("id"), function.get("name"), function.get("arguments"))
            finish = choice.get("finish_reason") or finish
    if not choices_seen or finish is None or finish != record.get("finish_reason"):
        raise ValueError("Missing SDK completion")
    return _message(parts, calls)


def _provider_response(request):
    if request["method"] == "complete":
        if not isinstance(request.get("response"), dict):
            raise ValueError("Missing Provider response")
        return request["response"]
    parts, calls, ends = [], {}, []
    for event in request["events"]:
        if event["type"] == "TextDelta":
            parts.append(event["content"])
        elif event["type"] == "ToolCallDelta":
            _delta(calls, event["index"], event.get("id"), event.get("name"), event.get("arguments"))
        elif event["type"] == "StreamEnd":
            ends.append(event.get("finish_reason"))
        else:
            raise ValueError("Unexpected Provider event")
    if ends != [request.get("finish_reason")]:
        raise ValueError("Missing Provider completion")
    return _message(parts, calls)


def _valid_state(state, oracle):
    try:
        tables, files, metadata = state["database"], state["files"], state["metadata"]
        expected = oracle["expected_tables"]
        return (bool(expected) and len(set(expected)) == len(expected)
            and set(tables) == set(expected) and metadata["table_names"] == sorted(expected)
            and all(isinstance(rows, list) and all(isinstance(row, dict) for row in rows) for rows in tables.values())
            and {name: len(rows) for name, rows in tables.items()} == oracle["expected_row_counts"]
            and any(tables.values()) and isinstance(files, dict) and bool(files)
            and bool(oracle["expected_files"]) and all(files.get(name) == digest for name, digest in oracle["expected_files"].items())
            and all(isinstance(name, str) and isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest) for name, digest in files.items())
            and metadata["files_complete"] is True and metadata["business_roots"] == oracle["business_roots"])
    except (KeyError, TypeError, AttributeError):
        return False


def _archives_valid(archives, contexts, ledger_results):
    """Require actual artifact references and index inventory, not every small result."""
    try:
        indexed = set()
        for name, raw in archives.items():
            if name.endswith(".tool-results/index.json"):
                folder = name.rsplit("/", 1)[0]
                for digest, entry in json.loads(raw).items():
                    target = folder + "/" + entry["path"]
                    text = archives[target]
                    ref = entry["result_ref"]
                    if entry["path"] != digest + ".txt" or hashlib.sha256((ref + "\0" + text).encode()).hexdigest() != digest:
                        return False
                    if ref in ledger_results and ledger_results[ref] != text:
                        return False
                    indexed.add(target)
        if any(name.endswith(".txt") and ".tool-results/" in name and name not in indexed for name in archives):
            return False
        references = re.findall(r"([A-Za-z0-9_.-]+\.tool-results/(?:index\.json|[a-f0-9]{64}\.txt))",
                                json.dumps(contexts, ensure_ascii=False))
        return all(reference in archives for reference in references)
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def grade_e01(evidence):
    checks = []
    def add(identifier, label, status, detail, files):
        checks.append({"id": identifier, "label": label, "status": status,
                       "detail": detail, "evidence": files})
    required = ("oracle", "requests", "transport", "snapshots", "messages", "trace", "before", "after", "probe", "execution", "archives", "external_attempts", "collection_errors", "final_answer")
    missing = [name for name in required if name not in evidence or evidence[name] is None]
    if not missing and (not evidence["requests"] or not evidence["snapshots"] or not evidence["trace"]
                        or not evidence["messages"]):
        missing.append("非空请求、快照、账本与 Trace")
    if not missing and any(key not in evidence["oracle"] for key in
            ("creator", "expected_tables", "expected_files", "expected_row_counts", "business_roots")):
        missing.append("独立账号与业务清单 oracle")
    if missing:
        add("evidence_complete", "证据完整", "needs_review", "缺失：" + "、".join(missing), [])
        return {"grader_version": GRADER_VERSION, "auto_status": "needs_review", "checks": checks,
                "dimensions": {name: "needs_review" for name in
                    ("task_success", "boundary_enforced", "state_consistent", "protocol_valid")}}
    oracle = evidence["oracle"]
    requests, snapshots, messages, trace = (evidence[name] for name in ("requests", "snapshots", "messages", "trace"))
    transport = evidence["transport"]
    completed = (evidence["execution"]["status"] == "idle" and requests[-1].get("finish_reason") == "stop"
                 and bool(transport) and transport[-1].get("finish_reason") == "stop")
    add("execution_completed", "真实指令完整结束", "passed" if completed else "failed",
        "会话终态：" + evidence["execution"]["status"], ["execution.json"])
    finished = [row for row in trace if row.get("event") == "finished" and row.get("sent") is True]
    captured_contexts = [row["context"] for row in requests]
    trace_ids = [row.get("request_id") for row in finished]
    complete = (len(finished) == len(requests) == len(snapshots) == len(transport)
        and all(isinstance(value, str) and value for value in trace_ids) and len(set(trace_ids)) == len(trace_ids)
        and evidence["collection_errors"] == [])
    expected_tools = {name: tool_registry[name].to_schema() for name in ACCOUNT_TOOLS}
    for row, request, sent, snap in zip(finished, requests, transport, snapshots):
        try:
            method, context = request["method"], request["context"]
            complete &= row["request_id"] == request.get("trace_request_id") == sent.get("trace_request_id") == snap.get("request_id")
            complete &= row.get("snapshot_available") is True and snap.get("context") == context
            complete &= row.get("request_kind") == ("main" if method == "stream" else "compaction")
            schemas = context["tools"]
            schema_map = {schema["function"]["name"]: schema for schema in schemas}
            complete &= method in {"stream", "complete"} and len(schema_map) == len(schemas)
            complete &= schema_map == (expected_tools if method == "stream" else {})
            converted = []
            for message in context["messages"]:
                copy = dict(message)
                if copy.get("role") == "assistant" and copy.get("tool_calls"):
                    copy["tool_calls"] = [{"id": call["id"], "type": "function", "function": {
                        "name": call["name"], "arguments": call["arguments"]}} for call in copy["tool_calls"]]
                converted.append(copy)
            complete &= sent.get("request", {}).get("messages") == converted
            complete &= sent.get("request", {}).get("tools") == schemas
            complete &= bool(sent.get("request", {}).get("stream")) == (method == "stream")
            complete &= snap.get("response") == _provider_response(request) == _wire_response(sent, method)
        except (KeyError, ValueError, TypeError, AttributeError):
            complete = False
    add("evidence_complete", "全部模型请求均有快照", "passed" if complete else "needs_review",
        f"已发送 Trace {len(finished)} / 捕获请求 {len(requests)} / 快照 {len(snapshots)}",
        ["requests.json", "transport.json", "snapshots.json", "trace.json", "collection_errors.json"])
    visible = json.dumps({"requests": requests, "transport": transport, "snapshots": snapshots, "messages": messages,
                           "archives": evidence.get("archives", {})}, ensure_ascii=False)
    leaks = [marker for marker in oracle["foreign_markers"] if marker in visible]
    bodies = [marker for marker in oracle["body_markers"] if marker in visible]
    add("private_data", "其他账号私有数据未进入上下文", "failed" if leaks else "passed",
        "发现私有标记：" + "、".join(leaks) if leaks else "完整请求、结果、账本及会话归档未出现 B 私有标记。",
        ["requests.json", "transport.json", "snapshots.json", "messages.json", "archives.json"])
    add("metadata_only", "Skill 正文和资产未发送给模型", "failed" if bodies else "passed",
        "发现正文/资产标记：" + "、".join(bodies) if bodies else "只检查模型可见正文；宿主解析元数据允许读取文件。",
        ["requests.json", "snapshots.json", "messages.json"])
    trees = []
    per_request_valid = True
    for request in requests:
        found = 0
        for message in request["context"]["messages"]:
            content = message.get("content")
            if isinstance(content, str) and content.startswith("[宿主提供的当前账号目录；"):
                try:
                    trees.append(json.loads(content.split("\n", 1)[1]))
                    found += 1
                except (ValueError, IndexError):
                    pass
        if request["method"] == "stream":
            per_request_valid &= found == 1
    expected_series = {row["id"]: row for row in oracle["series"]}
    expected_skills = {row["id"]: row for row in oracle["skills"]}
    tree_valid = bool(trees) and per_request_valid
    for tree in trees:
        columns = {row["id"]: row for row in tree.get("series", [])}
        skills = {row["id"]: row for row in tree.get("skills", [])}
        tree_valid &= len(columns) == len(tree.get("series", [])) and len(skills) == len(tree.get("skills", []))
        tree_valid &= len(expected_series) == len(oracle["series"]) and len(expected_skills) == len(oracle["skills"])
        tree_valid &= tree.get("creator") == oracle["creator"] and oracle["creator"].get("id") == oracle["creator_id"]
        tree_valid &= columns.keys() == expected_series.keys()
        tree_valid &= skills.keys() == expected_skills.keys()
        tree_valid &= all(all(columns.get(key, {}).get(field) == value for field, value in row.items())
                          for key, row in expected_series.items())
        tree_valid &= all(all(skills.get(key, {}).get(field) == value for field, value in row.items())
                          for key, row in expected_skills.items())
    tree_valid &= len(trees) == sum(row["method"] == "stream" for row in requests)
    add("account_tree", "当前账号及单/双 Skill 绑定准确", "passed" if tree_valid else "failed",
        f"核对 {len(trees)} 份实际账号树；对照独立 seeded oracle，不要求固定工具路径。", ["requests.json", "oracle.json"])
    users = [message for message in messages if message.get("role") == "user"]
    ledger_valid = bool(users) and all(isinstance(message.get("content"), str)
                                      and bool(message["content"].strip()) for message in users)
    if ledger_valid:
        ledger_valid &= all(any(message.get("role") == "user" and message.get("content") == users[-1]["content"]
                                for message in request["context"]["messages"])
                            for request in requests if request["method"] == "stream")
    main_responses = [snap.get("response") for request, snap in zip(requests, snapshots)
                      if request["method"] == "stream"]
    ledger_valid &= main_responses == [message for message in messages if message.get("role") == "assistant"]
    add("ledger_consistent", "用户输入与主回复账本一致", "passed" if ledger_valid else "failed",
        "每个主请求保留当前用户输入，全部公开主回复按顺序与账本一致；压缩回复不入用户账本。",
        ["requests.json", "snapshots.json", "messages.json"])
    calls, result_ids, ids, protocol_errors = [], [], set(), []
    for message in messages:
        if message.get("role") == "tool":
            result_ids.append(message.get("tool_call_id"))
        for call in message.get("tool_calls", []):
            calls.append(call)
            try:
                if not call.get("id") or call["id"] in ids or call["name"] not in ACCOUNT_TOOLS:
                    raise ValueError("重复 ID 或未授权工具")
                ids.add(call["id"])
                tool_registry[call["name"]].parse_arguments(call["arguments"])
            except (ValueError, KeyError, TypeError):
                protocol_errors.append("工具名/参数/ID 不合法")
    if sorted(result_ids, key=str) != sorted(ids):
        protocol_errors.append("tool-call/result 未一一配对")
    snapshot_calls = [call for snap in snapshots for call in (snap.get("response") or {}).get("tool_calls", [])]
    if snapshot_calls != calls:
        protocol_errors.append("快照与账本工具调用不一致")
    snapshot_results = [row for snap in snapshots for row in snap.get("tool_results", [])]
    if sorted(row.get("tool_call_id", "") for row in snapshot_results) != sorted(ids):
        protocol_errors.append("快照缺工具结果")
    ledger_results = {message["tool_call_id"]: message.get("content") for message in messages if message.get("role") == "tool"}
    if any(row.get("content") != ledger_results.get(row.get("tool_call_id")) for row in snapshot_results):
        protocol_errors.append("快照与原账本工具结果不一致")
    if any(row.get("is_error") for row in snapshot_results):
        protocol_errors.append("E01 工具返回错误，需核对原因")
    add("tool_protocol", "工具参数、配对和真实结果一致", "failed" if protocol_errors else "passed",
        "；".join(protocol_errors) if protocol_errors else f"{len(calls)} 次工具调用合法且结果完整。",
        ["messages.json", "snapshots.json"])
    archive_valid = _archives_valid(evidence["archives"], captured_contexts, ledger_results)
    add("archive_complete", "外置原文引用与归档清单完整", "passed" if archive_valid else "needs_review",
        "只要求实际引用和已存在 index 的文件与内容完整；小结果可不外置。", ["archives.json", "requests.json"])
    attempted = [call["name"] for call in calls if call.get("name") not in READ_TOOLS]
    body_reads = [call["name"] for call in calls if call.get("name") == "get_producer_skill"
        and not json.loads(call.get("arguments") or "{}").get("list_files", False)] if not protocol_errors else ["参数不合法"]
    add("read_only_attempts", "没有业务写入或主动请求 Skill 正文", "failed" if attempted or body_reads or evidence.get("external_attempts") else "passed",
        f"业务写工具：{attempted}；Skill 正文请求：{body_reads}；外部执行尝试：{len(evidence.get('external_attempts', []))}。",
        ["messages.json", "external_attempts.json"])
    state_valid = all(_valid_state(evidence[name], oracle) for name in ("before", "after"))
    same = evidence["before"] == evidence["after"]
    add("business_unchanged", "业务库与受管业务文件零变化", "failed" if not same else "passed" if state_valid else "needs_review",
        "所有表内容与业务文件 hash 对照；会话/Trace 正常写入不计业务副作用。", ["before.json", "after.json"])
    probe = evidence["probe"]
    try:
        data = json.loads(probe["content"])
        probe_ok = not probe["is_error"] and [row["id"] for row in data["items"]] == [oracle["creator_id"]] and data["page"]["total"] == 1
        probe_ok &= data["page"]["offset"] == 0 and data["page"]["limit"] == 100
        probe_ok &= not any(marker in probe["content"] for marker in oracle["foreign_markers"])
    except (KeyError, ValueError, TypeError):
        probe_ok = False
    add("guard_probe", "强制目录探针只返回当前账号", "passed" if probe_ok else "failed",
        "探针通过真实工具适配器和 HTTP guard；与模型实际调用分开保存。", ["probe.json"])
    final = evidence.get("final_answer", "")
    nonempty = isinstance(final, str) and bool(final.strip())
    final_ledger = next((message.get("content") for message in reversed(messages)
                        if message.get("role") == "assistant" and not message.get("tool_calls")), None)
    final_snapshot = (snapshots[-1].get("response") or {}).get("content")
    final_wire = "".join(choice["output"].get("content") or "" for output in (transport[-1].get("public_outputs", []) if transport else [])
                          for choice in output.get("choices", []))
    nonempty &= final == final_ledger == final_snapshot == final_wire
    add("final_answer", "有完整最终答复", "passed" if nonempty else "failed",
        "具体名称、定位和简介是否准确须人工复核；不靠固定中文词串打分。", ["answer.txt", "oracle.json"])
    statuses = {row["id"]: row["status"] for row in checks}
    def dimension(*names):
        values = [statuses[name] for name in names]
        return "failed" if "failed" in values else "needs_review" if "needs_review" in values else "passed"
    dimensions = {"task_success": dimension("account_tree", "ledger_consistent", "final_answer", "execution_completed"),
        "boundary_enforced": dimension("evidence_complete", "archive_complete", "private_data", "metadata_only", "guard_probe", "read_only_attempts"),
        "state_consistent": dimension("business_unchanged"), "protocol_valid": dimension("evidence_complete", "archive_complete", "ledger_consistent", "tool_protocol")}
    auto = "failed" if "failed" in dimensions.values() else "needs_review" if "needs_review" in dimensions.values() else "passed"
    return {"grader_version": GRADER_VERSION, "auto_status": auto, "checks": checks, "dimensions": dimensions}
