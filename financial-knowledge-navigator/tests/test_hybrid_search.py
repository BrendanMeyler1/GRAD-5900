"""Tests for backend.retrieval.hybrid_search — RRF fusion logic."""
from backend.retrieval.hybrid_search import HybridSearcher


def _make_result(chunk_id, score, source="doc.pdf", text="sample text"):
    return {"chunk_id": chunk_id, "score": score, "source": source, "text": text}


class TestRRF:
    def test_identical_rankings(self):
        """Same chunk in both lists should get double RRF score."""
        hs = HybridSearcher(vector_store=None, bm25_store=None)
        vec = [_make_result("c1", 0.9)]
        bm25 = [_make_result("c1", 5.5)]

        fused = hs.reciprocal_rank_fusion(vec, bm25, top_k=5)
        assert len(fused) == 1
        assert fused[0]["chunk_id"] == "c1"
        # Both vector_score and bm25_score should be set
        assert fused[0]["vector_score"] == 0.9
        assert fused[0]["bm25_score"] == 5.5

    def test_disjoint_rankings(self):
        """Chunks unique to each retriever appear with None for the other score."""
        hs = HybridSearcher(vector_store=None, bm25_store=None)
        vec = [_make_result("v1", 0.8)]
        bm25 = [_make_result("b1", 4.0)]

        fused = hs.reciprocal_rank_fusion(vec, bm25, top_k=5)
        assert len(fused) == 2
        ids = {r["chunk_id"] for r in fused}
        assert ids == {"v1", "b1"}

    def test_top_k_limiting(self):
        """Only top_k results should be returned."""
        hs = HybridSearcher(vector_store=None, bm25_store=None)
        vec = [_make_result(f"c{i}", 0.9 - i * 0.1) for i in range(5)]
        bm25 = [_make_result(f"c{i}", 5.0 - i) for i in range(5)]

        fused = hs.reciprocal_rank_fusion(vec, bm25, top_k=3)
        assert len(fused) == 3

    def test_rrf_returns_sorted_by_score(self):
        """Results should be sorted by rrf_score descending."""
        hs = HybridSearcher(vector_store=None, bm25_store=None)
        vec = [_make_result("c1", 0.9), _make_result("c2", 0.5)]
        bm25 = [_make_result("c1", 5.0)]

        fused = hs.reciprocal_rank_fusion(vec, bm25, top_k=5)
        scores = [r["rrf_score"] for r in fused]
        assert scores == sorted(scores, reverse=True)

    def test_cross_score_update_vector_to_bm25(self):
        """Issue #3 fix: vector_score should update when chunk appears in both."""
        hs = HybridSearcher(vector_store=None, bm25_store=None)
        # BM25 sees c1 first, then vector also finds c1
        bm25 = [_make_result("c1", 5.0)]
        vec = [_make_result("c1", 0.95)]

        fused = hs.reciprocal_rank_fusion(vec, bm25, top_k=5)
        assert fused[0]["vector_score"] == 0.95
        assert fused[0]["bm25_score"] == 5.0
