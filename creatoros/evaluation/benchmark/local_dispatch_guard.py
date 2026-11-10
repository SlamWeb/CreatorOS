"""Opt-in local-only regression host guard; not an OS or network sandbox.

The model still sees the normal tool schemas. Rejected calls are emitted through
the ordinary tool trace, and a rejected attempt is an explicit critical gate.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import stat
from threading import Lock
from uuid import UUID

VERSION = "local-dispatch-v3"
_V1_V2_CASES = frozenset({"B01", "B02", "B03", "B05", "B06", "B07", "B10", "B31", "B32", "B38", "B40"})
CASES_BY_VERSION = {
    "local-dispatch-v1": _V1_V2_CASES,
    "local-dispatch-v2": _V1_V2_CASES,
    VERSION: _V1_V2_CASES | frozenset({"B08", "B09", "B11", "B12", "B13", "B14", "B15", "B16", "B17", "B20", "B25", "B30"}),
}
CASES_BY_VERSION["local-dispatch-v4"] = CASES_BY_VERSION["local-dispatch-v3"]
CASES_BY_NAMESPACE = {"local-dispatch-v4": {"base": CASES_BY_VERSION["local-dispatch-v3"],
                                            "natural": frozenset({"B06", "B07"})}}
CASES_BY_NAMESPACE["local-dispatch-v5"] = {
    "base": CASES_BY_NAMESPACE["local-dispatch-v4"]["base"] | frozenset({"B18", "B19", "B33", "B35", "B37", "B39"}),
    "natural": CASES_BY_NAMESPACE["local-dispatch-v4"]["natural"],
    "expansion": frozenset({"E06", "E07", "Y04", "C02", "E05"}),
}
CASES_BY_VERSION["local-dispatch-v5"] = CASES_BY_NAMESPACE["local-dispatch-v5"]["base"]
CASES_BY_NAMESPACE["local-dispatch-v6"] = {"e06-scope": frozenset({"E06"})}
CASES_BY_VERSION["local-dispatch-v6"] = frozenset({"E06"})
CASES_BY_NAMESPACE["local-dispatch-v7"] = {"expansion": frozenset({"P01", "P02", "P03"})}
CASES_BY_VERSION["local-dispatch-v7"] = CASES_BY_NAMESPACE["local-dispatch-v7"]["expansion"]
CASES_BY_NAMESPACE["local-dispatch-v8"] = {"base": frozenset({"B29", "B36", "B45"})}
CASES_BY_VERSION["local-dispatch-v8"] = CASES_BY_NAMESPACE["local-dispatch-v8"]["base"]
VERSIONS = frozenset(CASES_BY_VERSION)
CASES = CASES_BY_VERSION[VERSION]

# These tools use only the isolated local Studio routes or session archive.
# manage_topics deliberately reaches the real ownership/permission checks and
# isolated writes, including removal and cross-account negative tests.
LOCAL_TOOLS = frozenset({"manage_topics", "list_creators", "list_creator_series",
                         "get_creator_tasks", "list_producer_skills", "get_producer_skill",
                         "read_file", "read_tool_result", "get_current_date", "get_current_time"})


class DispatchGuard:
    def __init__(self, version=VERSION):
        if version not in VERSIONS:
            raise ValueError("Unknown local dispatch policy")
        self.version = version
        self._observed = []
        self._lock = Lock()
        self._wrapper = None
        self._world = None
        self._world_root = None
        self._trusted_root = None

    @staticmethod
    def _safe_under(root, path):
        root, path = Path(root), Path(path)
        if not root.is_absolute() or not path.is_absolute() or not path.is_relative_to(root):
            return False
        for part in (root, *(root / Path(*path.relative_to(root).parts[:index])
                             for index in range(1, len(path.relative_to(root).parts) + 1))):
            if part.is_symlink() or (part.exists() and getattr(part.lstat(), 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)):
                return False
        return path.resolve().is_relative_to(root.resolve())

    def bind_world(self, world, expected_root, *, trusted_root):
        """Bind only a link-free descendant of a caller-owned trusted root."""
        if self.version != 'local-dispatch-v8' or self._world is not None:
            raise RuntimeError('Invalid or repeated isolated world binding')
        trusted, root = Path(trusted_root).absolute(), Path(expected_root).absolute()
        if ('..' in trusted.parts or '..' in root.parts or trusted.resolve() != trusted
                or not self._safe_under(trusted, root) or Path(world.root) != root):
            raise RuntimeError('Trial world root differs')
        self._world, self._world_root, self._trusted_root = world, root, trusted
        if not self._world_roots_valid():
            self._world = self._world_root = self._trusted_root = None
            raise RuntimeError('Image evaluation roots are not isolated')

    def _world_roots_valid(self):
        try:
            world, root = self._world, self._world_root
            app = world.app
            chat = app.state.chat
            roots = ((world.catalog.root, root / 'world-producer-skills'),
                     (world.runs.output_root, root / 'outputs'),
                     (chat.root, root / 'sessions'), (chat.attachments.root, root / 'sessions'),
                     (Path(world.database.engine.url.database), root / 'world.db'),
                     (app.state.queries.artifacts.output_root, root / 'outputs'))
            return (self._trusted_root.resolve() == self._trusted_root and self._safe_under(self._trusted_root, root)
                    and app.state.database is world.database and world.runs.database is world.database
                    and app.state.skill_installs.catalog is world.catalog
                    and app.state.queries.artifacts.database is world.database
                    and app.state.background_services_started is False
                    and all(Path(actual).absolute() == expected and self._safe_under(root, expected)
                            for actual, expected in roots))
        except (AttributeError, OSError, TypeError, ValueError):
            return False

    def _image_context_valid(self, context):
        try:
            if not self._world_roots_valid() or context is None or context.creator_id != 'bench-a':
                return False
            sid = str(UUID(context.agent_session_id))
            expected = self._world_root / 'sessions' / sid / 'messages.json'
            return (sid == context.agent_session_id and Path(context.session_file).absolute() == expected
                    and self._safe_under(self._world_root, expected)
                    and context.studio_url == self._world.app.state.benchmark_base_url
                    and self._world.app.state.chat.get(sid).get('creator_id') == context.creator_id)
        except Exception:
            # Lookup failures, including HTTPException for missing sessions,
            # must never bypass the local dispatch guard.
            return False

    def wrap(self, original):
        def execute(call, context=None, *, model_requested=False):
            if not model_requested:
                return original(call, context=context, model_requested=model_requested)
            allowed = call.name in LOCAL_TOOLS
            if self.version == 'local-dispatch-v8' and call.name in {'read_image', 'get_content_run'}:
                allowed = self._image_context_valid(context)
            if self.version in {"local-dispatch-v2", "local-dispatch-v3", "local-dispatch-v4", "local-dispatch-v5", "local-dispatch-v6", "local-dispatch-v7"} and call.name == "manage_media_collection":
                # Reject duplicate keys as well as malformed or non-object JSON;
                # the actual executor still enforces account and argument rules.
                def unique_pairs(pairs):
                    result = {}
                    for key, value in pairs:
                        if key in result:
                            raise ValueError("Duplicate argument")
                        result[key] = value
                    return result
                try:
                    args = json.loads(call.arguments, object_pairs_hook=unique_pairs)
                    allowed = isinstance(args, dict) and args.get("action") in ("list", "get")
                except (TypeError, ValueError):
                    allowed = False
            if self.version == "local-dispatch-v7" and call.name == "manage_material":
                def unique_pairs(pairs):
                    result = {}
                    for key, value in pairs:
                        if key in result:
                            raise ValueError("Duplicate argument")
                        result[key] = value
                    return result
                try:
                    args = json.loads(call.arguments, object_pairs_hook=unique_pairs)
                    allowed = isinstance(args, dict) and args.get("action") in {"list", "get", "analysis_status", "analysis"}
                except (TypeError, ValueError):
                    allowed = False
            denied = not allowed or (call.name == "read_file" and
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


def install(version=VERSION):
    # Only the dedicated per-case benchmark process installs this wrapper.
    import creatoros.agent.loop as loop
    guard = DispatchGuard(version)
    loop.execute_tool_call = guard.wrap(loop.execute_tool_call)
    return guard


def verify_installed(guard, app):
    import creatoros.agent.loop as loop
    if (not isinstance(guard, DispatchGuard) or guard._wrapper is None
            or loop.execute_tool_call is not guard._wrapper
            or app.state.background_services_started is not False):
        raise RuntimeError("Local dispatch guard or deferred background state unavailable")
    if guard.version == 'local-dispatch-v8' and (guard._world is None or guard._world.app is not app or not guard._world_roots_valid()):
        raise RuntimeError("Image evaluation world roots differ")


def apply_gate(scores, gate):
    """Record guard evidence as a non-averaged mandatory critical gate."""
    scores["critical_checks"].append(gate)
    if gate["status"] == "failed":
        scores["critical_status"] = "failed"
    elif gate["status"] != "passed" and scores["critical_status"] == "passed":
        scores["critical_status"] = "needs_review"
    return scores
