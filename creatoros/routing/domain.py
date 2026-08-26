from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from ..discovery import HotTopic
from .embedding import EmbeddedRoutePrototype, EmbeddingError

MAX_DOMAIN_SUMMARY_CHARS = 4_000


def build_domain_query(
    topic: HotTopic,
    *,
    max_summary_chars: int = MAX_DOMAIN_SUMMARY_CHARS,
) -> str:
    """Build a bounded, evidence-preserving domain query from one hot topic."""
    title = topic.title.strip()
    if not title:
        raise ValueError("热点标题不能为空。")
    if max_summary_chars < 0:
        raise ValueError("max_summary_chars 不能小于 0。")

    parts = [f"热点标题：{title}"]
    summary = topic.summary.strip()
    if summary and max_summary_chars:
        parts.append(f"问题介绍：{summary[:max_summary_chars]}")
    return "\n".join(parts)


@dataclass(frozen=True)
class DomainMatch:
    """The best matching domain prototype for one author."""

    author_id: str
    prototype_id: str
    label: str
    score: float
    confidence: float
    corpus_version: str


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise EmbeddingError("热点向量与作者原型向量维度不一致。")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        raise EmbeddingError("embedding 向量不能是零向量。")
    score = sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)
    if not math.isfinite(score):
        raise EmbeddingError("embedding 相似度不是有限数值。")
    return float(score)


def rank_domain_matches(
    query_vector: Sequence[float],
    prototypes: Sequence[EmbeddedRoutePrototype],
    *,
    top_k: int | None = None,
) -> tuple[DomainMatch, ...]:
    """Rank authors by each author's maximum domain-prototype similarity."""
    if top_k is not None and top_k < 1:
        raise ValueError("top_k 必须大于 0。")

    best_by_author: dict[str, DomainMatch] = {}
    for embedded in prototypes:
        document = embedded.document
        if document.prototype_type != "domain":
            continue
        score = _cosine_similarity(query_vector, embedded.vector)
        candidate = DomainMatch(
            author_id=document.author_id,
            prototype_id=document.prototype_id,
            label=document.label,
            score=score,
            confidence=document.confidence,
            corpus_version=document.corpus_version,
        )
        previous = best_by_author.get(candidate.author_id)
        if previous is None or (candidate.score, candidate.prototype_id) > (
            previous.score,
            previous.prototype_id,
        ):
            best_by_author[candidate.author_id] = candidate

    ranked = tuple(
        sorted(
            best_by_author.values(),
            key=lambda item: (-item.score, item.author_id, item.prototype_id),
        )
    )
    return ranked if top_k is None else ranked[:top_k]
