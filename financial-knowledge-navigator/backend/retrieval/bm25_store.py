import re
from typing import List, Dict

try:
    from rank_bm25 import BM25Okapi
except ImportError:
    class BM25Okapi:  # pragma: no cover - exercised indirectly when dependency is absent
        def __init__(self, tokenized_corpus: List[List[str]]):
            self.tokenized_corpus = tokenized_corpus

        def get_scores(self, tokenized_query: List[str]) -> List[float]:
            query_terms = set(tokenized_query)
            scores = []
            for document in self.tokenized_corpus:
                score = sum(1.0 for token in document if token in query_terms)
                scores.append(score)
            return scores


class BM25Store:
    def __init__(self):
        self.documents: List[Dict] = []
        self.tokenized_corpus: List[List[str]] = []
        self.bm25 = None
        self.indexed_chunk_ids = set()
        self._dirty = True  # Track whether index needs rebuild

    def _tokenize(self, text: str) -> List[str]:
        """
        Simple lowercase tokenizer for a starter build.
        """
        return re.findall(r"\b\w+\b", text.lower())

    def index_chunks(self, chunks: List[Dict]) -> None:
        """
        Add chunks to the BM25 corpus. Defers expensive BM25Okapi rebuild
        until first search() call (lazy initialization).
        """
        if not chunks:
            return

        for chunk in chunks:
            chunk_id = chunk.get("chunk_id")
            if chunk_id in self.indexed_chunk_ids:
                continue
            self.documents.append(chunk)
            self.tokenized_corpus.append(self._tokenize(chunk["text"]))
            if chunk_id:
                self.indexed_chunk_ids.add(chunk_id)

        self._dirty = True  # Mark for rebuild on next search

    def _ensure_index(self) -> None:
        """Rebuild BM25 index only when needed."""
        if self._dirty and self.tokenized_corpus:
            self.bm25 = BM25Okapi(self.tokenized_corpus)
            self._dirty = False

    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        """
        Return BM25-ranked chunks.
        """
        self._ensure_index()

        if self.bm25 is None or not self.documents:
            return []

        tokenized_query = self._tokenize(query)
        scores = self.bm25.get_scores(tokenized_query)

        ranked = sorted(
            zip(self.documents, scores),
            key=lambda x: x[1],
            reverse=True,
        )[:top_k]

        return [
            {
                "score": float(score),
                "chunk_id": doc["chunk_id"],
                "source": doc["source"],
                "text": doc["text"],
            }
            for doc, score in ranked
        ]
