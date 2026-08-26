from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

from .models import RoutePrototypeDoc


class EmbeddingError(RuntimeError):
    pass


@dataclass(frozen=True)
class EmbeddedRoutePrototype:
    document: RoutePrototypeDoc
    vector: tuple[float, ...]
    normalized: bool = True

    @property
    def dimension(self) -> int:
        return len(self.vector)


class BGEEmbeddingProvider:
    """Local, offline BGE-M3 embedder for CreatorOS routing documents."""

    def __init__(
        self,
        model_name: str = "BAAI/bge-m3",
        *,
        cache_dir: str | None = None,
        batch_size: int = 16,
        device: str | None = None,
    ):
        if not model_name.strip():
            raise ValueError("embedding model_name 不能为空。")
        if batch_size < 1:
            raise ValueError("embedding batch_size 必须大于 0。")
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.batch_size = batch_size
        self.device = device
        self._model = None

    def _load_model(self):
        if self._model is not None:
            return self._model
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        try:
            from sentence_transformers import SentenceTransformer

            kwargs = {
                "local_files_only": True,
                "trust_remote_code": False,
            }
            if self.cache_dir:
                kwargs["cache_folder"] = self.cache_dir
            if self.device:
                kwargs["device"] = self.device
            self._model = SentenceTransformer(self.model_name, **kwargs)
        except Exception as error:
            raise EmbeddingError(
                f"本地 embedding 模型不可用：{self.model_name}"
            ) from error
        return self._model

    def _encode_texts(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        model = self._load_model()
        vectors = model.encode(
            list(texts),
            batch_size=self.batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        if len(vectors) != len(texts):
            raise EmbeddingError("embedding 返回数量与输入文本数量不一致。")
        return tuple(
            tuple(float(value) for value in vector)
            for vector in vectors
        )

    def embed_texts(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        values = tuple(text.strip() for text in texts)
        if any(not value for value in values):
            raise ValueError("embedding 文本不能为空。")
        if not values:
            return ()
        return self._encode_texts(values)

    def embed_text(self, text: str) -> tuple[float, ...]:
        vectors = self.embed_texts((text,))
        return vectors[0]

    def embed_documents(
        self, documents: Sequence[RoutePrototypeDoc]
    ) -> tuple[EmbeddedRoutePrototype, ...]:
        docs = tuple(documents)
        if not docs:
            return ()
        if any(doc.embedding_model != self.model_name for doc in docs):
            raise EmbeddingError("画像声明的 embedding_model 与当前 Provider 不一致。")

        vectors = self._encode_texts([doc.embedding_text for doc in docs])
        dimension = len(vectors[0])
        if any(doc.embedding_dimension != dimension for doc in docs):
            raise EmbeddingError("embedding 实际维度与画像声明不一致。")
        return tuple(
            EmbeddedRoutePrototype(
                document=doc,
                vector=vector,
            )
            for doc, vector in zip(docs, vectors)
        )
