"""Small local account boundary for requests made by a scoped Studio Agent session.

This is a correctness guard for CreatorOS's local Agent UI, not an authentication
mechanism. Requests without the host-added session header retain the existing
Web/CLI behavior.
"""
from __future__ import annotations

import re

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select

from creatoros.storage import ContentRun, Creator, Database, Series, Topic, TopicRemoval, WriteReceipt, PublicationRequest


_CREATOR_PATH = re.compile(r"^/api/creators/([^/]+)$")
_SERIES_TOPICS = re.compile(r"^/api/series/([^/]+)/(?:topics|topic-library)$")
_SERIES_RESEARCH = re.compile(r"^/api/series/([^/]+)/topic-research$")
_SERIES_COMPOSITION = re.compile(r"^/api/series/([^/]+)/composition$")
_SERIES_QUEUE = re.compile(r"^/api/series/([^/]+)/queue$")
_SERIES_QUEUE_RECEIPT = re.compile(r"^/api/series/([^/]+)/queue/receipts/([^/]+)$")
_SERIES_DELETE = re.compile(r"^/api/series/([^/]+)$")
_BATCH = re.compile(r"^/api/topic-research/([a-f0-9]{32})$")
_BATCH_ACTION = re.compile(r"^/api/topic-research/([a-f0-9]{32})/(preview|queue)$")
_RUN = re.compile(r"^/api/runs/([^/]+)$")
_RUN_EXECUTE = re.compile(r"^/api/runs/([^/]+)/execute$")
_RUN_DISCUSSION = re.compile(r"^/api/runs/([^/]+)/discussion$")
_RUN_REVISIONS = re.compile(r"^/api/runs/([^/]+)/revisions$")
_TOPIC_REMOVE = re.compile(r"^/api/topics/([^/]+)/remove$")
_TOPIC = re.compile(r"^/api/topics/([^/]+)$")
_TOPIC_EDIT_RECEIPT = re.compile(r"^/api/topics/([^/]+)/edit-receipts/([^/]+)$")
_CREATOR_TASKS = re.compile(r"^/api/creators/([^/]+)/tasks$")
_PRODUCER_SKILL_CONTENT = re.compile(r"^/api/producer-skills/(knowledge-to-carousel|[a-z0-9-]+--[a-f0-9]{16})/content$")
_PRODUCER_SKILL_FILES = re.compile(
    r"^/api/producer-skills/(knowledge-to-carousel|[a-z0-9-]+--[a-f0-9]{16})/files(?:/(?:content|text))?$"
)


