"""
Vector store wrapper for local semantic retrieval.

Primary backend: ChromaDB persistent collection.
Fallback backend: in-memory vector index.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

from retrieval.embeddings import TextEmbedder

logger = logging.getLogger("job_finder.retrieval.vector_store")


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _matches_where(metadata: dict[str, Any], where: dict[str, Any] | None) -> bool:
    """Evaluate simple key==value metadata filters."""
    if not where:
        return True
    return all(metadata.get(k) == v for k, v in where.items())


class VectorStore:
    """Persistent semantic document store."""

    def __init__(
        self,
        collection_name: str = "job_descriptions",
        persist_dir: str = "data/chroma",
        embedder: TextEmbedder | None = None,
        use_chroma: bool | None = None,
    ) -> None:
        self.collection_name = collection_name
        self.persist_dir = persist_dir
        self.embedder = embedder or TextEmbedder()

        self._client = None
        self._collection = None
        self._use_chroma = False

        self._memory_docs: dict[str, dict[str, Any]] = {}
        self._memory_embeddings: dict[str, list[float]] = {}

        if use_chroma is not False:
            self._initialize_chroma()

    def _initialize_chroma(self) -> None:
        """Initialize ChromaDB client + collection."""
        try:
            import chromadb

            Path(self.persist_dir).mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=self.persist_dir)
            self._collection = self._client.get_or_create_collection(
                name=self.collection_name
            )
            self._use_chroma = True
            logger.info(
                f"VectorStore using Chroma collection '{self.collection_name}' "
                f"at {self.persist_dir}"
            )
        except Exception as exc:
            self._use_chroma = False
            logger.warning(
                "ChromaDB unavailable; using in-memory vector store fallback: %s",
                exc,
            )

    def upsert(self, documents: list[dict[str, Any]]) -> int:
        """
        Upsert documents into the store.

        Each document must include:
        - id: unique string
        - text: document text
        - metadata: optional dict
        """
        if not documents:
            return 0

        ids: list[str] = []
        texts: list[str] = []
        metadatas: list[dict[str, Any]] = []

        for item in documents:
            doc_id = str(item["id"])
            text = item.get("text", "")
            metadata = item.get("metadata", {}) or {}

            ids.append(doc_id)
            texts.append(text)
            metadatas.append(metadata)

        embeddings = self.embedder.embed_texts(texts)

        for doc_id, text, metadata, embedding in zip(ids, texts, metadatas, embeddings):
            self._memory_docs[doc_id] = {"id": doc_id, "text": text, "metadata": metadata}
            self._memory_embeddings[doc_id] = embedding

        if self._use_chroma:
            try:
                self._collection.upsert(
                    ids=ids,
                    documents=texts,
                    metadatas=metadatas,
                    embeddings=embeddings,
                )
            except Exception as exc:
                logger.warning(
                    "Chroma upsert failed; continuing with in-memory index: %s",
                    exc,
                )
                self._use_chroma = False

        return len(documents)

    def query(
        self,
        query_text: str,
        k: int = 5,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Query for semantically similar documents."""
        if not self._memory_docs:
            return []

        k = max(1, k)
        query_embedding = self.embedder.embed_text(query_text)

        if self._use_chroma:
            try:
                raw = self._collection.query(
                    query_embeddings=[query_embedding],
                    n_results=k,
                    where=where,
                )
                return self._format_chroma_results(raw)
            except Exception as exc:
                logger.warning(
                    "Chroma query failed; using in-memory fallback: %s",
                    exc,
                )
                self._use_chroma = False

        return self._query_memory(query_embedding, k=k, where=where)

    def _query_memory(
        self,
        query_embedding: list[float],
        k: int,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Pure-python cosine similarity query."""
        scored: list[tuple[str, float]] = []
        for doc_id, doc in self._memory_docs.items():
            if not _matches_where(doc["metadata"], where):
                continue
            score = _cosine_similarity(query_embedding, self._memory_embeddings[doc_id])
            scored.append((doc_id, score))

        scored.sort(key=lambda item: item[1], reverse=True)
        results: list[dict[str, Any]] = []
        for doc_id, score in scored[:k]:
            doc = self._memory_docs[doc_id]
            results.append(
                {
                    "id": doc_id,
                    "text": doc["text"],
                    "metadata": doc["metadata"],
                    "score": float(score),
                }
            )
        return results

    def _format_chroma_results(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        """Transform Chroma query response to a normalized result schema."""
        ids = (raw.get("ids") or [[]])[0]
        docs = (raw.get("documents") or [[]])[0]
        metadatas = (raw.get("metadatas") or [[]])[0]
        distances = (raw.get("distances") or [[]])[0]

        results = []
        for doc_id, text, metadata, distance in zip(ids, docs, metadatas, distances):
            similarity = 1.0 / (1.0 + float(distance))
            results.append(
                {
                    "id": doc_id,
                    "text": text,
                    "metadata": metadata or {},
                    "score": similarity,
                }
            )
        return results

    def get_all_documents(self) -> list[dict[str, Any]]:
        """Return all in-memory documents."""
        return list(self._memory_docs.values())

    def count(self) -> int:
        """Return current document count."""
        return len(self._memory_docs)
