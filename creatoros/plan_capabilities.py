"""Declarative plan permissions, independent of tool and Agent initialization.

These declarations grant no resource ownership and perform no dispatch. Runtime
argument validation, account scopes and production replay rules remain separate.
None explicitly permits an actionless tool; an empty action set denies access.
"""
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class PlanCapability:
    plan_actions: frozenset[str | None] = frozenset()
    continuation_actions: frozenset[str | None] = frozenset()
    continuation_edit_actions: frozenset[str | None] = frozenset()
    wait_for_completion: bool = False


_ACTIONLESS = frozenset({None})
_READ = frozenset({"get", "list"})
_GET = frozenset({"get"})
_PRODUCTION = frozenset({"start", "tasks", "fresh", "reference"})
_COLLECTION = frozenset({"list", "get", "create", "export"})
_CAPABILITIES = MappingProxyType({
    # Order is part of the existing model-facing PlanStepArgs enum.
    # request_review is a host checkpoint, not a registered model tool.
    "request_review": PlanCapability(plan_actions=_ACTIONLESS, continuation_edit_actions=_ACTIONLESS),
    "start_content_run": PlanCapability(plan_actions=_PRODUCTION, continuation_edit_actions=_PRODUCTION,
                                        wait_for_completion=True),
    "manage_topics": PlanCapability(plan_actions=frozenset({"get", "list", "create", "update", "remove"}),
                                    continuation_actions=_READ, continuation_edit_actions=_READ),
    "manage_material": PlanCapability(plan_actions=_READ, continuation_actions=_READ, continuation_edit_actions=_READ),
    "manage_media_collection": PlanCapability(plan_actions=_COLLECTION, continuation_actions=_READ,
                                              continuation_edit_actions=_COLLECTION),
    "manage_skill_library": PlanCapability(plan_actions=frozenset({"get", "add"}),
                                           continuation_actions=_GET, continuation_edit_actions=_GET),
    **{name: PlanCapability(plan_actions=_ACTIONLESS, continuation_actions=_ACTIONLESS,
                           continuation_edit_actions=_ACTIONLESS)
       for name in ("get_content_run", "get_creator_tasks", "get_producer_skill")},
    "manage_plan": PlanCapability(continuation_actions=frozenset({"get", "update", "resume", "retry", "skip"})),
    **{name: PlanCapability(continuation_actions=_ACTIONLESS)
       for name in ("list_creators", "list_creator_series", "list_producer_skills", "read_image", "read_tool_result")},
})

PLAN_TOOL_NAMES = tuple(name for name, capability in _CAPABILITIES.items() if capability.plan_actions)
CONTINUATION_TOOLS = frozenset(name for name, capability in _CAPABILITIES.items() if capability.continuation_actions)
_SCOPES = {"plan": "plan_actions", "continuation": "continuation_actions",
           "continuation_edit": "continuation_edit_actions"}


def allows_tool_action(tool: str, action: str | None, *, scope: str) -> bool:
    """Unknown tools, actions and permission scopes never acquire a grant."""
    if (not isinstance(tool, str) or not isinstance(scope, str)
            or action is not None and not isinstance(action, str)):
        return False
    capability = _CAPABILITIES.get(tool)
    field = _SCOPES.get(scope)
    return bool(capability and field and action in getattr(capability, field))


def can_wait(tool: str) -> bool:
    capability = _CAPABILITIES.get(tool) if isinstance(tool, str) else None
    return bool(capability and capability.wait_for_completion)
