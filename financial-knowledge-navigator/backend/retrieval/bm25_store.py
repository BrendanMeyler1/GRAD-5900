import re
from typing import List, Dict
from rank_bm25 import BM25Okapi


class BM25Store:
    def __init__(self):
        self.documents: List[Dict] = []
        self.tokenized_corpus: List[List[str]] = []
        self.bm25 = None

    def _tokenize(self, text: str) -> List[str]:
        """
        Simple lowercase tokenizer for a starter build.
        """
        return re.findall(r"\b\w+\b", text.lower())

    def index_chunks(self, chunks: List[Dict]) -> None:
        """
        Add chunks to the BM25 corpus and rebuild the BM25 index.
        """
        if not chunks:
            return

        for chunk in chunks:
            self.documents.append(chunk)
            self.tokenized_corpus.append(self._tokenize(chunk["text"]))

        self.bm25 = BM25Okapi(self.tokenized_corpus)

    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        """
        Return BM25-ranked chunks.
        """
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
