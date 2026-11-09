"""Same-turn protection for Skill writes with an unknown outcome."""
from __future__ import annotations

import json
import re

from pydantic import ValidationError

from creatoros.tools.studio import UpdateProducerSkillFileArgs, get_producer_skill


_SKILL_ID = re.compile(r"[a-z0-9-]+--[a-f0-9]{16}\Z")


def _skill_id(call):
    if call.name != "update_producer_skill_file":
        return None
    try:
        return UpdateProducerSkillFileArgs.model_validate_json(call.arguments or "{}").skill_id
    except (ValueError, TypeError, ValidationError):
        return None


class SkillUnknownWriteGuard:
    """Block affected families until the current user turn ends.

    A released version may produce an unknown candidate ID. Exact version IDs
    alone cannot establish that two edits affect different Skills. Resolve only
    after an unknown write, through the existing scoped version-read endpoint;
    any unavailable or inconsistent identity remains conservatively blocked.
    """

    def __init__(self, context):
        self.context = context
        self.families = set()
        self.unresolved = False
        self._resolved = {}

    def _family(self, skill_id):
        if skill_id is None:
            return None
        if skill_id in self._resolved:
            return self._resolved[skill_id]
        try:
            result = get_producer_skill(skill_id, context=self.context, include_versions=True)
            data = json.loads(result.content) if not result.is_error else None
            family = data.get("family_id") if isinstance(data, dict) else None
            items = data.get("items") if isinstance(data, dict) else None
            matches = [item for item in items if isinstance(item, dict) and item.get("id") == skill_id] if isinstance(items, list) else []
            if (not isinstance(family, str) or _SKILL_ID.fullmatch(family) is None
                    or len(matches) != 1 or matches[0].get("family_id") != family):
                family = None
        except Exception:
            # Family lookup is supplementary: a failed read must not prevent
            # the original unknown-write receipt from entering the ledger.
            family = None
        self._resolved[skill_id] = family
        return family

    def record_unknown(self, call):
        family = self._family(_skill_id(call))
        if family is None:
            self.unresolved = True
        else:
            self.families.add(family)

    def blocks(self, call):
        if call.name != "update_producer_skill_file" or not (self.unresolved or self.families):
            return False
        if self.unresolved:
            return True
        family = self._family(_skill_id(call))
        return family is None or family in self.families
