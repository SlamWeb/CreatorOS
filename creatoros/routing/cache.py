from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .models import RoutePrototypeDoc


CACHE_VERSION = 1


class RoutingEmbeddingCache:
    """Best-effort persistent cache for profile prototype vectors."""

    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self._entries: dict[str, dict] | None = None

    @classmethod
    def from_defaults(cls):
        from ..config import PROJECT_ROOT

        return cls(PROJECT_ROOT / "tmp" / "routing_embedding_cache.json")

    def get(self, document: RoutePrototypeDoc) -> tuple[float, ...] | None:
        entry = self._load().get(document.doc_id)
        if not self._matches(entry, document):
            return None
        vector = entry.get("vector")
        if not isinstance(vector, list) or len(vector) != document.embedding_dimension:
            return None
        try:
            return tuple(float(value) for value in vector)
        except (TypeError, ValueError):
            return None

    def put(self, document: RoutePrototypeDoc, vector) -> None:
        values = tuple(float(value) for value in vector)
        if len(values) != document.embedding_dimension:
            raise ValueError("缓存向量维度与画像声明不一致。")
        self._load()[document.doc_id] = {
            "author_id": document.author_id,
            "prototype_id": document.prototype_id,
            "prototype_type": document.prototype_type,
            "corpus_version": document.corpus_version,
            "embedding_model": document.embedding_model,
            "embedding_dimension": document.embedding_dimension,
            "text_sha256": self._text_hash(document),
            "vector": list(values),
        }

    def save(self) -> None:
        entries = self._load()
        payload = {"version": CACHE_VERSION, "entries": entries}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def _load(self) -> dict[str, dict]:
        if self._entries is not None:
            return self._entries
        self._entries = {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            entries = (
                payload.get("entries")
                if isinstance(payload, dict) and payload.get("version") == CACHE_VERSION
                else None
            )
            if isinstance(entries, dict):
                self._entries = {
                    str(key): value for key, value in entries.items() if isinstance(value, dict)
                }
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            self._entries = {}
        return self._entries

    @staticmethod
    def _text_hash(document: RoutePrototypeDoc) -> str:
        return hashlib.sha256(document.embedding_text.encode("utf-8")).hexdigest()

    def _matches(self, entry: dict | None, document: RoutePrototypeDoc) -> bool:
        if not isinstance(entry, dict):
            return False
        return all(
            entry.get(field) == expected
            for field, expected in {
                "author_id": document.author_id,
                "prototype_id": document.prototype_id,
                "prototype_type": document.prototype_type,
                "corpus_version": document.corpus_version,
                "embedding_model": document.embedding_model,
                "embedding_dimension": document.embedding_dimension,
                "text_sha256": self._text_hash(document),
            }.items()
        )
