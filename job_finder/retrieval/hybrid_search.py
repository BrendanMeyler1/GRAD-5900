"""
Hybrid retrieval: vector similarity + BM25 lexical matching.

Implements a weighted merge:
hybrid_score = vector_weight * vector_score + (1 - vector_weight) * bm25_score
"""

from __future__ import annotations

import logging
import re
from collections import OrderedDict
from typing import Any

from retrieval.vector_store import VectorStore

logger = logging.getLogger("job_finder.retrieval.hybrid_search")


def _tokenize(text: str) -> list[str]:
    """Tokenize text for lexical retrieval."""
    return re.findall(r"[a-z0-9]+", text.lower())


def _normalize_scores(score_map: dict[str, float]) -> dict[str, float]:
    """Min-max normalize scores to [0, 1]."""
    if not score_map:
        return {}
    min_score = min(score_map.values())
    max_score = max(score_map.values())
    if max_score - min_score < 1e-12:
        return {k: (1.0 if v > 0 else 0.0) for k, v in score_map.items()}
    return {
        k: (v - min_score) / (max_score - min_score)
        for k, v in score_map.items()
    }


class HybridSearch:
    """Weighted hybrid search over local document collections."""

    def __init__(
        self,
        vector_store: VectorStore | None = None,
        vector_weight: float = 0.6,
    ) -> None:
        self.vector_store = vector_store or VectorStore()
        self.vector_weight = max(0.0, min(1.0, vector_weight))

        self._docs: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._tokenized_corpus: list[list[str]] = []
        self._doc_id_order: list[str] = []
        self._bm25 = None

        self._bm25_available = False
        try:
            from rank_bm25 import BM25Okapi  # noqa: F401

            self._bm25_available = True
        except Exception:
            logger.info("rank-bm25 unavailable; lexical overlap fallback will be used")

    def index_documents(self, documents: list[dict[str, Any]]) -> int:
        """
        Index documents for both vector and lexical retrieval.

        Each document requires:
        - id
        - text
        - metadata (optional)
        """
        if not documents:
            return 0

        for doc in documents:
            doc_id = str(doc["id"])
            self._docs[doc_id] = {
                "id": doc_id,
                "text": doc.get("text", ""),
                "metadata": doc.get("metadata", {}) or {},
            }

        self.vector_store.upsert(list(self._docs.values()))
        self._rebuild_lexical_index()
        return len(documents)

    def _rebuild_lexical_index(self) -> None:
        """Rebuild BM25 corpus from current in-memory documents."""
        self._doc_id_order = list(self._docs.keys())
        self._tokenized_corpus = [
            _tokenize(self._docs[doc_id]["text"])
            for doc_id in self._doc_id_order
        ]

        if self._bm25_available and self._tokenized_corpus:
            from rank_bm25 import BM25Okapi

            self._bm25 = BM25Okapi(self._tokenized_corpus)
        else:
            self._bm25 = None

    def search(
        self,
        query: str,
        k: int = 10,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Search documents using weighted hybrid scoring."""
        if not self._docs:
            return []

        k = max(1, k)
        candidate_pool = max(k * 4, 20)

        vector_results = self.vector_store.query(query, k=candidate_pool, where=where)
        vector_scores = {
            result["id"]: float(result.get("score", 0.0)) for result in vector_results
        }
        vector_scores = _normalize_scores(vector_scores)

        bm25_scores = self._search_bm25(query, where=where)
        bm25_scores = _normalize_scores(bm25_scores)

        doc_ids = set(vector_scores) | set(bm25_scores)
        if where:
            doc_ids = {
                doc_id
                for doc_id in doc_ids
                if self._matches_where(self._docs[doc_id]["metadata"], where)
            }

        merged = []
        for doc_id in doc_ids:
            vector_score = vector_scores.get(doc_id, 0.0)
            bm25_score = bm25_scores.get(doc_id, 0.0)
            hybrid_score = (
                self.vector_weight * vector_score
                + (1.0 - self.vector_weight) * bm25_score
            )
            merged.append((doc_id, vector_score, bm25_score, hybrid_score))

        merged.sort(key=lambda item: item[3], reverse=True)

        results = []
        for rank, (doc_id, vector_score, bm25_score, hybrid_score) in enumerate(
            merged[:k], start=1
        ):
            doc = self._docs[doc_id]
            results.append(
                {
                    "id": doc_id,
                    "text": doc["text"],
                    "metadata": doc["metadata"],
                    "score": float(hybrid_score),
                    "vector_score": float(vector_score),
                    "bm25_score": float(bm25_score),
                    "rank": rank,
                }
            )

        return results

    def _search_bm25(
        self,
        query: str,
        where: dict[str, Any] | None = None,
    ) -> dict[str, float]:
        """Retrieve lexical scores for all candidate docs."""
        if not self._doc_id_order:
            return {}

        query_tokens = _tokenize(query)
        if not query_tokens:
            return {}

        if self._bm25 is not None:
            raw_scores = self._bm25.get_scores(query_tokens)
            scores = {
                doc_id: float(score)
                for doc_id, score in zip(self._doc_id_order, raw_scores)
            }
        else:
            # Fallback lexical overlap score
            query_set = set(query_tokens)
            scores = {}
            for doc_id, tokens in zip(self._doc_id_order, self._tokenized_corpus):
                if not tokens:
                    scores[doc_id] = 0.0
                    continue
                token_set = set(tokens)
                overlap = len(query_set & token_set)
                scores[doc_id] = overlap / max(len(query_set), 1)

        if where:
            scores = {
                doc_id: score
                for doc_id, score in scores.items()
                if self._matches_where(self._docs[doc_id]["metadata"], where)
            }
        return scores

    @staticmethod
    def _matches_where(metadata: dict[str, Any], where: dict[str, Any]) -> bool:
        """Evaluate simple metadata equality filters."""
        return all(metadata.get(key) == value for key, value in where.items())
