from __future__ import annotations

from collections.abc import Sequence

from ..discovery import HotTopic
from ..routing import EmbeddedRoutePrototype, rank_domain_matches
from .models import ContentOpportunity, DailyPlan


def _validate_inputs(
    topics: Sequence[HotTopic],
    topic_vectors: Sequence[Sequence[float]],
    top_k: int | None,
) -> None:
    if len(topics) != len(topic_vectors):
        raise ValueError("topics 与 topic_vectors 长度必须一致。")
    if top_k is not None and top_k < 1:
        raise ValueError("top_k 必须大于 0。")


def build_daily_plans(
    topics: Sequence[HotTopic],
    topic_vectors: Sequence[Sequence[float]],
    prototypes: Sequence[EmbeddedRoutePrototype],
    *,
    top_k: int | None = None,
) -> tuple[DailyPlan, ...]:
    """Transpose hotspot→author scores into one ranked hot queue per author."""
    _validate_inputs(topics, topic_vectors, top_k)
    author_ids = sorted(
        {
            item.document.author_id
            for item in prototypes
            if item.document.prototype_type == "domain"
        }
    )
    opportunities: dict[str, list[ContentOpportunity]] = {
        author_id: [] for author_id in author_ids
    }
    for topic, vector in zip(topics, topic_vectors):
        for match in rank_domain_matches(vector, prototypes):
            opportunities[match.author_id].append(
                ContentOpportunity(
                    author_id=match.author_id,
                    queue="hot",
                    hotspot_rank=topic.rank,
                    hotspot_title=topic.title,
                    hotspot_url=topic.url,
                    hotspot_summary=topic.summary,
                    score=match.score,
                    matched_prototype_id=match.prototype_id,
                    matched_domain_label=match.label,
                    profile_corpus_version=match.corpus_version,
                )
            )

    plans: list[DailyPlan] = []
    for author_id in author_ids:
        ranked = tuple(
            sorted(
                opportunities[author_id],
                key=lambda item: (-item.score, item.hotspot_rank, item.hotspot_title),
            )
        )
        if top_k is not None:
            ranked = ranked[:top_k]
        plans.append(DailyPlan(author_id=author_id, hot=ranked))
    return tuple(plans)


def rank_hotspots_for_author(
    author_id: str,
    topics: Sequence[HotTopic],
    topic_vectors: Sequence[Sequence[float]],
    prototypes: Sequence[EmbeddedRoutePrototype],
    *,
    top_k: int | None = None,
) -> tuple[ContentOpportunity, ...]:
    """Return one author's ranked hot queue from the shared score matrix."""
    if not author_id.strip():
        raise ValueError("author_id 不能为空。")
    for plan in build_daily_plans(topics, topic_vectors, prototypes, top_k=top_k):
        if plan.author_id == author_id:
            return plan.hot
    return ()
