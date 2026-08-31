from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterator, Literal
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from ..config import (
    PERSONCLONE_BASE_URL,
    PERSONCLONE_SESSION_COOKIE,
    PERSONCLONE_TIMEOUT_SECONDS,
)
from ..routing import RoutingProfileEnvelope

PERSONCLONE_SESSION_COOKIE_NAME = "personaforge_session"


class PersonCloneError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_type: str = "personclone_error",
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.error_type = error_type
        self.retryable = retryable
        self.details = details or {}


@dataclass(frozen=True)
class PersonaAnswer:
    author: str
    answer: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    trace_id: str | None = None


class AuthorJobStatus(BaseModel):
    """Typed status returned by PersonClone's persistent author-job API."""

    model_config = ConfigDict(extra="ignore")

    id: str
    author: str
    status: Literal["queued", "running", "ready", "failed", "cancelled", "interrupted"]
    stage: str
    label: str
    operation: str | None = None
    display_name: str | None = None
    error_message: str | None = None
    cancel_requested: bool = False
    routing_profile_status: str | None = None
    routing_profile_corpus_version: str | None = None
    domain_prototype_count: int | None = None
    perspective_prototype_count: int | None = None
    created_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in {"ready", "failed", "cancelled", "interrupted"}

    @property
    def is_ready(self) -> bool:
        return self.status == "ready" and self.stage == "ready"


