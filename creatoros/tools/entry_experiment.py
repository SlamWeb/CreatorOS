"""Frozen high-frequency direct actions for opt-in entry experiments.

Counts and the six fixed historical snapshot hashes are documented in the
tool-calling evaluation folder. The list is not selected from evaluation cases.
"""
import re


HYBRID_ACTIONS = (
    "get_skill_extraction",
    "read_image",
    "update_producer_skill_file",
    "get_producer_skill",
    "manage_plan.update",
    "manage_topics.create",
    "get_content_run",
    "manage_topics.list",
)


def direct_alias(action_id):
    """Map a scoped catalog action to a provider-compatible function name."""
    if not isinstance(action_id, str):
        raise ValueError("invalid action id")
    alias = action_id.replace(".", "__")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", alias):
        raise ValueError("invalid direct tool name")
    return alias
