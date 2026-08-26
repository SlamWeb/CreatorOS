"""Typed models for CreatorOS author routing."""

from .models import (
    AuthorRoutingProfile,
    DomainPrototype,
    PerspectivePrototype,
    RoutePrototypeDoc,
    RoutingEvidence,
    RoutingProfileEnvelope,
    VectorRef,
)
from .embedding import BGEEmbeddingProvider, EmbeddedRoutePrototype, EmbeddingError
from .domain import DomainMatch, build_domain_query, rank_domain_matches

__all__ = [
    "AuthorRoutingProfile",
    "DomainPrototype",
    "PerspectivePrototype",
    "RoutePrototypeDoc",
    "RoutingEvidence",
    "RoutingProfileEnvelope",
    "VectorRef",
    "BGEEmbeddingProvider",
    "EmbeddedRoutePrototype",
    "EmbeddingError",
    "DomainMatch",
    "build_domain_query",
    "rank_domain_matches",
]
