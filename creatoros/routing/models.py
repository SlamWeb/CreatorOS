from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field


class RoutingModel(BaseModel):
    """Strict base for data crossing the PersonClone routing boundary."""

    model_config = ConfigDict(strict=True, extra="forbid")


class VectorRef(RoutingModel):
    collection_name: str
    point_id: str
    vector_name: str
    embedding_model: str
    dimension: int = Field(gt=0)
    normalized: bool
    corpus_version: str


class RoutingEvidence(RoutingModel):
    doc_id: str
    title: str
    kind: str
    updated_at: str | None = None
    source_method: str
    field: str | None = None
    claim_id: str | None = None
    excerpt: str | None = None


class DomainPrototype(RoutingModel):
    prototype_id: str
    label: str
    description: str
    retrieval_text: str
    document_count: int = Field(ge=0)
    representative_evidence: list[RoutingEvidence]
    confidence: float = Field(ge=0, le=1)
    status: str
    vector_ref: VectorRef


class PerspectivePrototype(RoutingModel):
    prototype_id: str
    label: str
    summary: str
    values: list[str]
    trigger_cues: list[str]
    reasoning_pattern: str
    boundaries: list[str]
    retrieval_text: str
    representative_evidence: list[RoutingEvidence]
    confidence: float = Field(ge=0, le=1)
    source_method: str
    vector_ref: VectorRef


class AuthorRoutingProfile(RoutingModel):
    READY_STATUSES: ClassVar[frozenset[str]] = frozenset({"ready", "domain_ready"})

    schema_version: int = Field(ge=1)
    author_id: str
    display_name: str
    source: str
    generated_at: str
    corpus_version: str
    config_hash: str
    status: str
    embedding_model: str
    embedding_dimension: int = Field(gt=0)
    qdrant_collection: str
    domain_prototypes: list[DomainPrototype]
    perspective_prototypes: list[PerspectivePrototype]

    @property
    def can_use_domain(self) -> bool:
        return self.status in self.READY_STATUSES and bool(self.domain_prototypes)

    @property
    def can_use_perspective(self) -> bool:
        return self.status == "ready" and bool(self.perspective_prototypes)


class RoutingProfileEnvelope(RoutingModel):
    status: str
    profile: AuthorRoutingProfile
