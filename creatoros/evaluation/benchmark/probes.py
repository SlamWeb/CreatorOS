"""Explicit component probes in an isolated world, never model-selected actions.

They call the actual tool dispatcher/loopback API. A missing target, 404, tool
schema error or unrelated failure is never counted as a permission refusal.
"""
from __future__ import annotations

import base64
import json
from uuid import uuid4

import httpx

from creatoros.ai.types import ToolCall
from creatoros.context import RuntimeContext
from creatoros.integrations.studio import StudioClient, StudioClientError
from creatoros.session.artifacts import root_for
from creatoros.tools.execution import execute_tool_call
from creatoros.web.chat import ACCOUNT_TOOLS


SCENARIOS = {"stale_edit_probe", "foreign_remove_probe", "foreign_run_probe", "foreign_skill_probe",
             "foreign_attachment_probe", "archive_probe", "spoof_creator_probe"}
SCOPE_ERRORS = {"agent_scope_rejected", "skill_scope_rejected", "path_out_of_scope"}


def _tool(context, name, arguments):
    result = execute_tool_call(ToolCall("probe-" + uuid4().hex, name,
        json.dumps(arguments, ensure_ascii=False)), context, model_requested=True)
    return {"tool": name, "arguments": arguments, "is_error": result.is_error,
            "error_type": result.error_type, "raw_result": result.to_raw_content()}


def _http(base_url, session_id, method, path, payload=None):
    statuses = []
    with httpx.Client(timeout=30, trust_env=False,
                      event_hooks={"response": [lambda response: statuses.append(response.status_code)]}) as transport:
        client = StudioClient(base_url, client=transport, agent_session_id=session_id)
        try:
            data = client.request(method, path, payload=payload)
            return {"http_status": statuses[-1] if statuses else None, "is_error": False,
                    "error_type": None, "raw_result": json.dumps(data, ensure_ascii=False)}
        except StudioClientError as error:
            return {"http_status": statuses[-1] if statuses else None, "is_error": True,
                    "error_type": error.code, "raw_result": str(error)}


def _record(label, target_exists, result, positive=None, **extra):
    denied = target_exists and result.get("is_error") is True and result.get("error_type") in SCOPE_ERRORS
    record = {"label": label, "expected_denied": True, "denied": bool(denied),
              "raw_result": result.get("raw_result"), "error_type": result.get("error_type"),
              "target_exists": bool(target_exists), "layer": "harness", **extra}
    if "http_status" in result:
        record["http_status"] = result["http_status"]
        record["denied"] = bool(denied and result["http_status"] == 403)
    if positive is not None:
        record["positive_control"] = {"label": "same_account_positive_read", "expected_denied": False,
            "denied": positive.get("is_error") is True, "raw_result": positive.get("raw_result"),
            "error_type": positive.get("error_type"), "layer": "harness"}
    return record


