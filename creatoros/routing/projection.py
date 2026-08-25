from __future__ import annotations

from .models import AuthorRoutingProfile, RoutePrototypeDoc


def _doc_id(author_id: str, prototype_type: str, prototype_id: str) -> str:
    return f"{author_id}::{prototype_type}::{prototype_id}"


def project_profile(profile: AuthorRoutingProfile) -> tuple[RoutePrototypeDoc, ...]:
    """Flatten both prototype families without contacting Qdrant or an embedder."""

    docs: list[RoutePrototypeDoc] = []
    for prototype in profile.domain_prototypes:
        evidence_titles = ", ".join(item.title for item in prototype.representative_evidence)
        text = "\n".join(
            (
                f"领域标签: {prototype.label}",
                f"领域描述: {prototype.description}",
                f"检索文本: {prototype.retrieval_text}",
                f"代表标题: {evidence_titles}",
            )
        )
        docs.append(
            RoutePrototypeDoc(
                doc_id=_doc_id(profile.author_id, "domain", prototype.prototype_id),
                author_id=profile.author_id,
                prototype_id=prototype.prototype_id,
                prototype_type="domain",
                label=prototype.label,
                embedding_text=text,
                confidence=prototype.confidence,
                evidence_doc_ids=[item.doc_id for item in prototype.representative_evidence],
                corpus_version=profile.corpus_version,
                embedding_model=profile.embedding_model,
                embedding_dimension=profile.embedding_dimension,
            )
        )
    for prototype in profile.perspective_prototypes:
        evidence_text = " | ".join(
            item.excerpt or item.title for item in prototype.representative_evidence
        )
        text = "\n".join(
            (
                f"视角标签: {prototype.label}",
                f"视角摘要: {prototype.summary}",
                f"价值关注: {'、'.join(prototype.values)}",
                f"触发线索: {'、'.join(prototype.trigger_cues)}",
                f"推理方式: {prototype.reasoning_pattern}",
                f"边界: {'、'.join(prototype.boundaries)}",
                f"检索文本: {prototype.retrieval_text}",
                f"代表证据: {evidence_text}",
            )
        )
        docs.append(
            RoutePrototypeDoc(
                doc_id=_doc_id(profile.author_id, "perspective", prototype.prototype_id),
                author_id=profile.author_id,
                prototype_id=prototype.prototype_id,
                prototype_type="perspective",
                label=prototype.label,
                embedding_text=text,
                confidence=prototype.confidence,
                evidence_doc_ids=[item.doc_id for item in prototype.representative_evidence],
                corpus_version=profile.corpus_version,
                embedding_model=profile.embedding_model,
                embedding_dimension=profile.embedding_dimension,
            )
        )
    return tuple(docs)
