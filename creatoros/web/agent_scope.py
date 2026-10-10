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

from creatoros.storage import ContentRun, Creator, Database, Series, Topic, TopicRemoval, WriteReceipt


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
_CREATOR_TASKS = re.compile(r"^/api/creators/([^/]+)/tasks$")
_PRODUCER_SKILL_CONTENT = re.compile(r"^/api/producer-skills/(?:knowledge-to-carousel|[a-z0-9-]+--[a-f0-9]{16})/content$")
_PRODUCER_SKILL_FILES = re.compile(
    r"^/api/producer-skills/(knowledge-to-carousel|[a-z0-9-]+--[a-f0-9]{16})/files(?:/(?:content|text))?$"
)


class AgentScopeGuard:
    """Check every request carrying a persisted creator-scoped Agent session."""

    def __init__(self, db: Database, chat, research, discussions=None):
        self.db = db
        self.chat = chat
        self.research = research
        self.discussions = discussions

    async def check(self, request: Request) -> Response | None:
        session_id = request.headers.get("x-creatoros-agent-session")
        if not session_id:
            return None
        try:
            session = self.chat.get(session_id)
        except Exception:
            return self._denied()

        if session.get("scope_kind", "overview") != "creator":
            return None
        creator_id = session.get("creator_id")
        if not isinstance(creator_id, str) or not self._active_creator(creator_id):
            return self._denied()

        method, path = request.method.upper(), request.url.path

        # The shared catalog is read-only and has no account-owned records.
        if method == "GET" and path == "/api/producer-skills":
            return None
        if method == "GET" and _PRODUCER_SKILL_CONTENT.fullmatch(path):
            return None

        match = _PRODUCER_SKILL_FILES.fullmatch(path)
        if match and (method == "GET" or method == "PUT" and path.endswith("/files/content")):
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

        if method == "POST" and path == "/api/runs":
            try:
                payload = await request.json()
            except Exception:
                return None
            topic_id = payload.get("topic_id") if isinstance(payload, dict) else None
            return None if isinstance(topic_id, str) and self._owns_topic(topic_id, creator_id) else self._denied()

        match = _RUN.fullmatch(path)
        if method == "GET" and match:
            return None if self._owns_run(match.group(1), creator_id) else self._denied()

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
            return any(skill_id in {item.skill_name, item.mind_skill_id, item.production_skill_id}
                       for item in series)

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