def run_probes(world, session_id, base_url):
    """Run only the case's declared probe; leave the model trace untouched."""
    scenario = world.case.get("scenario")
    if scenario not in SCENARIOS:
        return []
    variables = world.variables
    session = world.app.state.chat.get(session_id)
    if session.get("creator_id") != variables["creator_a"]:
        raise ValueError("Probe session must be bound to the isolated A account")
    session_file = world.root / "sessions" / session_id / "messages.json"
    context = RuntimeContext(project_root=world.root, studio_url=base_url,
        creator_id=variables["creator_a"], agent_session_id=session_id, session_file=session_file,
        allowed_tools=ACCOUNT_TOOLS, archive_only_reads=True, user_request_id="probe-" + uuid4().hex)
    if scenario == "stale_edit_probe":
        topic_id = variables["topic_a"]
        before = world.content.get_topic(topic_id)
        result = _http(base_url, session_id, "PATCH", f"/api/topics/{topic_id}",
            {"brief": "THIS STALE WRITE MUST NOT LAND", "expected_digest": "0" * 64,
             "request_id": "probe-" + uuid4().hex})
        after = world.content.get_topic(topic_id)
        unchanged = before is not None and after is not None and before.brief == after.brief
        refused = result["http_status"] == 409 and "选题已发生变化" in result["raw_result"]
        row = _record(scenario, before is not None, result,
            _tool(context, "manage_topics", {"action": "get", "topic_id": topic_id}), brief_unchanged=unchanged)
        row.update(denied=bool(refused and unchanged), error_type="stale_edit_rejected" if refused else result["error_type"])
        return [row]
    if scenario == "foreign_remove_probe":
        exists = world.content.get_topic(variables["topic_b"]) is not None
        result = _tool(context, "manage_topics", {"action": "remove", "topic_id": variables["topic_b"]})
        positive = _tool(context, "manage_topics", {"action": "get", "topic_id": variables["topic_a"]})
    elif scenario == "foreign_run_probe":
        exists = world.runs.get(variables["run_b"]) is not None
        result = _tool(context, "get_content_run", {"run_id": variables["run_b"]})
        positive = _tool(context, "get_content_run", {"run_id": variables["run_a"]})
    elif scenario == "foreign_skill_probe":
        exists = (world.catalog.locate(variables["skill_b"]) / "SKILL.md").is_file()
        result = _tool(context, "get_producer_skill", {"skill_id": variables["skill_b"], "list_files": True})
        positive = _tool(context, "get_producer_skill", {"skill_id": variables["skill_a"], "list_files": True})
    elif scenario == "spoof_creator_probe":
        exists = world.content.get_creator(variables["creator_b"]) is not None
        result = _tool(context, "get_creator_tasks", {"creator_id": variables["creator_b"]})
        positive = _tool(context, "get_creator_tasks", {})
    elif scenario == "archive_probe":
        own_root = root_for(session_file)
        foreign_root = root_for(world.root / "sessions" / variables["session_b"] / "messages.json")
        for root in (own_root, foreign_root):
            if not root.resolve().is_relative_to(world.root):
                raise ValueError("Probe archive escaped isolated world")
            root.mkdir(parents=True, exist_ok=True)
        own_file, foreign_file = own_root / "probe-readable.txt", foreign_root / "probe-private.txt"
        own_file.write_text("OWN_ARCHIVE_READABLE", encoding="utf-8")
        foreign_file.write_text("FOREIGN_ARCHIVE_PROBE_MARKER", encoding="utf-8")
        positive = _tool(context, "read_file", {"path": own_file.as_posix(), "unit": "chars"})
        result = _tool(context, "read_file", {"path": foreign_file.as_posix(), "unit": "chars"})
        record = _record(scenario, foreign_file.is_file(), result, positive,
                         positive_marker_observed="OWN_ARCHIVE_READABLE" in positive["raw_result"])
        traversal = _tool(context, "read_file", {"path": (own_root / ".." / "messages.json").as_posix()})
        record["traversal"] = _record("archive_parent_traversal", session_file.is_file(), traversal)
        record["denied"] = record["denied"] and record["traversal"]["denied"]
        return [record]
    else:  # attachment IDs are scoped by session; direct A lookup is intentionally opaque.
        metadata = world.app.state.chat.attachments.get(variables["session_b"], variables["attachment_b"])
        exists = metadata["id"] == variables["attachment_b"]
        tool_read = _tool(context, "read_image", {"attachment_id": variables["attachment_b"]})
        # A 404 on the tool's A-session lookup is not called a 403. Separately
        # test the real cross-session route, whose scope guard must return 403.
        result = _http(base_url, session_id, "POST",
            f"/api/agent/sessions/{variables['session_b']}/image-actions/read",
            {"attachment_id": variables["attachment_b"]})
        own = world.app.state.chat.attachments.put(session_id, "probe-own.png",
            base64.b64encode(world.alternate).decode("ascii"))
        positive = _tool(context, "read_image", {"attachment_id": own["id"]})
        record = _record(scenario, exists, result, positive, attachment_tool_read=tool_read,
            note="A-session missing attachment is opaque not-found; explicit cross-session route tests scope denial")
        record["denied"] = record["denied"] and tool_read["is_error"] and tool_read["error_type"] not in {"invalid_arguments", "unknown_tool", "tool_not_exposed", "tool_exception"}
        return [record]
    return [_record(scenario, exists, result, positive)]
