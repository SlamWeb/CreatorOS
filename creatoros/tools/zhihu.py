from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict

from ..context import RuntimeContext
from ..integrations.zhihu import ZhihuOpenAPIClient, ZhihuOpenAPIError
from .results import ToolResult

_client_factory: Callable[[], ZhihuOpenAPIClient] = ZhihuOpenAPIClient.from_env


def get_zhihu_hot_list(
    limit: int = 10,
    context: RuntimeContext | None = None,
) -> ToolResult:
    del context
    client = _client_factory()
    try:
        snapshot = client.get_hot_list(limit)
    except ZhihuOpenAPIError as error:
        return ToolResult(
            content=str(error),
            is_error=True,
            error_type=error.error_type,
            retryable=error.retryable,
            details=error.details,
        )
    finally:
        client.close()

    result = {
        "source": snapshot.source,
        "total": snapshot.total,
        "topics": [
            {
                "rank": topic.rank,
                "title": topic.title,
                "url": topic.url,
                "summary": topic.summary,
                "thumbnail_url": topic.thumbnail_url,
            }
            for topic in snapshot.topics
        ],
    }
    return ToolResult(content=json.dumps(result, ensure_ascii=False))


def search_zhihu(
    query: str,
    count: int = 10,
    context: RuntimeContext | None = None,
) -> ToolResult:
    del context
    client = _client_factory()
    try:
        snapshot = client.search(query, count)
    except ZhihuOpenAPIError as error:
        return ToolResult(
            content=str(error),
            is_error=True,
            error_type=error.error_type,
            retryable=error.retryable,
            details=error.details,
        )
    finally:
        client.close()

    result = {
        "source": "zhihu",
        "query": snapshot.query,
        "search_hash_id": snapshot.search_hash_id,
        "has_more": snapshot.has_more,
        "empty_reason": snapshot.empty_reason,
        "items": [asdict(item) for item in snapshot.items],
    }
    return ToolResult(content=json.dumps(result, ensure_ascii=False))
