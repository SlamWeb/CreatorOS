from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import uuid4

from ...ai.types import ToolCall
from ...context import RuntimeContext
from ...planning import SelectionAssignment
from ...tools.execution import execute_tool_call
from ...tools.results import ToolResult


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


def _execute_assignment(
    assignment: SelectionAssignment,
    context: RuntimeContext | None,
) -> ToolResult:
    arguments = {
        "author": assignment.author_id,
        "question": build_assignment_question(assignment),
        "query_mode": "grounded",
        "writer_prompt": "strong_identity",
        "parent_top_k": 20,
    }
    return execute_tool_call(
        ToolCall(
            id=f"batch-answer-{uuid4().hex}",
            name="ask_author",
            arguments=json.dumps(arguments, ensure_ascii=False),
        ),
        context=context,
    )


async def answer_assignments(
    assignments: Sequence[SelectionAssignment],
    *,
    max_concurrency: int = 3,
    context: RuntimeContext | None = None,
) -> tuple[AssignmentAnswer, ...]:
    """Run existing synchronous ask_author calls concurrently in worker threads."""
    if max_concurrency < 1:
        raise ValueError("max_concurrency 必须大于 0。")
    semaphore = asyncio.Semaphore(max_concurrency)

    async def answer_one(assignment: SelectionAssignment) -> AssignmentAnswer:
        async with semaphore:
            result = await asyncio.to_thread(_execute_assignment, assignment, context)
            return AssignmentAnswer(assignment=assignment, result=result)

    return tuple(await asyncio.gather(*(answer_one(item) for item in assignments)))