class PersonCloneClient:
    """Thin HTTP client for the already-running PersonClone FastAPI service."""

    def __init__(
        self,
        base_url: str = PERSONCLONE_BASE_URL,
        session_cookie: str = PERSONCLONE_SESSION_COOKIE,
        timeout: float = PERSONCLONE_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ):
        cookies = {}
        if session_cookie:
            cookies[PERSONCLONE_SESSION_COOKIE_NAME] = session_cookie
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            cookies=cookies,
            transport=transport,
        )

    @classmethod
    def from_env(cls) -> "PersonCloneClient":
        return cls()

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "PersonCloneClient":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def list_personas(self) -> dict[str, Any]:
        response = self._request("GET", "/api/personas", operation="列出作者")
        return self._json_object(response, "作者列表")

    def get_routing_profile(self, author: str) -> RoutingProfileEnvelope:
        encoded_author = quote(author, safe="")
        response = self._request(
            "GET",
            f"/api/personas/{encoded_author}/routing-profile",
            operation="获取作者路由画像",
        )
        payload = self._json_object(response, "作者路由画像")
        try:
            return RoutingProfileEnvelope.model_validate(payload)
        except ValidationError as error:
            raise PersonCloneError(
                "PersonClone 作者路由画像不符合 CreatorOS 数据合同。",
                error_type="personclone_protocol_error",
                details={"validation_errors": error.errors()},
            ) from error

    def add_author(
        self,
        author: str,
        kinds: list[str],
        max_items: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"author": author, "kinds": kinds}
        if max_items is not None:
            payload["max_items"] = max_items
        response = self._request(
            "POST",
            "/api/author-jobs",
            payload=payload,
            operation="添加作者",
        )
        return self._json_object(response, "添加作者")

    def get_author_job(self, job_id: str) -> AuthorJobStatus:
        encoded_job_id = quote(job_id, safe="")
        response = self._request(
            "GET",
            f"/api/author-jobs/{encoded_job_id}",
            operation="查询作者任务",
        )
        payload = self._json_object(response, "作者任务")
        try:
            return AuthorJobStatus.model_validate(payload)
        except ValidationError as error:
            raise PersonCloneError(
                "PersonClone 作者任务状态不符合 CreatorOS 数据合同。",
                error_type="personclone_protocol_error",
                details={"validation_errors": error.errors()},
            ) from error

    def ask_author(
        self,
        author: str,
        question: str,
        *,
        query_mode: str = "grounded",
        writer_prompt: str = "strong_identity",
        parent_top_k: int = 20,
    ) -> PersonaAnswer:
        payload = {
            "author": author,
            "query": question,
            "query_mode": query_mode,
            "writer_prompt": writer_prompt,
            "parent_top_k": parent_top_k,
        }
        try:
            with self._http.stream(
                "POST",
                "/api/chat/stream",
                json=payload,
                headers={"Accept": "text/event-stream"},
            ) as response:
                if response.status_code >= 400:
                    self._raise_http_error(response, "回答作者")
                return self._read_answer(response, author)
        except PersonCloneError:
            raise
        except httpx.TimeoutException as error:
            raise PersonCloneError(
                "PersonClone 回答超时。",
                error_type="personclone_timeout",
                retryable=True,
            ) from error
        except httpx.RequestError as error:
            raise PersonCloneError(
                f"无法连接 PersonClone：{error}",
                error_type="personclone_unavailable",
                retryable=True,
            ) from error

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        operation: str,
    ) -> httpx.Response:
        try:
            response = self._http.request(method, path, json=payload)
        except httpx.TimeoutException as error:
            raise PersonCloneError(
                f"PersonClone {operation}超时。",
                error_type="personclone_timeout",
                retryable=True,
            ) from error
        except httpx.RequestError as error:
            raise PersonCloneError(
                f"无法连接 PersonClone：{error}",
                error_type="personclone_unavailable",
                retryable=True,
            ) from error

        if response.status_code >= 400:
            self._raise_http_error(response, operation)
        return response

    @staticmethod
    def _json_object(response: httpx.Response, operation: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as error:
            raise PersonCloneError(
                f"PersonClone {operation}返回的不是 JSON。",
                error_type="personclone_protocol_error",
            ) from error
        if not isinstance(payload, dict):
            raise PersonCloneError(
                f"PersonClone {operation}返回的 JSON 不是 object。",
                error_type="personclone_protocol_error",
            )
        return payload

    @staticmethod
    def _raise_http_error(response: httpx.Response, operation: str) -> None:
        try:
            response.read()
        except RuntimeError:
            pass
        try:
            payload = response.json()
            detail = payload.get("detail") or payload.get("error") or payload
        except ValueError:
            detail = response.text[:500]

        status = response.status_code
        if status in (401, 403):
            error_type = "personclone_auth"
            retryable = False
            message = f"PersonClone {operation}需要有效的登录会话。"
        elif status == 404:
            error_type = "personclone_not_found"
            retryable = False
            message = f"PersonClone 找不到请求的作者或接口：{detail}"
        elif status == 429 or status >= 500:
            error_type = "personclone_unavailable"
            retryable = True
            message = f"PersonClone 暂时不可用：{detail}"
        else:
            error_type = "personclone_http_error"
            retryable = False
            message = f"PersonClone {operation}失败（HTTP {status}）：{detail}"
        raise PersonCloneError(
            message,
            error_type=error_type,
            retryable=retryable,
            details={"status_code": status},
        )

    @classmethod
    def _read_answer(cls, response: httpx.Response, author: str) -> PersonaAnswer:
        tokens: list[str] = []
        done_payload: dict[str, Any] | None = None
        meta_payload: dict[str, Any] = {}

        for event_name, payload in cls._iter_sse_events(response):
            if event_name == "meta" and isinstance(payload, dict):
                meta_payload = payload
            elif event_name == "token" and isinstance(payload, dict):
                text = payload.get("text")
                if isinstance(text, str):
                    tokens.append(text)
            elif event_name == "error":
                detail = payload.get("error") if isinstance(payload, dict) else payload
                raise PersonCloneError(
                    f"PersonClone 生成失败：{detail}",
                    error_type="personclone_generation_error",
                    details=payload if isinstance(payload, dict) else {},
                )
            elif event_name == "done" and isinstance(payload, dict):
                done_payload = payload

        if done_payload is None:
            raise PersonCloneError(
                "PersonClone 流结束前没有收到 done 事件。",
                error_type="personclone_protocol_error",
                retryable=True,
            )

        answer = done_payload.get("answer") or "".join(tokens)
        if not isinstance(answer, str) or not answer.strip():
            raise PersonCloneError(
                "PersonClone 返回了空回答。",
                error_type="personclone_empty_answer",
            )

        sources = done_payload.get("sources") or []
        if not isinstance(sources, list):
            sources = []
        trace_id = done_payload.get("trace_id") or meta_payload.get("trace_id")
        return PersonaAnswer(
            author=author,
            answer=answer,
            sources=sources,
            trace_id=trace_id if isinstance(trace_id, str) else None,
        )

    @staticmethod
    def _iter_sse_events(response: httpx.Response) -> Iterator[tuple[str, Any]]:
        event_name: str | None = None
        data_lines: list[str] = []

        def flush() -> tuple[str, Any] | None:
            nonlocal event_name, data_lines
            if not data_lines:
                event_name = None
                return None
            raw_data = "\n".join(data_lines)
            current_event = event_name or "message"
            event_name = None
            data_lines = []
            try:
                payload = json.loads(raw_data)
            except json.JSONDecodeError:
                payload = {"text": raw_data}
            return current_event, payload

        for raw_line in response.iter_lines():
            line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
            if not line:
                event = flush()
                if event is not None:
                    yield event
            elif line.startswith(":"):
                continue
            elif line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())

        event = flush()
        if event is not None:
            yield event


