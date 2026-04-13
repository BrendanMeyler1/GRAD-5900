"""
Embeddings utilities for retrieval.

Provides a single embedding interface with two backends:
- sentence_transformers: higher-quality semantic embeddings
- hash: deterministic local fallback that requires no model downloads
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
from typing import Iterable, Sequence

logger = logging.getLogger("job_finder.retrieval.embeddings")


def _tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase alphanumeric terms."""
    return re.findall(r"[a-z0-9]+", text.lower())


def _l2_normalize(vector: list[float]) -> list[float]:
    """Normalize a vector to unit length."""
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0:
        return vector
    return [v / norm for v in vector]


class TextEmbedder:
    """Embedding abstraction with a deterministic local fallback."""

    def __init__(
        self,
        model_name: str | None = None,
        backend: str = "auto",
        vector_size: int = 384,
    ) -> None:
        self.model_name = model_name or os.getenv(
            "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
        )
        self.backend = backend
        self.vector_size = vector_size
        self._model = None
        self._resolved_backend = self._resolve_backend()

    def _resolve_backend(self) -> str:
        """Resolve backend preference."""
        if self.backend in {"hash", "sentence_transformers"}:
            return self.backend

        # auto: prefer sentence-transformers if import is available
        try:
            import sentence_transformers  # noqa: F401

            return "sentence_transformers"
        except Exception:
            logger.info(
                "sentence-transformers unavailable; using hash embeddings fallback"
            )
            return "hash"

    @property
    def model(self):
        """Lazy-load sentence-transformers model."""
        if self._resolved_backend != "sentence_transformers":
            return None
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            logger.info(f"Loading embedding model: {self.model_name}")
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed many texts at once."""
        normalized_texts = [t if isinstance(t, str) else str(t) for t in texts]

        if self._resolved_backend == "sentence_transformers":
            vectors = self.model.encode(
                normalized_texts,
                convert_to_numpy=False,
                normalize_embeddings=True,
            )
            return [list(v) for v in vectors]

        return [self._hash_embed(text) for text in normalized_texts]

    def embed_text(self, text: str) -> list[float]:
        """Embed a single text."""
        return self.embed_texts([text])[0]

    def _hash_embed(self, text: str) -> list[float]:
        """
        Deterministic embedding fallback.

        This is not a replacement for model embeddings, but it provides stable
        lexical/semantic-ish signals for local tests and fully offline runs.
        """
        tokens = _tokenize(text)
        if not tokens:
            tokens = [text.strip().lower() or "_empty_"]

        vector = [0.0] * self.vector_size
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            for i, byte in enumerate(digest):
                idx = (i * 131 + byte) % self.vector_size
                sign = 1.0 if byte % 2 == 0 else -1.0
                magnitude = 0.2 + (byte / 255.0)
                vector[idx] += sign * magnitude

        return _l2_normalize(vector)

    @staticmethod
    def cosine_similarity(a: Iterable[float], b: Iterable[float]) -> float:
        """Compute cosine similarity in pure Python."""
        va = list(a)
        vb = list(b)
        if not va or not vb or len(va) != len(vb):
            return 0.0
        dot = sum(x * y for x, y in zip(va, vb))
        norm_a = math.sqrt(sum(x * x for x in va))
        norm_b = math.sqrt(sum(y * y for y in vb))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)
