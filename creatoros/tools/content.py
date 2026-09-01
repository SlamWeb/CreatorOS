from __future__ import annotations

import json
from collections.abc import Callable

from ..context import RuntimeContext
from ..integrations.codex import CodexProducer, CodexProducerError
from .results import ToolResult

_producer_factory: Callable[[], CodexProducer] = CodexProducer.from_defaults


def produce_content_pack(
    creator_id: str,
    series_id: str,
    topic_id: str,
    topic_title: str,
    context: RuntimeContext | None = None,
) -> ToolResult:
    del context
    try:
        produced = _producer_factory().produce(
            creator_id=creator_id,
            series_id=series_id,
            topic_id=topic_id,
            topic_title=topic_title,
        )
    except CodexProducerError as error:
        return ToolResult(
            content=str(error),
            is_error=True,
            error_type=error.error_type,
        )
    payload = {
        "status": "completed",
        "pack_id": produced.pack.pack_id,
        "output_directory": str(produced.directory),
        "manifest": str(produced.directory / "social_content_pack.json"),
        "thread_id": produced.session.thread_id,
        "card_count": len(produced.pack.cards),
        "title": produced.pack.publish_copy.title,
    }
    return ToolResult(
        content=json.dumps(payload, ensure_ascii=False),
        details={
            "pack_id": produced.pack.pack_id,
            "thread_id": produced.session.thread_id,
            "card_count": len(produced.pack.cards),
            "output_directory": str(produced.directory),
        },
    )
