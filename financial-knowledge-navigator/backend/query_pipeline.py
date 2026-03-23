from typing import Dict, List, Any

from backend.core.config import settings
from backend.eval.context_builder import build_retrieved_context_text


class QueryPipeline:
    def __init__(
        self,
        vector_store,
        bm25_store,
        hybrid_searcher,
        answer_generator,
        refined_answer_generator,
        graphrag_engine,
        query_cache,
        self_corrector=None,
    ):
        self.vector_store = vector_store
        self.bm25_store = bm25_store
        self.hybrid_searcher = hybrid_searcher
        self.answer_generator = answer_generator
        self.refined_answer_generator = refined_answer_generator
        self.graphrag_engine = graphrag_engine
        self.query_cache = query_cache
        self.self_corrector = self_corrector

    def _retrieve(self, query: str, mode: str, top_k: int) -> Dict[str, Any]:
        """
        Dispatch retrieval by mode.

        Supported modes:
          - "vector": Dense vector search only.
          - "bm25": Sparse BM25 keyword search only.
          - "hybrid": Reciprocal Rank Fusion of vector + BM25.
          - "graphrag": Same retrieval as hybrid; graph context is layered on
            in the generation stage by build_graph_context(). This is a
            runner-level concept documented here for explicitness.
        """
        # graphrag uses hybrid retrieval; graph enrichment happens downstream
        effective_mode = mode if mode != "graphrag" else "hybrid"

        if effective_mode == "vector":
            vector_results = self.vector_store.search(query=query, top_k=top_k)
            return {
                "selected_results": vector_results,
                "vector_results": vector_results,
                "bm25_results": [],
                "mode": mode,
            }

        if effective_mode == "bm25":
            bm25_results = self.bm25_store.search(query=query, top_k=top_k)
            return {
                "selected_results": bm25_results,
                "vector_results": [],
                "bm25_results": bm25_results,
                "mode": mode,
            }

        if effective_mode == "hybrid":
            search_output = self.hybrid_searcher.search(
                query=query,
                top_k=top_k,
                per_retriever_k=max(top_k + 3, 8),
            )
            return {
                "selected_results": search_output["hybrid_results"],
                "vector_results": search_output["vector_results"],
                "bm25_results": search_output["bm25_results"],
                "mode": mode,
            }

        raise ValueError(f"Unsupported mode: {mode}")

    def run(
        self,
        query: str,
        mode: str,
        indexed_docs: List[str],
        top_k: int = None,
        use_cache: bool = True,
        use_correction: bool = False,
    ) -> Dict[str, Any]:
        top_k = top_k or settings.top_k

        # Slightly modify version tag to ensure cache miss if toggled on
        version_str = "v2" if use_correction else "v1"

        cache_key = self.query_cache.make_pipeline_key(
            query=query,
            mode=mode,
            indexed_docs=indexed_docs,
            top_k=top_k,
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
            version=version_str,
        )

        if use_cache:
            cached = self.query_cache.load(cache_key)
            if cached is not None:
                cached["cache_hit"] = True
                return cached

        retrieval_output = self._retrieve(query=query, mode=mode, top_k=top_k)
        selected_results = retrieval_output["selected_results"]

        was_corrected = False
        rewritten_query = None

        if use_correction and self.self_corrector:
            context_text = build_retrieved_context_text(selected_results)
            # Grade relevance
            is_relevant = self.self_corrector.grade_relevance(query, context_text)
            
            if not is_relevant:
                # Rewrite and retry
                rewritten_query = self.self_corrector.rewrite_query(query)
                retrieval_output_2 = self._retrieve(query=rewritten_query, mode=mode, top_k=top_k)
                selected_results = retrieval_output_2["selected_results"]
                
                # Combine original state but swap selected chunks
                # We won't overwrite vector_results / bm25_results purely for simplicity of outputs,
                # though realistically you'd return the new sets.
                retrieval_output["vector_results"] = retrieval_output_2["vector_results"]
                retrieval_output["bm25_results"] = retrieval_output_2["bm25_results"]
                was_corrected = True

        preliminary_answer = self.answer_generator.generate_answer(
            question=query,
            retrieved_chunks=selected_results,
        )

        try:
            graph_output = self.graphrag_engine.build_graph_context(
                query=query,
                radius=1,
                max_edges=20,
            )
        except Exception:
            graph_output = {
                "graph_context_text": "Graph context unavailable due to a graph processing error.",
                "matched_nodes": [],
            }

        refined_answer = self.refined_answer_generator.generate_refined_answer(
            question=query,
            preliminary_answer=preliminary_answer,
            retrieved_chunks=selected_results,
            graph_context=graph_output["graph_context_text"],
        )

        result = {
            "cache_hit": False,
            "query": query,
            "rewritten_query": rewritten_query,
            "was_corrected": was_corrected,
            "mode": mode,
            "selected_results": selected_results,
            "vector_results": retrieval_output["vector_results"],
            "bm25_results": retrieval_output["bm25_results"],
            "preliminary_answer": preliminary_answer,
            "graph_context_text": graph_output["graph_context_text"],
            "matched_nodes": graph_output["matched_nodes"],
            "refined_answer": refined_answer,
            "retrieved_context_text": build_retrieved_context_text(selected_results),
        }

        # Do not save raw subgraph object because it is not JSON serializable.
        self.query_cache.save(cache_key, result)
        return result
