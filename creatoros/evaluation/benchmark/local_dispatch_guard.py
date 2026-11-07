"""Opt-in local-only regression host guard; not an OS or network sandbox.

The model still sees the normal tool schemas. Rejected calls are emitted through
the ordinary tool trace, and a rejected attempt is an explicit critical gate.
"""
from __future__ import annotations

from collections import Counter
from threading import Lock

VERSION = "local-dispatch-v1"
CASES = frozenset({"B01", "B02", "B03", "B05", "B06", "B07", "B10", "B31", "B32", "B38", "B40"})

# These tools use only the isolated local Studio routes or session archive.
# manage_topics deliberately reaches the real ownership/permission checks and
# isolated writes, including removal and cross-account negative tests.
LOCAL_TOOLS = frozenset({"manage_topics", "list_creators", "list_creator_series",
                         "get_creator_tasks", "list_producer_skills", "get_producer_skill",
                         "read_file", "read_tool_result", "get_current_date", "get_current_time"})


class DispatchGuard:
    def __init__(self):
        self._observed = []
        self._lock = Lock()
        self._wrapper = None

    def wrap(self, original):
        def execute(call, context=None, *, model_requested=False):
            if not model_requested:
                return original(call, context=context, model_requested=model_requested)
            denied = call.name not in LOCAL_TOOLS or (call.name == "read_file" and
                      (context is None or not context.archive_only_reads))
            with self._lock:
                self._observed.append({"id": call.id, "denied": denied})
            if denied:
                from creatoros.tools.results import ToolResult
                return ToolResult(content="评测隔离策略阻止了该工具的外部或非本地操作。", is_error=True,
                                  error_type="local_dispatch_guard_denied", details={"pre_dispatch": True})
            return original(call, context=context, model_requested=model_requested)
        self._wrapper = execute
        return execute

    def score(self, calls, *, background_started=False):
        with self._lock:
            observed = list(self._observed)
        denied = sum(event["denied"] for event in observed)
        if denied:
            return {"id": "local_dispatch_guard", "status": "failed", "detail": f"Restricted tool attempts: {denied}"}
        if background_started:
            return {"id": "local_dispatch_guard", "status": "needs_review", "detail": "Background services started despite local dispatch policy"}
        if not isinstance(calls, list) or any(not isinstance(row, dict) or not row.get("tool_call_id") for row in calls):
            return {"id": "local_dispatch_guard", "status": "needs_review", "detail": "Missing tool-call evidence"}
        if Counter(event["id"] for event in observed) != Counter(row["tool_call_id"] for row in calls):
            return {"id": "local_dispatch_guard", "status": "needs_review", "detail": "Dispatch and model trace do not match"}
        return {"id": "local_dispatch_guard", "status": "passed", "detail": "No restricted dispatch"}


def install():
    # Only the dedicated per-case benchmark process installs this wrapper.
    import creatoros.agent.loop as loop
    guard = DispatchGuard()
    loop.execute_tool_call = guard.wrap(loop.execute_tool_call)
    return guard


def verify_installed(guard, app):
    import creatoros.agent.loop as loop
    if (not isinstance(guard, DispatchGuard) or guard._wrapper is None
            or loop.execute_tool_call is not guard._wrapper
            or app.state.background_services_started is not False):
        raise RuntimeError("Local dispatch guard or deferred background state unavailable")


def apply_gate(scores, gate):
    """Record guard evidence as a non-averaged mandatory critical gate."""
    scores["critical_checks"].append(gate)
    if gate["status"] == "failed":
        scores["critical_status"] = "failed"
    elif gate["status"] != "passed" and scores["critical_status"] == "passed":
        scores["critical_status"] = "needs_review"
    return scores
