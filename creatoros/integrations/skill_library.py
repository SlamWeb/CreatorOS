"""Account references and series defaults; shared Skill bytes remain in the catalog."""
from __future__ import annotations

from contextlib import contextmanager
from threading import RLock
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError

from creatoros.storage import Creator, CreatorSkillRef, Series, SeriesSkillRef


class SkillLibraryConflict(ValueError):
    pass


class SkillLibraryService:
    def __init__(self, database, catalog, *, source_owner=None):
        self.db, self.catalog = database, catalog
        self.source_owner = source_owner
        self.lock = RLock()

    @contextmanager
    def _write_session(self):
        # Serialize short SQLite metadata mutations across service instances,
        # including library removal racing a newly written series reference.
        with self.db.session() as session:
            if self.db.engine.dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            yield session

    def describe(self, skill_id):
        if skill_id == self.catalog.BUILTIN_ID:
            item = {key: value for key, value in self.catalog.list_skill_files(skill_id).items() if key != "files"}
            return {**item, "family_id": skill_id, "version_number": 1,
                    "version_status": "released", "outputs": ["image"], "capabilities": {"outputs": ["image"]},
                    "producible": True, "carousel_compatible": True}
        return self.catalog.describe(skill_id)

    def _family(self, skill_id):
        if skill_id == self.catalog.BUILTIN_ID:
            return skill_id
        item = self.catalog._record(skill_id)
        return item.get("family_id", item["id"])

    def _creator(self, session, creator_id):
        item = session.get(Creator, creator_id)
        if item is None:
            raise LookupError("账号不存在。")
        return item

    def _series(self, session, series_id):
        item = session.get(Series, series_id)
        if item is None:
            raise LookupError("栏目不存在。")
        if not item.creator_id:
            raise ValueError("请先把栏目分配到账号。")
        return item

    @staticmethod
    def _bindings(series):
        return [value for value in (series.skill_name, series.mind_skill_id, series.production_skill_id) if value]

    def _legacy(self, session, creator_id):
        return list(dict.fromkeys(value for series in session.scalars(
            select(Series).where(Series.creator_id == creator_id)) for value in self._bindings(series)))

    def _ids(self, session, creator_id):
        self._creator(session, creator_id)
        row = session.get(CreatorSkillRef, creator_id)
        return list(dict.fromkeys([*(row.skill_ids_json if row else []), *self._legacy(session, creator_id)]))

    def _contains(self, session, creator_id, skill_id):
        if skill_id != self.catalog.BUILTIN_ID:
            edition = self.catalog._record(skill_id)
            if edition.get("version_status") == "candidate":
                owner = self._source_creator(skill_id)
                if owner and owner != creator_id:
                    return False
        ids = self._ids(session, creator_id)
        if skill_id in ids:
            return True
        family = self._family(skill_id)
        for value in ids:
            try:
                if self._family(value) == family:
                    return True
            except (ValueError, OSError, KeyError):
                continue
        return False

    def contains(self, creator_id, skill_id):
        try:
            with self.db.session() as session:
                return self._contains(session, creator_id, skill_id)
        except (LookupError, ValueError, OSError, KeyError):
            return False

    can_use = contains

    def _source_creator(self, skill_id):
        if skill_id == self.catalog.BUILTIN_ID:
            return None
        item = self.catalog._record(skill_id)
        owner = item.get("creator_id") or item.get("source_creator_id")
        if self.source_owner:
            owner = self.source_owner(item) or owner
        return owner

    def _owns_source(self, creator_id, skill_id):
        return self._source_creator(skill_id) == creator_id

    def can_edit(self, creator_id, skill_id):
        """A use reference does not grant authority to edit another account's source."""
        if skill_id == self.catalog.BUILTIN_ID:
            return False
        try:
            owner = self._source_creator(skill_id)
            if owner:
                return owner == creator_id
            family = self._family(skill_id)
            with self.db.session() as session:
                self._creator(session, creator_id)
                locally_bound = False
                for value in self._legacy(session, creator_id):
                    try:
                        if self._family(value) == family:
                            locally_bound = True
                    except (ValueError, OSError, KeyError):
                        continue
                if not locally_bound:
                    return False
                # Legacy imports have no durable source owner. Once the same
                # family is bound by another account, a new binding must not
                # turn its shared use reference into edit authority.
                for column in session.scalars(select(Series).where(Series.creator_id != creator_id)):
                    for value in self._bindings(column):
                        try:
                            if self._family(value) == family:
                                return False
                        except (ValueError, OSError, KeyError):
                            continue
                return True
        except (LookupError, ValueError, OSError, KeyError):
            return False

    def _items(self, ids, creator_id):
        result, seen = [], set()
        for skill_id in ids:
            if not self.can_use(creator_id, skill_id):
                continue
            try:
                item = self.describe(skill_id)
            except (ValueError, OSError, KeyError):
                item = {"id": skill_id, "name": skill_id, "family_id": skill_id, "available": False,
                        "outputs": [], "local_error": "本地 Skill 缺失或元数据无效。"}
            if item["family_id"] in seen:
                continue
            seen.add(item["family_id"])
            result.append({**item, "can_use": True, "can_edit": self.can_edit(creator_id, item["id"])})
        return result

    def get_creator(self, creator_id):
        with self.db.session() as session:
            ids = self._ids(session, creator_id)
            row = session.get(CreatorSkillRef, creator_id)
            version = row.version if row else 0
        return {"creator_id": creator_id, "items": self._items(ids, creator_id), "version": version,
                "defaults": {"image": [], "video": []}}

    def available(self, creator_id):
        """Exact released and candidate editions within the account's referenced families."""
        with self.db.session() as session:
            ids = self._ids(session, creator_id)
        items = {}
        for skill_id in ids:
            try:
                versions = ([self.describe(skill_id)] if skill_id == self.catalog.BUILTIN_ID
                            else self.catalog.list_skill_versions(skill_id)["items"])
            except (ValueError, OSError, KeyError):
                continue
            for item in versions:
                if not self.can_use(creator_id, item["id"]):
                    continue
                items[item["id"]] = {**item, "can_use": True,
                                     "can_edit": self.can_edit(creator_id, item["id"])}
        return list(items.values())

    def add(self, creator_id, skill_id, request_id):
        if not isinstance(request_id, str) or not 8 <= len(request_id) <= 64:
            raise ValueError("request_id 必须为 8–64 字符。")
        with self.lock:
            try:
                with self._write_session() as session:
                    self._creator(session, creator_id)
                    row = session.get(CreatorSkillRef, creator_id)
                    requests = dict(row.request_ids_json) if row else {}
                    if request_id in requests:
                        if requests[request_id] != skill_id:
                            raise SkillLibraryConflict("同一 request_id 的 Skill 已改变。")
                    else:
                        item = self.describe(skill_id)
                        if (item["version_status"] != "released"
                                and not self._contains(session, creator_id, skill_id)
                                and not self._owns_source(creator_id, skill_id)):
                            raise PermissionError("候选版本不属于此账号可访问的 Skill 家族。")
                        ids = list(row.skill_ids_json) if row else []
                        if not any(self._family(value) == item["family_id"] for value in ids):
                            ids.append(item["id"])
                        requests[request_id] = skill_id
                        if row is None:
                            session.add(CreatorSkillRef(creator_id=creator_id, skill_ids_json=ids,
                                                       request_ids_json=requests, version=1))
                        else:
                            changed = session.execute(update(CreatorSkillRef).where(
                                CreatorSkillRef.creator_id == creator_id, CreatorSkillRef.version == row.version
                            ).values(skill_ids_json=ids, request_ids_json=requests, version=row.version + 1))
                            if changed.rowcount != 1:
                                raise SkillLibraryConflict("账号 Skill 库已变化，请刷新。")
            except IntegrityError as error:
                raise SkillLibraryConflict("账号 Skill 库已变化，请重试。") from error
        return self.get_creator(creator_id)

    def remove(self, creator_id, skill_id, expected_version):
        with self.lock, self._write_session() as session:
            self._creator(session, creator_id)
            row = session.get(CreatorSkillRef, creator_id)
            if (row.version if row else 0) != expected_version:
                raise SkillLibraryConflict("账号 Skill 库已变化，请刷新。")
            family = self._family(skill_id)
            if any(self._family(value) == family for value in self._legacy(session, creator_id)):
                raise SkillLibraryConflict("Skill 仍被旧栏目绑定引用，请先调整栏目绑定。")
            for pool in session.scalars(select(SeriesSkillRef).join(Series).where(Series.creator_id == creator_id)):
                if any(self._family(value) == family for value in pool.skill_ids_json):
                    raise SkillLibraryConflict("Skill 仍在栏目常用池中，请先移出常用池。")
            if row:
                ids = [value for value in row.skill_ids_json if self._family(value) != family]
                changed = session.execute(update(CreatorSkillRef).where(
                    CreatorSkillRef.creator_id == creator_id, CreatorSkillRef.version == expected_version
                ).values(skill_ids_json=ids, version=expected_version + 1))
                if changed.rowcount != 1:
                    raise SkillLibraryConflict("账号 Skill 库已变化，请刷新。")
        return self.get_creator(creator_id)

    def get_series(self, series_id):
        with self.db.session() as session:
            series = self._series(session, series_id)
            row = session.get(SeriesSkillRef, series_id)
            ids = list(dict.fromkeys([*(row.skill_ids_json if row else []), *self._bindings(series)]))
            defaults = row.defaults_json if row else {"image": ([
                {"skill_id": series.production_skill_id or series.skill_name, "role": "primary"},
                *([{"skill_id": series.mind_skill_id, "role": "supporting"}] if series.mind_skill_id else [])
            ] if self._bindings(series) else []), "video": []}
            creator_id, version = series.creator_id, row.version if row else 0
        return {"series_id": series_id, "creator_id": creator_id, "items": self._items(ids, creator_id),
                "version": version, "defaults": defaults}

    def set_series(self, series_id, expected_version, skill_ids, defaults):
        if not isinstance(skill_ids, list) or len(skill_ids) != len(set(skill_ids)):
            raise ValueError("skill_ids 必须为不重复的 Skill ID 列表。")
        if not isinstance(defaults, dict) or set(defaults) != {"image", "video"}:
            raise ValueError("defaults 必须包含 image、video 两个列表。")
        with self.lock:
            try:
                with self._write_session() as session:
                    series = self._series(session, series_id)
                    row = session.get(SeriesSkillRef, series_id)
                    if (row.version if row else 0) != expected_version:
                        raise SkillLibraryConflict("栏目 Skill 池已变化，请刷新。")
                    families = {self._family(value) for value in skill_ids}
                    if len(families) != len(skill_ids):
                        raise ValueError("同一家族只能加入栏目池一次。")
                    if not all(self._contains(session, series.creator_id, value) for value in skill_ids):
                        raise PermissionError("栏目常用池只能引用账号 Skill 库。")
                    if any(self._family(value) not in families for value in self._bindings(series)):
                        raise SkillLibraryConflict("不能移除仍被旧栏目绑定引用的 Skill 家族。")
                    for kind, selections in defaults.items():
                        if not isinstance(selections, list):
                            raise ValueError("阶段默认必须是列表。")
                        if selections and sum(item.get("role") == "primary" for item in selections) != 1:
                            raise ValueError("非空阶段默认必须有且只有一个主 Skill。")
                        selected_ids = []
                        for selection in selections:
                            if set(selection) != {"skill_id", "role"} or selection["role"] not in {"primary", "supporting"}:
                                raise ValueError("阶段 Skill 需要 skill_id 和 primary/supporting 角色。")
                            skill_id = selection["skill_id"]
                            if self._family(skill_id) not in families or not self._contains(session, series.creator_id, skill_id):
                                raise PermissionError("阶段默认必须来自本栏目常用池。")
                            item = self.describe(skill_id)
                            self.catalog.resolve_for_task(skill_id, item["digest"], kind, selection["role"] == "primary")
                            selected_ids.append(skill_id)
                        if len(selected_ids) != len(set(selected_ids)):
                            raise ValueError("阶段默认不能重复引用同一 Skill。")
                    if row is None:
                        session.add(SeriesSkillRef(series_id=series_id, skill_ids_json=skill_ids,
                                                  defaults_json=defaults, version=1))
                    else:
                        changed = session.execute(update(SeriesSkillRef).where(
                            SeriesSkillRef.series_id == series_id, SeriesSkillRef.version == expected_version
                        ).values(skill_ids_json=skill_ids, defaults_json=defaults, version=expected_version + 1))
                        if changed.rowcount != 1:
                            raise SkillLibraryConflict("栏目 Skill 池已变化，请刷新。")
            except IntegrityError as error:
                raise SkillLibraryConflict("栏目 Skill 池已变化，请刷新。") from error
        return self.get_series(series_id)