class AgentScopeGuard:
    """Check every request carrying a persisted creator-scoped Agent session."""

    def __init__(self, db: Database, chat, research, discussions=None, iterations=None):
        self.db = db
        self.chat = chat
        self.research = research
        self.discussions = discussions
        self.iterations = iterations

    async def check(self, request: Request) -> Response | None:
        session_id = request.headers.get("x-creatoros-agent-session")
        if not session_id:
            return None
        # Applying a candidate is a user's explicit review action, not a model tool.
        # Do not let overview sessions bypass this boundary either.
        if (request.url.path.startswith("/api/skill-calibrations")
                or re.fullmatch(r"/api/producer-skills/[^/]+/publish-version", request.url.path)
                or re.fullmatch(r"/api/series/[^/]+/skill-version", request.url.path)):
            return self._denied()
        try:
            session = self.chat.get(session_id)
        except Exception:
            return self._denied()

        # Every Agent source, including overview sessions, must edit what it
        # actually read; the header-free legacy Web editor remains compatible.
        if request.method.upper() == "PATCH" and _TOPIC.fullmatch(request.url.path):
            try:
                edit_payload = await request.json()
            except Exception:
                return self._denied()
            if (not isinstance(edit_payload, dict)
                    or not isinstance(edit_payload.get("expected_digest"), str)
                    or not re.fullmatch(r"[a-f0-9]{64}", edit_payload["expected_digest"])
                    or not isinstance(edit_payload.get("request_id"), str)
                    or not 8 <= len(edit_payload["request_id"]) <= 64):
                return self._denied()

        if session.get("scope_kind", "overview") != "creator":
            return None
        creator_id = session.get("creator_id")
        if not isinstance(creator_id, str) or not self._active_creator(creator_id):
            return self._denied()

        method, path = request.method.upper(), request.url.path

        collection = re.fullmatch(r"/api/creators/([^/]+)/collections(?P<tail>/.*)?", path)
        if collection:
            tail = collection.group("tail") or ""
            single = re.fullmatch(r"/[^/]+", tail) and tail != "/sources"
            allowed = ((method == "GET" and (tail in {"", "/sources"} or single
                        or re.fullmatch(r"/[^/]+/(?:download|items/[^/]+/[^/]+/[0-9]+|exports/[a-f0-9]{64}(?:/download)?)", tail)))
                       or (method == "POST" and (not tail or re.fullmatch(r"/[^/]+/(?:sort|exports)", tail)))
                       or (method in {"PATCH", "DELETE"} and single))
            # The collection service also verifies collection ownership, current
            # source membership and each source's frozen account identity.
            return None if collection.group(1) == creator_id and allowed else self._denied()

        if path == "/api/skill-iterations" and method == "GET":
            requested = request.query_params.get("creator_id")
            selected = request.query_params.get("series_id")
            return None if ((requested is None or requested == creator_id)
                            and (selected is None or self._owns_series(selected, creator_id))) else self._denied()
        if path == "/api/skill-iterations" and method == "POST":
            try:
                payload = await request.json()
            except Exception:
                return self._denied()
            return None if (isinstance(payload, dict)
                            and self._owns_series(payload.get("series_id", ""), creator_id)
                            and self._owns_topic(payload.get("topic_id", ""), creator_id)
                            and self._owns_skill(payload.get("skill_id", ""), creator_id)) else self._denied()
        iteration = re.fullmatch(r"/api/skill-iterations/([a-f0-9]{64})(?:/(actions|files/[a-zA-Z0-9_-]+))?", path)
        if iteration:
            if not self.iterations or not ((method == "GET" and iteration.group(2) != "actions")
                                          or (method == "POST" and iteration.group(2) == "actions")):
                return self._denied()
            try:
                item = self.iterations.get(iteration.group(1))
                allowed = item.get("creator_id") == creator_id and self._owns_series(item.get("series_id", ""), creator_id)
            except Exception:
                allowed = False
            return None if allowed else self._denied()

        analytics = re.fullmatch(r"/api/creators/([^/]+)/analytics(?:/(settings|refresh))?", path)
        if analytics:
            allowed = ((method == "GET" and analytics.group(2) is None)
                       or (method == "PUT" and analytics.group(2) == "settings")
                       or (method == "POST" and analytics.group(2) == "refresh"))
            return None if allowed and analytics.group(1) == creator_id else self._denied()

        # Image actions bind their own resource checks to this exact conversation.
        image_action = re.fullmatch(r"/api/agent/sessions/([^/]+)/image-actions/(read|extract|author|import|reference|skill-asset)", path)
        if image_action and method == "POST":
            return None if image_action.group(1) == session_id else self._denied()

        reference = re.fullmatch(r"/api/series/([^/]+)/(reference-images(?:/[a-f0-9]{64})?|reference-inputs|reference-production)", path)
        if reference and method in {"GET", "POST"}:
            return None if self._owns_series(reference.group(1), creator_id) else self._denied()
        extraction = re.fullmatch(r"/api/skill-extractions/([a-f0-9]{64})(?:/(draft|save|cancel|events)(?:/[0-9]+)?)?", path)
        if extraction:
            from .chat_image_actions import owns_extraction
            readable = method == "GET" and extraction.group(2) in {None, "events"}
            writable = method == "POST" and extraction.group(2) in {"draft", "save", "cancel"}
            return None if (readable or writable) and owns_extraction(self.chat, extraction.group(1), session_id) else self._denied()

        # The shared catalog is read-only and has no account-owned records.
        if method == "GET" and path == "/api/producer-skills":
            if request.query_params.get("include_versions", "").casefold() in {"true", "1", "on", "yes"}:
                return self._version_catalog(request, creator_id)
            return None
        match = _PRODUCER_SKILL_CONTENT.fullmatch(path)
        if method == "GET" and match:
            # Released library instructions remain intentionally shared. A new
            # unreviewed candidate stays within accounts using its Skill family.
            try:
                record = self.research.catalog._record(match.group(1))
            except (AttributeError, ValueError, OSError, KeyError):
                return None  # Preserve the route's normal not-found response.
            return (None if record.get("version_status") != "candidate"
                    or self._owns_skill(match.group(1), creator_id) else self._denied())

        match = re.fullmatch(r"/api/producer-skills/(knowledge-to-carousel|[a-z0-9-]+--[a-f0-9]{16})/versions", path)
        if match and method in {"GET", "POST"}:
            if not self._owns_skill(match.group(1), creator_id):
                return self._denied()
            if method == "GET":
                identity = match.group(1)
                if identity == self.research.catalog.BUILTIN_ID:
                    result = {"family_id": identity, "items": [self._builtin_version()], "bindings": []}
                    with self.db.session() as db:
                        for series in db.scalars(select(Series).where(Series.creator_id == creator_id)):
                            if series.skill_name == identity:
                                result["bindings"].append(dict(series_id=series.id, series_name=series.name,
                                    role="single", skill_id=identity, revision=series.revision))
                else:
                    result = request.app.state.skill_versions.history(identity)
                result["bindings"] = [item for item in result.get("bindings", [])
                                      if self._owns_series(item["series_id"], creator_id)]
                return JSONResponse(result, headers={"Cache-Control": "no-store"})
            return None

        match = re.fullmatch(r"/api/producer-skills/([^/]+)/version-trials", path)
        if match and method == "POST":
            try:
                payload = await request.json()
            except Exception:
                return self._denied()
            selected = payload.get("series_id") if isinstance(payload, dict) else None
            return None if (self._owns_skill(match.group(1), creator_id)
                            and (selected is None or self._owns_series(selected, creator_id))) else self._denied()

        match = _PRODUCER_SKILL_FILES.fullmatch(path)
        if match and (method == "GET" or method == "PUT" and path.endswith("/files/content")):
            return None if self._owns_skill(match.group(1), creator_id) else self._denied()

        match = re.fullmatch(r"/api/producer-skills/(knowledge-to-carousel|[a-z0-9-]+--[a-f0-9]{16})/assets", path)
        if match and method in {"PUT", "DELETE"}:
            return None if self._owns_skill(match.group(1), creator_id) else self._denied()

        if method == "GET" and path == "/api/creators":
            return await self._creator_page(request, creator_id)

        match = _CREATOR_PATH.fullmatch(path)
        if method == "GET" and match:
            return None if match.group(1) == creator_id else self._denied()

        match = _CREATOR_TASKS.fullmatch(path)
        if method == "GET" and match:
            if match.group(1) != creator_id:
                return self._denied()
            series_id = request.query_params.get("series_id")
            if series_id is not None and not self._owns_series(series_id, creator_id):
                return self._denied()
            return None

        match = _SERIES_TOPICS.fullmatch(path)
        if method == "GET" and match:
            return None if self._owns_series(match.group(1), creator_id) else self._denied()

        match = _SERIES_RESEARCH.fullmatch(path)
        if match and method in {"GET", "POST"}:
            return None if self._owns_series(match.group(1), creator_id) else self._denied()

        match = _SERIES_COMPOSITION.fullmatch(path)
        if method == "POST" and match:
            return None if self._owns_series(match.group(1), creator_id) else self._denied()

        match = _SERIES_QUEUE.fullmatch(path)
        if method == "POST" and match:
            if not self._owns_series(match.group(1), creator_id):
                return self._denied()
            try:
                payload = await request.json()
            except Exception:
                return None
            request_id = payload.get("request_id") if isinstance(payload, dict) else None
            return None if self._owns_queue_receipt(match.group(1), request_id, creator_id) else self._denied()

        match = _SERIES_QUEUE_RECEIPT.fullmatch(path)
        if method == "GET" and match:
            return None if (self._owns_series(match.group(1), creator_id)
                           and self._owns_queue_receipt(match.group(1), match.group(2), creator_id)) else self._denied()

        match = _SERIES_DELETE.fullmatch(path)
        if method == "DELETE" and match:
            try:
                payload = await request.json()
            except Exception:
                return None
            request_id = payload.get("request_id") if isinstance(payload, dict) else None
            if self._owns_series(match.group(1), creator_id):
                return None
            return None if self._owns_deleted_series(match.group(1), request_id, creator_id) else self._denied()

        match = _TOPIC.fullmatch(path)
        if match and method in {"GET", "PATCH"}:
            topic_id = match.group(1)
            if not self._owns_topic(topic_id, creator_id):
                return self._denied()
            if method == "PATCH":
                payload = await request.json()
                return None if self._owns_edit_receipt(topic_id, payload.get("request_id"), creator_id) else self._denied()
            return None

        match = _TOPIC_EDIT_RECEIPT.fullmatch(path)
        if method == "GET" and match:
            return None if self._owns_edit_receipt(match.group(1), match.group(2), creator_id) else self._denied()

        match = _TOPIC_REMOVE.fullmatch(path)
        if method == "POST" and match:
            topic_id = match.group(1)
            try:
                payload = await request.json()
            except Exception:
                payload = None
            request_id = payload.get("request_id") if isinstance(payload, dict) else None
            with self.db.session() as transaction:
                replay = transaction.scalar(select(TopicRemoval).where(TopicRemoval.request_id == request_id)) if isinstance(request_id, str) else None
                if replay is not None:
                    # Only the original account may replay its exact request,
                    # even after a no-history series has been reassigned.
                    return None if replay.topic_id == topic_id and replay.creator_id == creator_id else self._denied()
            if self._owns_topic(topic_id, creator_id):
                return None
            with self.db.session() as transaction:
                removal = transaction.get(TopicRemoval, topic_id)
                if removal is not None:
                    return None if self._owns_series(removal.series_id, creator_id) else self._denied()
            batch_id = payload.get("batch_id") if isinstance(payload, dict) else None
            candidate_id = payload.get("candidate_id") if isinstance(payload, dict) else None
            if (isinstance(batch_id, str) and isinstance(candidate_id, str)
                    and topic_id == self.research.topic_id(batch_id, candidate_id)
                    and self._owns_batch(batch_id, creator_id)):
                return None
            return self._denied()

        match = _BATCH.fullmatch(path)
        if method == "GET" and match:
            return None if self._owns_batch(match.group(1), creator_id) else self._denied()

        match = _BATCH_ACTION.fullmatch(path)
        if method == "POST" and match:
            return None if self._owns_batch(match.group(1), creator_id) else self._denied()

        if method == "POST" and path == "/api/series":
            try:
                payload = await request.json()
            except Exception:
                return None  # Let the route return its normal JSON validation error.
            return None if isinstance(payload, dict) and payload.get("creator_id") == creator_id else self._denied()

        if method == "POST" and path == "/api/runs/batch":
            try:
                payload = await request.json()
            except Exception:
                return None
            topic_ids = payload.get("topic_ids") if isinstance(payload, dict) else None
            # Validate the whole boundary before any item can be submitted.
            allowed = (isinstance(topic_ids, list) and bool(topic_ids)
                       and all(isinstance(topic_id, str) and self._owns_topic(topic_id.strip(), creator_id)
                               for topic_id in topic_ids))
            return None if allowed and self._owns_overrides(payload, creator_id) else self._denied()

        if method == "POST" and path == "/api/runs":
            try:
                payload = await request.json()
            except Exception:
                return None
            topic_id = payload.get("topic_id") if isinstance(payload, dict) else None
            allowed = isinstance(topic_id, str) and self._owns_topic(topic_id, creator_id)
            if allowed and payload.get("mode") == "fresh":
                source_id = payload.get("source_run_id")
                allowed = isinstance(source_id, str) and self._owns_run(source_id, creator_id)
            return None if allowed and self._owns_overrides(payload, creator_id) else self._denied()

        match = _RUN.fullmatch(path)
        if method == "GET" and match:
            return None if self._owns_run(match.group(1), creator_id) else self._denied()

        match = re.fullmatch(r"/api/runs/([^/]+)/publication-request", path)
        if match and method in {"GET", "POST"}:
            return None if self._owns_run(match.group(1), creator_id) else self._denied()

        match = re.fullmatch(r"/api/publication-requests/([^/]+)(?:/(reconcile|metrics))?", path)
        if match and ((method == "GET" and match.group(2) is None)
                      or (method == "POST" and match.group(2) in {"reconcile", "metrics"})):
            with self.db.session() as transaction:
                record = transaction.get(PublicationRequest, match.group(1))
                allowed = bool(record and record.creator_id == creator_id
                               and self._owns_run(record.content_run_id, creator_id))
            return None if allowed else self._denied()

        match = _RUN_EXECUTE.fullmatch(path)
        if method == "POST" and match:
            return None if self._owns_run(match.group(1), creator_id) else self._denied()

        match = _RUN_DISCUSSION.fullmatch(path)
        if match and method in {"GET", "POST"}:
            return None if self._owns_run(match.group(1), creator_id) else self._denied()

        match = _RUN_REVISIONS.fullmatch(path)
        if method == "POST" and match:
            return None if self._owns_run(match.group(1), creator_id) else self._denied()

        # Fail closed for every other endpoint, including global mutations,
        # resource transfers, and endpoints added without an explicit scope rule.
        return self._denied()

    def _active_creator(self, creator_id: str) -> bool:
        with self.db.session() as session:
            creator = session.get(Creator, creator_id)
            return bool(creator and creator.is_active)

    def _owns_edit_receipt(self, topic_id: str, request_id, creator_id: str) -> bool:
        with self.db.session() as session:
            receipt = session.get(WriteReceipt, request_id) if isinstance(request_id, str) else None
            if receipt is None:
                return self._owns_topic(topic_id, creator_id)
            data = receipt.response_json or {}
            if (receipt.operation != "edit_topic" or receipt.resource_id != topic_id
                    or data.get("creator_id") != creator_id):
                return False
            series_id = data.get("topic", {}).get("series_id")
            topic = session.get(Topic, topic_id)
            if topic is not None and topic.series_id != series_id:
                return False
            return isinstance(series_id, str) and self._owns_series(series_id, creator_id)

    def _owns_series(self, series_id: str, creator_id: str) -> bool:
        with self.db.session() as session:
            series = session.get(Series, series_id)
            if series is None or series.creator_id != creator_id:
                return False
            runs = session.scalars(
                select(ContentRun).join(Topic, ContentRun.topic_id == Topic.id).where(
                    Topic.series_id == series_id
                )
            )
            # A moved Series may contain a Run whose frozen snapshot names its old
            # Creator. Refuse the whole projection so topic DTOs cannot leak that Run.
            return all((run.input_snapshot_json or {}).get("creator_id") == creator_id for run in runs)

    def _owns_skill(self, skill_id: str, creator_id: str) -> bool:
        with self.db.session() as session:
            series = session.scalars(select(Series).where(Series.creator_id == creator_id))
            bindings = {binding for item in series
                        for binding in (item.skill_name, item.mind_skill_id, item.production_skill_id)
                        if binding}
        if skill_id in bindings:
            return True
        try:
            catalog = self.research.catalog
            candidate = catalog._record(skill_id)
            family_id = candidate.get("family_id", candidate.get("id"))
            if not isinstance(family_id, str) or not family_id:
                return False
            for binding in bindings:
                if binding == catalog.BUILTIN_ID:
                    continue
                try:
                    if catalog._record(binding).get("family_id", binding) == family_id:
                        return True
                except (ValueError, OSError, KeyError):
                    continue
            return False
        except (AttributeError, ValueError, OSError, KeyError):
            return False

    def _owns_overrides(self, payload, creator_id):
        overrides = payload.get("skill_overrides") if isinstance(payload, dict) else None
        if overrides is None:
            return True
        if not isinstance(overrides, dict):
            return False
        identifiers = [overrides[key] for key in ("skill_id", "mind_skill_id", "production_skill_id")
                       if overrides.get(key) is not None]
        return bool(identifiers) and all(isinstance(value, str) and self._owns_skill(value, creator_id)
                                         for value in identifiers)

    def _version_catalog(self, request, creator_id):
        catalog = self.research.catalog
        requested = request.query_params.get("family_skill_id")
        if requested and not self._owns_skill(requested, creator_id):
            return self._denied()
        with self.db.session() as session:
            series = session.scalars(select(Series).where(Series.creator_id == creator_id))
            bindings = {binding for item in series
                        for binding in (item.skill_name, item.mind_skill_id, item.production_skill_id) if binding}
        items = {}
        for identity in ([requested] if requested else bindings):
            if identity == catalog.BUILTIN_ID:
                items[identity] = self._builtin_version()
                continue
            try:
                for item in catalog.list_skill_versions(identity)["items"]:
                    items[item["id"]] = item
            except (ValueError, OSError, KeyError):
                continue
        return JSONResponse({"items": list(items.values()), "include_versions": True}, headers={"Cache-Control": "no-store"})

    def _builtin_version(self):
        catalog = self.research.catalog
        item = next(row for row in catalog.list() if row["id"] == catalog.BUILTIN_ID)
        return {**item, "family_id": catalog.BUILTIN_ID, "version_number": 1,
                "version_status": "released", "digest": catalog.list_skill_files(catalog.BUILTIN_ID)["digest"]}

    def _owns_queue_receipt(self, series_id: str, request_id, creator_id: str) -> bool:
        if not isinstance(request_id, str):
            return True  # Route validation rejects malformed requests.
        with self.db.session() as session:
            receipt = session.get(WriteReceipt, request_id)
            if receipt is None:
                return True
            return (receipt.operation == "queue_topics" and receipt.resource_id == series_id
                    and receipt.response_json.get("creator_id") == creator_id)

    def _owns_deleted_series(self, series_id: str, request_id, creator_id: str) -> bool:
        if not isinstance(request_id, str):
            return False
        with self.db.session() as session:
            receipt = session.get(WriteReceipt, request_id)
            return bool(
                receipt
                and receipt.operation == "delete_series"
                and receipt.resource_id == series_id
                and receipt.response_json.get("creator_id") == creator_id
            )

    def _owns_topic(self, topic_id: str, creator_id: str) -> bool:
        with self.db.session() as session:
            topic = session.get(Topic, topic_id)
            if topic is None:
                return False
            series = session.get(Series, topic.series_id)
            if series is None or not self._owns_series(series.id, creator_id):
                return False
            return series.creator_id == creator_id

    def _owns_batch(self, batch_id: str, creator_id: str) -> bool:
        try:
            record = self.research._load(batch_id)
        except Exception:
            return False
        return isinstance(record, dict) and self._owns_series(record.get("series_id", ""), creator_id)

    def _owns_run(self, run_id: str, creator_id: str) -> bool:
        with self.db.session() as session:
            run = session.get(ContentRun, run_id)
            if run is None:
                return False
            snapshot = run.input_snapshot_json or {}
            topic = session.get(Topic, run.topic_id)
            series = session.get(Series, topic.series_id) if topic else None
            return bool(snapshot.get("creator_id") == creator_id and series
                        and series.creator_id == creator_id)

    async def _creator_page(self, request: Request, creator_id: str) -> Response:
        try:
            offset = int(request.query_params.get("offset", "0"))
            limit = int(request.query_params.get("limit", "50"))
        except ValueError:
            return self._invalid_page()
        if offset < 0 or not 1 <= limit <= 100:
            return self._invalid_page()
        creator = request.app.state.queries.get_creator(creator_id)
        item = creator.model_dump(mode="json") if creator is not None else None
        return JSONResponse({
            "items": [item] if item is not None and offset == 0 else [],
            "page": {"offset": offset, "limit": limit, "total": 1 if item is not None else 0},
        })

    @staticmethod
    def _invalid_page() -> JSONResponse:
        return JSONResponse(status_code=422, content={"error": {
            "code": "invalid_request", "message": "分页参数不符合接口契约。"
        }})

    @staticmethod
    def _denied() -> JSONResponse:
        return JSONResponse(status_code=403, content={"error": {
            "code": "agent_scope_rejected", "message": "该 Agent 会话不能访问此账号资源。"
        }})
