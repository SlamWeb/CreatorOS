"""One real read per pending task and human turn; no task execution or polling."""
from copy import deepcopy

from ..tools.results import ToolResult


class PendingTaskReads:
    """Create a fresh instance for each human turn, never persist this cache."""

    def __init__(self):
        self._receipts = {}
        self._read = set()

    @staticmethod
    def _arguments(name, raw_arguments, context):
        from ..tools.definitions import tool_registry
        from ..tools.host_contract import bind_account_arguments

        tool = tool_registry.get(name)
        if (tool is None or not tool.expose_to_model or context is not None
                and context.allowed_tools is not None and name not in context.allowed_tools):
            return None
        try:
            return tool.parse_arguments(bind_account_arguments(name, raw_arguments, context))
        except (ValueError, TypeError):
            # Let execute_tool_call produce its canonical validation/scope error.
            return None

    @staticmethod
    def _query(name, args):
        if name == "get_skill_extraction" and args.get("job_id"):
            return "skill_extraction", args["job_id"]
        if name == "manage_skill_iteration" and args.get("action") == "get":
            return "skill_iteration", args["iteration_id"]
        return None

    @staticmethod
    def _mutation(name, args):
        if name in {"edit_extracted_skills", "revise_extracted_skills", "trial_extracted_skills",
                    "save_extracted_skills", "cancel_skill_extraction"}:
            return "skill_extraction", args["job_id"]
        if name == "manage_skill_iteration" and args.get("action") in {"pause", "resume", "continue"}:
            return "skill_iteration", args["iteration_id"]
        return None

    def before(self, name, raw_arguments, context=None) -> ToolResult | None:
        """Return a copied receipt to skip execution, or None to execute normally."""
        args = self._arguments(name, raw_arguments, context)
        if args is None:
            return None
        key = self._query(name, args)
        if key not in self._read or key not in self._receipts:
            return None
        result = deepcopy(self._receipts[key])
        result.details.update(pending_read_deferred=True, next_action="wait_background")
        return result

    def observe(self, name, raw_arguments, result, context=None):
        """Record only real, validated tool results; never derive IDs from prose."""
        args = self._arguments(name, raw_arguments, context)
        if args is None or not isinstance(result, ToolResult):
            return
        if result.details.get("pending_read_deferred"):
            return
        mutation = self._mutation(name, args)
        if mutation is not None and not result.is_error:
            self._receipts.pop(mutation, None)
            self._read.discard(mutation)
        key = self._query(name, args)
        pending = result.details.get("pending_task")
        expected_kind = ("skill_iteration" if name == "manage_skill_iteration" else "skill_extraction"
                         if name in {"get_skill_extraction", "extract_skill_from_images",
                                     "extract_skills_from_artifact", "create_skill_draft"}
                         or mutation is not None else None)
        if isinstance(pending, dict):
            identity = pending.get("kind"), pending.get("id")
            if pending.get("kind") != expected_kind or key is not None and key != identity:
                return
        if (isinstance(pending, dict) and pending.get("kind") in
                {"skill_extraction", "skill_iteration"}
                and isinstance(pending.get("id"), str) and pending["id"]
                and (pending.get("status") in {"queued", "running", "waiting_production", "unknown"}
                     or pending.get("kind") == "skill_extraction" and bool(pending.get("operation")))
                and (not result.is_error or pending["status"] == "unknown")):
            identity = pending["kind"], pending["id"]
            if key == identity:
                self._receipts[key] = deepcopy(result)
                self._read.add(key)
            elif name in {"extract_skill_from_images", "extract_skills_from_artifact",
                          "create_skill_draft", "manage_skill_iteration"} or mutation == identity:
                self._receipts[identity] = deepcopy(result)
        elif key is not None and (not result.is_error or isinstance(pending, dict)):
            self._receipts.pop(key, None)
            self._read.discard(key)
