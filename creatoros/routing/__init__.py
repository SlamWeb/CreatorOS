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
from .cache import RoutingEmbeddingCache
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
    "RoutingEmbeddingCache",
    "DomainMatch",
    "build_domain_query",
    "rank_domain_matches",
]
