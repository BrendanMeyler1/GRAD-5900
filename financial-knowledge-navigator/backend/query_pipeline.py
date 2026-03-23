import re
from typing import Dict, List, Any

from backend.core.config import settings
from backend.eval.context_builder import (
    build_retrieved_context_text,
    build_structured_facts_context_text,
)
from backend.graph.builder import FinancialKnowledgeGraph
from backend.graph.graphrag import (
    GraphRAGEngine,
    build_graph_context_from_graph,
    collect_relevant_source_names,
    build_persisted_graph_context,
)


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
        graph_extractor=None,
        facts_extractor=None,
        facts_store=None,
        graph_store=None,
    ):
        self.vector_store = vector_store
        self.bm25_store = bm25_store
        self.hybrid_searcher = hybrid_searcher
        self.answer_generator = answer_generator
        self.refined_answer_generator = refined_answer_generator
        self.graphrag_engine = graphrag_engine
        self.query_cache = query_cache
        self.self_corrector = self_corrector
        self.graph_extractor = graph_extractor
        self.facts_extractor = facts_extractor
        self.facts_store = facts_store
        self.graph_store = graph_store

    def _tokenize(self, text: str) -> List[str]:
        return re.findall(r"[a-zA-Z0-9]+", (text or "").lower())

    def supported_modes(self) -> List[str]:
        if getattr(self.vector_store, "hosted", False):
            return ["file_search", "graphrag"]
        return ["vector", "hybrid", "bm25", "graphrag"]

    def _retrieve(self, query: str, mode: str, top_k: int) -> Dict[str, Any]:
        """
        Dispatch retrieval by mode.

        Supported modes:
          - "file_search": OpenAI hosted file search retrieval.
          - "vector": Dense vector search only.
          - "bm25": Sparse BM25 keyword search only.
          - "hybrid": Reciprocal Rank Fusion of vector + BM25.
          - "graphrag": Same retrieval as hybrid; graph context is layered on
            in the generation stage by build_graph_context(). This is a
            runner-level concept documented here for explicitness.
        """
        supported_modes = self.supported_modes()
        if mode not in supported_modes:
            raise ValueError(
                f"Unsupported mode '{mode}' for retrieval backend "
                f"'{getattr(self.vector_store, 'backend_name', 'unknown')}'. "
                f"Supported modes: {', '.join(supported_modes)}."
            )

        # graphrag uses hybrid retrieval; graph enrichment happens downstream
        effective_mode = "hybrid" if mode == "graphrag" else mode

        if getattr(self.vector_store, "hosted", False):
            hosted_results = self.vector_store.search(query=query, top_k=top_k)
            return {
                "selected_results": hosted_results,
                "vector_results": hosted_results,
                "bm25_results": [],
                "mode": mode,
            }

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

    def _build_query_local_graph_context(
        self,
        query: str,
        retrieved_chunks: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if not self.graph_extractor or not retrieved_chunks:
            return {
                "graph_context_text": "No lightweight graph context could be built for this query.",
                "matched_nodes": [],
            }

        temp_graph = FinancialKnowledgeGraph()
        candidate_chunks = [
            chunk for chunk in retrieved_chunks
            if self.graph_extractor.should_extract_chunk(chunk)
        ][: settings.top_k]

        for chunk in candidate_chunks:
            extraction = self.graph_extractor.extract_from_chunk(chunk)
            temp_graph.add_extraction_result(extraction)

        temp_engine = GraphRAGEngine(
            knowledge_graph=temp_graph,
            query_graph_linker=self.graphrag_engine.query_graph_linker,
        )
        graph_output = temp_engine.build_graph_context(
            query=query,
            radius=1,
            max_edges=12,
        )
        return {
            "graph_context_text": graph_output["graph_context_text"],
            "matched_nodes": [],
            "graph_context_origin": "query_local",
        }

    def _build_persisted_graph_context(
        self,
        query: str,
        retrieved_chunks: List[Dict[str, Any]],
    ) -> Dict[str, Any] | None:
        if self.graph_store is None or not retrieved_chunks:
            return None

        source_names = collect_relevant_source_names(retrieved_chunks)
        if not source_names:
            return None

        try:
            query_entities = self.graphrag_engine.query_graph_linker.extract_query_entities(query)
        except Exception:
            query_entities = []

        graph_output = build_persisted_graph_context(
            graph_store=self.graph_store,
            query=query,
            source_names=source_names,
            query_entities=query_entities,
            context_max_edges=24,
        )
        if graph_output is None:
            return None

        return {
            "graph_context_text": graph_output["graph_context_text"],
            "matched_nodes": graph_output["matched_nodes"],
            "graph_context_origin": "persisted_graph",
        }

    def _retrieve_structured_facts(
        self,
        query: str,
        retrieved_chunks: List[Dict[str, Any]],
        limit: int = 6,
    ) -> Dict[str, Any]:
        if self.facts_store is None:
            return {
                "selected_facts": [],
                "facts_context_text": "No structured facts available.",
            }

        source_names = []
        seen = set()
        for chunk in retrieved_chunks:
            source_name = chunk.get("source")
            if not source_name or source_name in seen:
                continue
            seen.add(source_name)
            source_names.append(source_name)
            if len(source_names) >= 5:
                break

        try:
            selected_facts = self.facts_store.search_facts(
                query=query,
                source_names=source_names or None,
                limit=limit,
            )
        except Exception:
            selected_facts = []

        if not selected_facts and self.facts_extractor is not None and retrieved_chunks:
            selected_facts = self._extract_query_local_structured_facts(
                query=query,
                retrieved_chunks=retrieved_chunks,
                limit=limit,
            )

        return {
            "selected_facts": selected_facts,
            "facts_context_text": build_structured_facts_context_text(selected_facts),
        }

    def _extract_query_local_structured_facts(
        self,
        query: str,
        retrieved_chunks: List[Dict[str, Any]],
        limit: int = 6,
    ) -> List[Dict[str, Any]]:
        if self.facts_extractor is None or self.facts_store is None:
            return []

        extracted_facts: List[Dict[str, Any]] = []
        for index, chunk in enumerate(retrieved_chunks[: max(settings.top_k, 3)], start=1):
            chunk_text = chunk.get("text", "")
            if not chunk_text:
                continue
            extracted_facts.extend(
                self.facts_extractor.extract_from_section(
                    section_text=chunk_text,
                    source_name=chunk.get("source", "Unknown"),
                    file_hash=chunk.get("file_hash") or chunk.get("chunk_id", f"query-{index}"),
                    section_index=index,
                )
            )

        if not extracted_facts:
            return []
        return self.facts_store.rank_facts(query=query, facts=extracted_facts, limit=limit)

    def _rerank_results_with_facts(
        self,
        query: str,
        results: List[Dict[str, Any]],
        selected_facts: List[Dict[str, Any]],
    ) -> tuple[List[Dict[str, Any]], bool]:
        if not results or not selected_facts:
            return results, False

        facts_by_source: Dict[str, List[Dict[str, Any]]] = {}
        for fact in selected_facts:
            source_name = fact.get("source_name")
            if not source_name:
                continue
            facts_by_source.setdefault(source_name, []).append(fact)

        query_tokens = set(self._tokenize(query))
        reranked = []
        rerank_applied = False

        for rank, result in enumerate(results):
            original_rank_score = max(len(results) - rank, 1)
            chunk_text = result.get("text", "").lower()
            chunk_tokens = set(self._tokenize(chunk_text))
            best_fact_boost = 0.0
            best_fact_labels: List[str] = []

            for fact in facts_by_source.get(result.get("source"), []):
                fact_tokens = set(
                    self._tokenize(
                        " ".join(
                            [
                                fact.get("metric_label", ""),
                                fact.get("metric_key", ""),
                                fact.get("period", "") or "",
                                fact.get("value_text", ""),
                            ]
                        )
                    )
                )
                overlap = len(chunk_tokens & fact_tokens)
                query_overlap = len(query_tokens & fact_tokens)
                direct_bonus = 0.0
                if fact.get("metric_label", "").lower() in chunk_text:
                    direct_bonus += 2.5
                if (fact.get("period") or "").lower() and (fact.get("period") or "").lower() in chunk_text:
                    direct_bonus += 1.5
                value_text = (fact.get("value_text") or "").lower()
                if value_text and value_text in chunk_text:
                    direct_bonus += 2.0

                fact_boost = (
                    overlap * 1.6
                    + query_overlap * 1.2
                    + min(float(fact.get("match_score", 0)), 20.0) * 0.2
                    + direct_bonus
                )
                if fact_boost > best_fact_boost:
                    best_fact_boost = fact_boost
                    best_fact_labels = [fact.get("metric_label", fact.get("metric_key", "Fact"))]

            combined_score = original_rank_score + best_fact_boost
            enriched = dict(result)
            enriched["fact_rerank_score"] = round(best_fact_boost, 3)
            enriched["fact_match_labels"] = best_fact_labels
            enriched["_fact_aware_combined_score"] = combined_score
            enriched["_original_rank"] = rank
            reranked.append(enriched)

            if best_fact_boost > 0.0 and rank > 0:
                rerank_applied = True

        reranked.sort(
            key=lambda item: (
                item["_fact_aware_combined_score"],
                -item["_original_rank"],
            ),
            reverse=True,
        )

        for item in reranked:
            item.pop("_fact_aware_combined_score", None)
            item.pop("_original_rank", None)

        return reranked, rerank_applied

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
        facts_count = 0
        if self.facts_store is not None:
            try:
                facts_count = self.facts_store.summary().get("num_facts", 0)
            except Exception:
                facts_count = 0
        graph_signature = "none"
        if mode == "graphrag" and self.graph_store is not None:
            try:
                graph_summary = self.graph_store.graph_summary()
                graph_signature = f"{graph_summary.get('num_nodes', 0)}:{graph_summary.get('num_edges', 0)}"
            except Exception:
                graph_signature = "unavailable"
        version_str = (
            f"v5|facts:{facts_count}|corr:{int(bool(use_correction))}|"
            f"fact_rerank:1|graph:{graph_signature}"
        )

        cache_key = self.query_cache.make_pipeline_key(
            query=query,
            mode=mode,
            indexed_docs=indexed_docs,
            top_k=top_k,
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
            retrieval_backend=getattr(self.vector_store, "backend_name", ""),
            graph_backend=settings.graph_backend,
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
        initial_facts_output = {
            "selected_facts": [],
            "facts_context_text": "No structured facts available.",
        }

        if use_correction and self.self_corrector:
            initial_facts_output = self._retrieve_structured_facts(
                query=query,
                retrieved_chunks=selected_results,
                limit=max(3, min(6, top_k + 1)),
            )
            context_text = build_retrieved_context_text(
                selected_results,
                structured_facts=initial_facts_output["selected_facts"],
            )
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

        facts_output = self._retrieve_structured_facts(
            query=query,
            retrieved_chunks=selected_results,
            limit=max(3, min(6, top_k + 1)),
        )
        selected_facts = facts_output["selected_facts"]
        selected_results, fact_rerank_applied = self._rerank_results_with_facts(
            query=query,
            results=selected_results,
            selected_facts=selected_facts,
        )

        preliminary_answer = self.answer_generator.generate_answer(
            question=query,
            retrieved_chunks=selected_results,
            structured_facts=selected_facts,
        )

        graph_output = {
            "graph_context_text": "",
            "matched_nodes": [],
            "graph_context_origin": "none",
        }
        if mode == "graphrag":
            try:
                graph_output = self._build_persisted_graph_context(
                    query=query,
                    retrieved_chunks=selected_results,
                ) or self._build_query_local_graph_context(
                    query=query,
                    retrieved_chunks=selected_results,
                )
            except Exception:
                graph_output = {
                    "graph_context_text": "Graph context unavailable due to a graph processing error.",
                    "matched_nodes": [],
                    "graph_context_origin": "error",
                }

        refined_answer = self.refined_answer_generator.generate_refined_answer(
            question=query,
            preliminary_answer=preliminary_answer,
            retrieved_chunks=selected_results,
            graph_context=graph_output["graph_context_text"],
            structured_facts=selected_facts,
        )

        result = {
            "cache_hit": False,
            "query": query,
            "rewritten_query": rewritten_query,
            "was_corrected": was_corrected,
            "fact_rerank_applied": fact_rerank_applied,
            "mode": mode,
            "selected_results": selected_results,
            "selected_facts": selected_facts,
            "vector_results": retrieval_output["vector_results"],
            "bm25_results": retrieval_output["bm25_results"],
            "preliminary_answer": preliminary_answer,
            "facts_context_text": facts_output["facts_context_text"],
            "graph_context_text": graph_output["graph_context_text"],
            "graph_context_origin": graph_output.get("graph_context_origin", "none"),
            "matched_nodes": graph_output["matched_nodes"],
            "refined_answer": refined_answer,
            "retrieved_context_text": build_retrieved_context_text(
                selected_results,
                structured_facts=selected_facts,
            ),
        }

        # Do not save raw subgraph object because it is not JSON serializable.
        self.query_cache.save(cache_key, result)
        return result
