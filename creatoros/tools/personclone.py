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
        "recommended_writer_prompt",
        "profile_url",
        "last_synced_at",
    )
    result = {field: item.get(field) for field in fields if field in item}
    if "recommended_writer_prompt" not in result:
        result["recommended_writer_prompt"] = (
            "mrprompt" if item.get("narrative_schema_available") else "strong_identity"
        )
    return result


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
        status = str(job.get("status") or "queued")
        display_name = job.get("display_name") or author
        return ToolResult(
            content=(
                f"作者 {display_name} 的索引任务已提交，当前状态：{status}。"
                f"任务句柄：{job.get('id') or '不可用'}。"
            ),
            details={
                "task_id": job.get("id"),
                "kind": "author_index",
                "author": job.get("author") or author,
                "status": status,
                "stage": job.get("stage"),
                "label": job.get("label"),
                "updated_at": job.get("updated_at"),
                "error_message": job.get("error_message"),
            },
        )

    return _run_with_client(operation)


def get_author_job(
    job_id: str,
    context: RuntimeContext | None = None,
) -> ToolResult:
    del context

    def operation(client: PersonCloneClient) -> ToolResult:
        job = client.get_author_job(job_id)
        status_text = f"{job.status}/{job.stage}"
        content = f"作者任务 {job.id} 当前状态：{status_text}。{job.label}"
        if job.error_message:
            content += f"错误：{job.error_message}"
        return ToolResult(
            content=content,
            is_error=job.status in {"failed", "cancelled", "interrupted"},
            error_type="personclone_job_failed" if job.status == "failed" else None,
            details={
                "task_id": job.id,
                "kind": "author_index",
                "author": job.author,
                "status": job.status,
                "stage": job.stage,
                "label": job.label,
                "updated_at": job.updated_at,
                "error_message": job.error_message,
            },
        )

    return _run_with_client(operation)


def ask_author(
    author: str,
    question: str,
    query_mode: str = "grounded",
    writer_prompt: str = "strong_identity",
    parent_top_k: int = 20,
    context: RuntimeContext | None = None,
) -> ToolResult:
    del context

    def operation(client: PersonCloneClient) -> ToolResult:
        answer = client.ask_author(
            author,
            question,
            query_mode=query_mode,
            writer_prompt=writer_prompt,
            parent_top_k=parent_top_k,
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
