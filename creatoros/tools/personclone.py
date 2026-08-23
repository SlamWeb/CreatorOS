from __future__ import annotations

import json
from collections.abc import Callable

from ..context import RuntimeContext
from ..integrations.personclone import PersonCloneClient, PersonCloneError
from .models import AddAuthorArgs, AskAuthorArgs
from .results import ToolResult

_client_factory: Callable[[], PersonCloneClient] = PersonCloneClient.from_env


def _run_with_client(operation):
    client = _client_factory()
    try:
        return operation(client)
    except PersonCloneError as error:
        return ToolResult(
            content=str(error),
            is_error=True,
            error_type=error.error_type,
            retryable=error.retryable,
            details=error.details,
        )
    finally:
        client.close()


def _public_persona(item: dict) -> dict:
    fields = (
        "author",
        "display_name",
        "headline",
        "content_count",
        "persona_pack_available",
        "narrative_schema_available",
        "profile_url",
        "last_synced_at",
    )
    return {field: item.get(field) for field in fields if field in item}


def list_authors(context: RuntimeContext | None = None) -> ToolResult:
    del context

    def operation(client: PersonCloneClient) -> ToolResult:
        payload = client.list_personas()
        personas = payload.get("personas", [])
        if not isinstance(personas, list):
            personas = []
        result = {
            "authors": [_public_persona(item) for item in personas if isinstance(item, dict)],
            "default_author": payload.get("default_author"),
        }
        return ToolResult(content=json.dumps(result, ensure_ascii=False))

    return _run_with_client(operation)


def add_author(
    author: str,
    kinds: list[str] | None = None,
    max_items: int | None = None,
    context: RuntimeContext | None = None,
) -> ToolResult:
    del context
    selected_kinds = kinds or ["answer", "article", "pin"]

    def operation(client: PersonCloneClient) -> ToolResult:
        job = client.add_author(author, selected_kinds, max_items)
        return ToolResult(content=json.dumps(job, ensure_ascii=False))

    return _run_with_client(operation)


def ask_author(
    author: str,
    question: str,
    query_mode: str = "grounded",
    writer_prompt: str = "mrprompt",
    context: RuntimeContext | None = None,
) -> ToolResult:
    del context

    def operation(client: PersonCloneClient) -> ToolResult:
        answer = client.ask_author(
            author,
            question,
            query_mode=query_mode,
            writer_prompt=writer_prompt,
        )
        return ToolResult(
            content=answer.answer,
            details={
                "author": answer.author,
                "sources": answer.sources,
                "trace_id": answer.trace_id,
            },
        )

    return _run_with_client(operation)