class AsyncPersonCloneClient:
    """Native-async HTTP client for concurrent PersonClone SSE streams."""

    def __init__(
        self,
        base_url: str = PERSONCLONE_BASE_URL,
        session_cookie: str = PERSONCLONE_SESSION_COOKIE,
        timeout: float = PERSONCLONE_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        cookies = {}
        if session_cookie:
            cookies[PERSONCLONE_SESSION_COOKIE_NAME] = session_cookie
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            cookies=cookies,
            transport=transport,
        )

    @classmethod
    def from_env(cls) -> "AsyncPersonCloneClient":
        return cls()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "AsyncPersonCloneClient":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.aclose()

    async def ask_author(
        self,
        author: str,
        question: str,
        *,
        query_mode: str = "grounded",
        writer_prompt: str = "strong_identity",
        parent_top_k: int = 20,
    ) -> PersonaAnswer:
        payload = {
            "author": author,
            "query": question,
            "query_mode": query_mode,
            "writer_prompt": writer_prompt,
            "parent_top_k": parent_top_k,
        }
        try:
            async with self._http.stream(
                "POST",
                "/api/chat/stream",
                json=payload,
                headers={"Accept": "text/event-stream"},
            ) as response:
                if response.status_code >= 400:
                    await self._raise_http_error(response, "回答作者")
                return await self._read_answer(response, author)
        except PersonCloneError:
            raise
        except httpx.TimeoutException as error:
            raise PersonCloneError(
                "PersonClone 回答超时。",
                error_type="personclone_timeout",
                retryable=True,
            ) from error
        except httpx.RequestError as error:
            raise PersonCloneError(
                f"无法连接 PersonClone：{error}",
                error_type="personclone_unavailable",
                retryable=True,
            ) from error

    @staticmethod
    async def _raise_http_error(response: httpx.Response, operation: str) -> None:
        try:
            await response.aread()
        except RuntimeError:
            pass
        try:
            payload = response.json()
            detail = payload.get("detail") or payload.get("error") or payload
        except ValueError:
            detail = response.text[:500]

        status = response.status_code
        if status in (401, 403):
            error_type = "personclone_auth"
            retryable = False
            message = f"PersonClone {operation}需要有效的登录会话。"
        elif status == 404:
            error_type = "personclone_not_found"
            retryable = False
            message = f"PersonClone 找不到请求的作者或接口：{detail}"
        elif status == 429 or status >= 500:
            error_type = "personclone_unavailable"
            retryable = True
            message = f"PersonClone 暂时不可用：{detail}"
        else:
            error_type = "personclone_http_error"
            retryable = False
            message = f"PersonClone {operation}失败（HTTP {status}）：{detail}"
        raise PersonCloneError(
            message,
            error_type=error_type,
            retryable=retryable,
            details={"status_code": status},
        )

    @classmethod
    async def _read_answer(cls, response: httpx.Response, author: str) -> PersonaAnswer:
        tokens: list[str] = []
        done_payload: dict[str, Any] | None = None
        meta_payload: dict[str, Any] = {}

        async for event_name, payload in cls._iter_sse_events(response):
            if event_name == "meta" and isinstance(payload, dict):
                meta_payload = payload
            elif event_name == "token" and isinstance(payload, dict):
                text = payload.get("text")
                if isinstance(text, str):
                    tokens.append(text)
            elif event_name == "error":
                detail = payload.get("error") if isinstance(payload, dict) else payload
                raise PersonCloneError(
                    f"PersonClone 生成失败：{detail}",
                    error_type="personclone_generation_error",
                    details=payload if isinstance(payload, dict) else {},
                )
            elif event_name == "done" and isinstance(payload, dict):
                done_payload = payload

        if done_payload is None:
            raise PersonCloneError(
                "PersonClone 流结束前没有收到 done 事件。",
                error_type="personclone_protocol_error",
                retryable=True,
            )

        answer = done_payload.get("answer") or "".join(tokens)
        if not isinstance(answer, str) or not answer.strip():
            raise PersonCloneError(
                "PersonClone 返回了空回答。",
                error_type="personclone_empty_answer",
            )

        sources = done_payload.get("sources") or []
        if not isinstance(sources, list):
            sources = []
        trace_id = done_payload.get("trace_id") or meta_payload.get("trace_id")
        return PersonaAnswer(
            author=author,
            answer=answer,
            sources=sources,
            trace_id=trace_id if isinstance(trace_id, str) else None,
        )

    @staticmethod
    async def _iter_sse_events(response: httpx.Response) -> AsyncIterator[tuple[str, Any]]:
        event_name: str | None = None
        data_lines: list[str] = []

        def flush() -> tuple[str, Any] | None:
            nonlocal event_name, data_lines
            if not data_lines:
                event_name = None
                return None
            raw_data = "\n".join(data_lines)
            current_event = event_name or "message"
            event_name = None
            data_lines = []
            try:
                payload = json.loads(raw_data)
            except json.JSONDecodeError:
                payload = {"text": raw_data}
            return current_event, payload

        async for line in response.aiter_lines():
            if not line:
                event = flush()
                if event is not None:
                    yield event
            elif line.startswith(":"):
                continue
            elif line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())

        event = flush()
        if event is not None:
            yield event
