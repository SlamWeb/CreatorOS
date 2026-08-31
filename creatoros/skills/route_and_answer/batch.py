from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ...context import RuntimeContext
from ...integrations.personclone import AsyncPersonCloneClient, PersonCloneError
from ...planning import SelectionAssignment
from ...tools.results import ToolResult

_async_client_factory: Callable[[], AsyncPersonCloneClient] = AsyncPersonCloneClient.from_env


@dataclass(frozen=True)
class AssignmentAnswer:
    """One assignment paired with its isolated Tool result."""

    assignment: SelectionAssignment
    result: ToolResult

    @property
    def succeeded(self) -> bool:
        return not self.result.is_error


def build_assignment_question(assignment: SelectionAssignment) -> str:
    parts = [assignment.title]
    if assignment.summary:
        parts.append(f"问题介绍：{assignment.summary}")
    if assignment.instruction:
        parts.append(f"额外要求：{assignment.instruction}")
    return "\n\n".join(parts)


async def _execute_assignment(
    client: AsyncPersonCloneClient,
    assignment: SelectionAssignment,
    context: RuntimeContext | None,
) -> ToolResult:
    del context
    try:
        answer = await client.ask_author(
            assignment.author_id,
            build_assignment_question(assignment),
            query_mode="grounded",
            writer_prompt="strong_identity",
            parent_top_k=20,
        )
        return ToolResult(
            content=answer.answer,
            details={
                "author": answer.author,
                "sources": answer.sources,
                "trace_id": answer.trace_id,
            },
        )
    except PersonCloneError as error:
        return ToolResult(
            content=str(error),
            is_error=True,
            error_type=error.error_type,
            retryable=error.retryable,
            details=error.details,
        )
    except Exception as error:
        return ToolResult(
            content=f"批量回答执行失败：{error}",
            is_error=True,
            error_type="tool_exception",
            details={"exception_type": type(error).__name__},
        )


async def answer_assignments(
    assignments: Sequence[SelectionAssignment],
    *,
    max_concurrency: int = 3,
    context: RuntimeContext | None = None,
) -> tuple[AssignmentAnswer, ...]:
    """Run native-async PersonClone streams with a bounded concurrency limit."""
    if max_concurrency < 1:
        raise ValueError("max_concurrency 必须大于 0。")
    if not assignments:
        return ()
    semaphore = asyncio.Semaphore(max_concurrency)

    async def answer_one(
        client: AsyncPersonCloneClient,
        assignment: SelectionAssignment,
    ) -> AssignmentAnswer:
        async with semaphore:
            result = await _execute_assignment(client, assignment, context)
            return AssignmentAnswer(assignment=assignment, result=result)

    async with _async_client_factory() as client:
        return tuple(
            await asyncio.gather(*(answer_one(client, item) for item in assignments))
        )
