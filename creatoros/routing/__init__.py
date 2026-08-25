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
]
