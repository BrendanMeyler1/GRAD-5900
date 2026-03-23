from typing import List, Dict


class HybridSearcher:
    def __init__(self, vector_store, bm25_store, rrf_k: int = 60):
        self.vector_store = vector_store
        self.bm25_store = bm25_store
        self.rrf_k = rrf_k

    def reciprocal_rank_fusion(
        self,
        vector_results: List[Dict],
        bm25_results: List[Dict],
        top_k: int = 5,
    ) -> List[Dict]:
        """
        Combine ranked result lists using Reciprocal Rank Fusion (RRF).
        """
        fused = {}

        for rank, item in enumerate(vector_results, start=1):
            key = item["chunk_id"]
            if key not in fused:
                fused[key] = {
                    "chunk_id": item["chunk_id"],
                    "source": item["source"],
                    "text": item["text"],
                    "vector_score": item["score"],
                    "bm25_score": None,
                    "rrf_score": 0.0,
                }
            else:
                fused[key]["vector_score"] = item["score"]
            fused[key]["rrf_score"] += 1.0 / (self.rrf_k + rank)

        for rank, item in enumerate(bm25_results, start=1):
            key = item["chunk_id"]
            if key not in fused:
                fused[key] = {
                    "chunk_id": item["chunk_id"],
                    "source": item["source"],
                    "text": item["text"],
                    "vector_score": None,
                    "bm25_score": item["score"],
                    "rrf_score": 0.0,
                }
            else:
                fused[key]["bm25_score"] = item["score"]

            fused[key]["rrf_score"] += 1.0 / (self.rrf_k + rank)

        ranked = sorted(
            fused.values(),
            key=lambda x: x["rrf_score"],
            reverse=True,
        )[:top_k]

        return ranked

    def search(
        self,
        query: str,
        top_k: int = 5,
        per_retriever_k: int = 8,
    ) -> Dict[str, List[Dict]]:
        """
        Run vector + BM25 retrieval and fuse the results.
        """
        vector_results = self.vector_store.search(query=query, top_k=per_retriever_k)
        bm25_results = self.bm25_store.search(query=query, top_k=per_retriever_k)

        hybrid_results = self.reciprocal_rank_fusion(
            vector_results=vector_results,
            bm25_results=bm25_results,
            top_k=top_k,
        )

        return {
            "vector_results": vector_results,
            "bm25_results": bm25_results,
            "hybrid_results": hybrid_results,
        }
