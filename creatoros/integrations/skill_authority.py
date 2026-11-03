"""Database-only snapshot used to recheck an account's Skill references."""
from dataclasses import dataclass

from sqlalchemy import select

from creatoros.storage import Creator, CreatorSkillRef, Series


def account_skill_scope(session, creator_id):
    if creator_id is None:
        return {}
    creator = session.get(Creator, creator_id)
    ref = session.get(CreatorSkillRef, creator_id)
    bindings = session.scalars(select(Series).where(Series.creator_id == creator_id).order_by(Series.id))
    return {"active": bool(creator and creator.is_active),
            "library_version": ref.version if ref else 0,
            "library_ids": list(ref.skill_ids_json) if ref else [],
            "bindings": [[row.id, row.revision, row.skill_name, row.mind_skill_id, row.production_skill_id]
                         for row in bindings]}


class SkillAuthorizationConflict(ValueError):
    pass


def skill_is_usable(creator_id, skill_id, references, reference_families, skill_family, *, candidate_owner=None):
    """Pure account-use rule shared by ordinary reads and transactional proofs."""
    if candidate_owner and candidate_owner != creator_id:
        return False
    return skill_id in references or skill_family in reference_families


@dataclass(frozen=True)
class PreparedSkillAuthority:
    creator_id: str
    scope: dict
    allowed: bool


def prepare_skill_authority(database, library, creator_id, skill_ids):
    """Read family/owner metadata before the writer, under the shared Skill lock.

    The proof uses the captured DB references themselves, rather than a separate
    earlier can_use() read which may have raced a revocation.
    """
    with database.session() as session:
        scope = account_skill_scope(session, creator_id)
    references = set(scope.get("library_ids", []))
    for binding in scope.get("bindings", []):
        references.update(item for item in binding[2:5] if item)
    catalog = library.catalog

    def family(skill_id):
        return skill_id if skill_id == catalog.BUILTIN_ID else library._family(skill_id)

    families = set()
    for skill_id in references:
        try:
            families.add(family(skill_id))
        except (ValueError, OSError, KeyError):
            continue  # Existing can_use() ignores unavailable unrelated references.
    allowed = bool(scope.get("active"))
    for skill_id in dict.fromkeys(skill_ids):
        try:
            edition = catalog._record(skill_id) if skill_id != catalog.BUILTIN_ID else {}
            owner = library._source_creator(skill_id) if edition.get("version_status") == "candidate" else None
            if not skill_is_usable(creator_id, skill_id, references, families, family(skill_id),
                                   candidate_owner=owner):
                allowed = False
        except (ValueError, OSError, KeyError):
            allowed = False
    return PreparedSkillAuthority(creator_id=creator_id, scope=scope, allowed=allowed)


def verify_skill_authority(session, prepared):
    """Verify the pre-resolved proof against the current writer snapshot, with no file IO."""
    if not prepared.allowed or account_skill_scope(session, prepared.creator_id) != prepared.scope:
        raise SkillAuthorizationConflict("账号的 Skill 使用权限已变化，请重新核对本次选择。")
