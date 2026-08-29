from __future__ import annotations

import json
from collections.abc import Callable

from ..context import RuntimeContext
from ..integrations.personclone import PersonCloneClient, PersonCloneError
from ..integrations.zhihu import ZhihuOpenAPIClient, ZhihuOpenAPIError
from ..planning import DailyPlan, build_daily_plans
from ..routing import (
    BGEEmbeddingProvider,
    EmbeddedRoutePrototype,
    EmbeddingError,
    RoutingEmbeddingCache,
    build_domain_query,
)
from ..routing.projection import project_profile
from .results import ToolResult

_zhihu_client_factory: Callable[[], ZhihuOpenAPIClient] = ZhihuOpenAPIClient.from_env
_personclone_client_factory: Callable[[], PersonCloneClient] = PersonCloneClient.from_env
_embedding_cache_factory: Callable[[], RoutingEmbeddingCache] = RoutingEmbeddingCache.from_defaults
MAX_QUEUE_SUMMARY_CHARS = 800


def _plan_to_dict(plan: DailyPlan, display_names: dict[str, str]) -> dict:
    opportunities = []
    for item in plan.hot:
        summary = item.hotspot_summary.strip()
        opportunities.append(
            {
                "rank": item.hotspot_rank,
                "title": item.hotspot_title,
                "url": item.hotspot_url,
                "summary": summary[:MAX_QUEUE_SUMMARY_CHARS],
                "summary_truncated": len(summary) > MAX_QUEUE_SUMMARY_CHARS,
                "score": round(item.score, 4),
                "matched_domain": item.matched_domain_label,
                "prototype_id": item.matched_prototype_id,
                "corpus_version": item.profile_corpus_version,
            }
        )
    return {
        "author_id": plan.author_id,
        "display_name": display_names.get(plan.author_id, plan.author_id),
        "hot": opportunities,
        "evergreen": [],
        "experiment": [],
    }


def route_hotspots(
    limit: int = 10,
    top_k: int = 3,
    context: RuntimeContext | None = None,
) -> ToolResult:
    """Build author-side hotspot queues from live Zhihu and PersonClone data."""
    del context
    zhihu = None
    personclone = None
    try:
        zhihu = _zhihu_client_factory()
        personclone = _personclone_client_factory()
        hot_list = zhihu.get_hot_list(limit)
        personas_payload = personclone.list_personas()
        personas = personas_payload.get("personas", [])
        if not isinstance(personas, list):
            raise PersonCloneError(
                "PersonClone 作者列表返回的数据结构无效。",
                error_type="personclone_protocol_error",
            )

        display_names: dict[str, str] = {}
        documents = []
        skipped_authors = []
        for item in personas:
            if not isinstance(item, dict) or not isinstance(item.get("author"), str):
                continue
            author = item["author"]
            display_names[author] = str(item.get("display_name") or author)
            try:
                profile = personclone.get_routing_profile(author).profile
            except PersonCloneError as error:
                skipped_authors.append(
                    {"author_id": author, "reason": str(error), "error_type": error.error_type}
                )
                continue
            if not profile.can_use_domain:
                skipped_authors.append(
                    {"author_id": author, "reason": f"画像状态不可用于领域匹配：{profile.status}"}
                )
                continue
            documents.extend(
                doc for doc in project_profile(profile) if doc.prototype_type == "domain"
            )

        if not documents:
            return ToolResult(
                content="没有可用于领域匹配的作者路由画像。",
                is_error=True,
                error_type="no_matchable_authors",
                details={"skipped_authors": skipped_authors},
            )

        embedder = BGEEmbeddingProvider()
        embedding_cache = _embedding_cache_factory()
        embedded_by_id: dict[str, EmbeddedRoutePrototype] = {}
        missing_documents = []
        cache_hit_count = 0
        for document in documents:
            cached_vector = embedding_cache.get(document)
            if cached_vector is None:
                missing_documents.append(document)
                continue
            embedded_by_id[document.doc_id] = EmbeddedRoutePrototype(document, cached_vector)
            cache_hit_count += 1

        if missing_documents:
            for embedded in embedder.embed_documents(missing_documents):
                embedded_by_id[embedded.document.doc_id] = embedded
                embedding_cache.put(embedded.document, embedded.vector)
            try:
                embedding_cache.save()
            except OSError:
                pass

        embedded_domains = tuple(embedded_by_id[document.doc_id] for document in documents)
        query_vectors = embedder.embed_texts(
            [build_domain_query(topic) for topic in hot_list.topics]
        )
        plans = build_daily_plans(
            hot_list.topics,
            query_vectors,
            embedded_domains,
            top_k=top_k,
        )
        result = {
            "source": hot_list.source,
            "hotspot_count": len(hot_list.topics),
            "author_count": len(plans),
            "top_k": top_k,
            "matching": "domain_max_similarity",
            "plans": [_plan_to_dict(plan, display_names) for plan in plans],
            "skipped_authors": skipped_authors,
        }
        return ToolResult(
            content=json.dumps(result, ensure_ascii=False),
            details={
                "hotspot_count": len(hot_list.topics),
                "author_count": len(plans),
                "top_k": top_k,
                "skipped_author_count": len(skipped_authors),
                "prototype_cache_hits": cache_hit_count,
                "prototype_cache_misses": len(missing_documents),
            },
        )
    except (ZhihuOpenAPIError, PersonCloneError) as error:
        return ToolResult(
            content=str(error),
            is_error=True,
            error_type=error.error_type,
            retryable=error.retryable,
            details=error.details,
        )
    except EmbeddingError as error:
        return ToolResult(
            content=str(error),
            is_error=True,
            error_type="routing_embedding_error",
            retryable=False,
        )
    finally:
        if zhihu is not None:
            zhihu.close()
        if personclone is not None:
            personclone.close()
