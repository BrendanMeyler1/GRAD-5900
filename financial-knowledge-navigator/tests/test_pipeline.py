import pytest
from unittest.mock import MagicMock
import networkx as nx
from backend.query_pipeline import QueryPipeline


def test_pipeline_simple_query():
    """Test standard single-hop vector retrieval flow."""
    vector_mock = MagicMock()
    vector_mock.hosted = False
    vector_mock.backend_name = "local_vector"
    vector_mock.search.return_value = [{"chunk_id": "c1", "text": "mock text", "source": "s1"}]

    pipeline = QueryPipeline(
        vector_store=vector_mock,
        bm25_store=MagicMock(),
        hybrid_searcher=MagicMock(),
        answer_generator=MagicMock(),
        refined_answer_generator=MagicMock(),
        graphrag_engine=MagicMock(),
        query_cache=MagicMock(),
        facts_store=MagicMock(),
    )

    pipeline.answer_generator.generate_answer.return_value = "prelim answer"
    pipeline.refined_answer_generator.generate_refined_answer.return_value = "refined answer"
    pipeline.query_cache.load.return_value = None
    pipeline.facts_store.summary.return_value = {"num_facts": 1}
    pipeline.facts_store.search_facts.return_value = []

    result = pipeline.run(
        query="What is the revenue?",
        mode="vector",
        indexed_docs=["doc1"],
        top_k=5,
        use_cache=False,
    )

    assert result["mode"] == "vector"
    assert len(result["selected_results"]) == 1
    assert result["preliminary_answer"] == "prelim answer"
    assert result["refined_answer"] == "refined answer"
    assert result["graph_context_text"] == ""
    vector_mock.search.assert_called_once_with(query="What is the revenue?", top_k=5)
    pipeline.graphrag_engine.build_graph_context.assert_not_called()
    pipeline.refined_answer_generator.generate_refined_answer.assert_called_once_with(
        question="What is the revenue?",
        preliminary_answer="prelim answer",
        retrieved_chunks=result["selected_results"],
        graph_context="",
        structured_facts=[],
    )


def test_pipeline_self_correction_failure_recovery():
    """Test that self-corrector triggers on bad retrieval and runs second pass."""

    vector_mock = MagicMock()
    vector_mock.hosted = False
    vector_mock.backend_name = "local_vector"
    # First returns bad data, second returns good
    vector_mock.search.side_effect = [
        [{"chunk_id": "bad", "text": "bad text", "source": "s1"}],
        [{"chunk_id": "good", "text": "good text", "source": "s1"}],
    ]

    corrector_mock = MagicMock()
    corrector_mock.grade_relevance.return_value = False
    corrector_mock.rewrite_query.return_value = "Rewritten Query"

    pipeline = QueryPipeline(
        vector_store=vector_mock,
        bm25_store=MagicMock(),
        hybrid_searcher=MagicMock(),
        answer_generator=MagicMock(),
        refined_answer_generator=MagicMock(),
        graphrag_engine=MagicMock(),
        query_cache=MagicMock(),
        self_corrector=corrector_mock,
        facts_store=MagicMock(),
    )

    pipeline.query_cache.load.return_value = None
    pipeline.facts_store.summary.return_value = {"num_facts": 0}
    pipeline.facts_store.search_facts.return_value = []

    result = pipeline.run(
        query="Bad Query",
        mode="vector",
        indexed_docs=["doc1"],
        top_k=5,
        use_cache=False,
        use_correction=True,
    )

    assert result["was_corrected"] is True
    assert result["rewritten_query"] == "Rewritten Query"
    assert result["selected_results"][0]["chunk_id"] == "good"
    assert vector_mock.search.call_count == 2


def test_pipeline_graphrag_falls_back_when_query_local_graph_build_fails():
    vector_mock = MagicMock()
    vector_mock.hosted = False
    vector_mock.backend_name = "local_vector"
    hybrid_mock = MagicMock()
    hybrid_mock.search.return_value = {
        "hybrid_results": [{"chunk_id": "c1", "text": "mock text", "source": "s1"}],
        "vector_results": [{"chunk_id": "c1", "text": "mock text", "source": "s1"}],
        "bm25_results": [],
    }
    graph_extractor = MagicMock()
    graph_extractor.should_extract_chunk.return_value = True
    graph_extractor.extract_from_chunk.side_effect = RuntimeError("graph failure")

    pipeline = QueryPipeline(
        vector_store=vector_mock,
        bm25_store=MagicMock(),
        hybrid_searcher=hybrid_mock,
        answer_generator=MagicMock(),
        refined_answer_generator=MagicMock(),
        graphrag_engine=MagicMock(),
        query_cache=MagicMock(),
        graph_extractor=graph_extractor,
        facts_store=MagicMock(),
    )

    pipeline.answer_generator.generate_answer.return_value = "prelim answer"
    pipeline.refined_answer_generator.generate_refined_answer.return_value = "refined answer"
    pipeline.query_cache.load.return_value = None
    pipeline.facts_store.summary.return_value = {"num_facts": 0}
    pipeline.facts_store.search_facts.return_value = []

    result = pipeline.run(
        query="What is the revenue?",
        mode="graphrag",
        indexed_docs=["doc1"],
        top_k=5,
        use_cache=False,
    )

    assert result["refined_answer"] == "refined answer"
    assert result["matched_nodes"] == []
    assert "Graph context unavailable" in result["graph_context_text"]


def test_pipeline_graphrag_builds_query_local_graph_from_retrieved_chunks():
    hybrid_mock = MagicMock()
    hybrid_mock.search.return_value = {
        "hybrid_results": [{"chunk_id": "c1", "text": "Revenue increased 12%", "source": "s1"}],
        "vector_results": [{"chunk_id": "c1", "text": "Revenue increased 12%", "source": "s1"}],
        "bm25_results": [],
    }

    graph_extractor = MagicMock()
    graph_extractor.should_extract_chunk.return_value = True
    graph_extractor.extract_from_chunk.return_value = {
        "chunk_id": "c1",
        "source": "s1",
        "entities": [{"name": "Tesla", "type": "Organization"}],
        "relationships": [],
    }

    linker = MagicMock()
    linker.extract_query_entities.return_value = [{"name": "Tesla", "type": "Organization"}]

    persistent_graph_engine = MagicMock()
    persistent_graph_engine.query_graph_linker = linker

    pipeline = QueryPipeline(
        vector_store=MagicMock(hosted=False, backend_name="local_vector"),
        bm25_store=MagicMock(),
        hybrid_searcher=hybrid_mock,
        answer_generator=MagicMock(),
        refined_answer_generator=MagicMock(),
        graphrag_engine=persistent_graph_engine,
        query_cache=MagicMock(),
        graph_extractor=graph_extractor,
        facts_store=MagicMock(),
    )

    pipeline.answer_generator.generate_answer.return_value = "prelim answer"
    pipeline.refined_answer_generator.generate_refined_answer.return_value = "refined answer"
    pipeline.query_cache.load.return_value = None
    pipeline.facts_store.summary.return_value = {"num_facts": 0}
    pipeline.facts_store.search_facts.return_value = []

    result = pipeline.run(
        query="How is Tesla performing?",
        mode="graphrag",
        indexed_docs=["doc1"],
        top_k=5,
        use_cache=False,
    )

    assert result["mode"] == "graphrag"
    assert result["matched_nodes"] == []
    assert "graph" in result["graph_context_text"].lower() or "relationships" in result["graph_context_text"].lower()
    graph_extractor.extract_from_chunk.assert_called_once()
    persistent_graph_engine.build_graph_context.assert_not_called()


def test_pipeline_file_search_mode_uses_hosted_retrieval():
    vector_mock = MagicMock()
    vector_mock.hosted = True
    vector_mock.backend_name = "openai_file_search"
    vector_mock.search.return_value = [{"chunk_id": "c1", "text": "hosted result", "source": "s1"}]

    pipeline = QueryPipeline(
        vector_store=vector_mock,
        bm25_store=MagicMock(),
        hybrid_searcher=MagicMock(),
        answer_generator=MagicMock(),
        refined_answer_generator=MagicMock(),
        graphrag_engine=MagicMock(),
        query_cache=MagicMock(),
        facts_store=MagicMock(),
    )

    pipeline.answer_generator.generate_answer.return_value = "prelim answer"
    pipeline.refined_answer_generator.generate_refined_answer.return_value = "refined answer"
    pipeline.query_cache.load.return_value = None
    pipeline.facts_store.summary.return_value = {"num_facts": 0}
    pipeline.facts_store.search_facts.return_value = []

    result = pipeline.run(
        query="What happened to revenue?",
        mode="file_search",
        indexed_docs=["tesla.pdf::abc123"],
        top_k=5,
        use_cache=False,
    )

    assert result["mode"] == "file_search"
    assert result["selected_results"][0]["text"] == "hosted result"
    assert result["graph_context_text"] == ""
    vector_mock.search.assert_called_once_with(query="What happened to revenue?", top_k=5)
    pipeline.graphrag_engine.build_graph_context.assert_not_called()


def test_pipeline_supported_modes_follow_backend_type():
    hosted_pipeline = QueryPipeline(
        vector_store=MagicMock(hosted=True, backend_name="openai_file_search"),
        bm25_store=MagicMock(),
        hybrid_searcher=MagicMock(),
        answer_generator=MagicMock(),
        refined_answer_generator=MagicMock(),
        graphrag_engine=MagicMock(),
        query_cache=MagicMock(),
        facts_store=MagicMock(),
    )
    local_pipeline = QueryPipeline(
        vector_store=MagicMock(hosted=False, backend_name="local_vector"),
        bm25_store=MagicMock(),
        hybrid_searcher=MagicMock(),
        answer_generator=MagicMock(),
        refined_answer_generator=MagicMock(),
        graphrag_engine=MagicMock(),
        query_cache=MagicMock(),
        facts_store=MagicMock(),
    )

    assert hosted_pipeline.supported_modes() == ["file_search", "graphrag"]
    assert local_pipeline.supported_modes() == ["vector", "hybrid", "bm25", "graphrag"]


def test_pipeline_rejects_modes_not_supported_by_the_active_backend():
    hosted_pipeline = QueryPipeline(
        vector_store=MagicMock(hosted=True, backend_name="openai_file_search"),
        bm25_store=MagicMock(),
        hybrid_searcher=MagicMock(),
        answer_generator=MagicMock(),
        refined_answer_generator=MagicMock(),
        graphrag_engine=MagicMock(),
        query_cache=MagicMock(),
        facts_store=MagicMock(),
    )

    with pytest.raises(ValueError, match="Unsupported mode 'vector'"):
        hosted_pipeline.run(
            query="What changed?",
            mode="vector",
            indexed_docs=["doc1"],
            top_k=5,
            use_cache=False,
        )


def test_pipeline_includes_structured_facts_in_generation_and_context():
    vector_mock = MagicMock()
    vector_mock.hosted = True
    vector_mock.backend_name = "openai_file_search"
    vector_mock.search.return_value = [{"chunk_id": "c1", "text": "Tesla revenue grew", "source": "tesla.pdf"}]

    facts_store = MagicMock()
    facts_store.summary.return_value = {"num_facts": 3}
    facts_store.search_facts.return_value = [
        {
            "source_name": "tesla.pdf",
            "metric_key": "revenue",
            "metric_label": "Revenue",
            "period": "2024",
            "page_label": "Page 4",
            "value_text": "$97,690 million",
            "evidence_text": "Revenue was $97,690 million.",
        }
    ]

    pipeline = QueryPipeline(
        vector_store=vector_mock,
        bm25_store=MagicMock(),
        hybrid_searcher=MagicMock(),
        answer_generator=MagicMock(),
        refined_answer_generator=MagicMock(),
        graphrag_engine=MagicMock(),
        query_cache=MagicMock(),
        facts_store=facts_store,
    )

    pipeline.answer_generator.generate_answer.return_value = "prelim answer using Fact 1"
    pipeline.refined_answer_generator.generate_refined_answer.return_value = "refined answer using Fact 1"
    pipeline.query_cache.load.return_value = None

    result = pipeline.run(
        query="What was Tesla revenue in 2024?",
        mode="file_search",
        indexed_docs=["tesla.pdf::hash"],
        top_k=5,
        use_cache=False,
    )

    assert result["selected_facts"][0]["metric_key"] == "revenue"
    assert "Fact 1" in result["facts_context_text"]
    assert "Structured Facts:" in result["retrieved_context_text"]
    pipeline.answer_generator.generate_answer.assert_called_once_with(
        question="What was Tesla revenue in 2024?",
        retrieved_chunks=result["selected_results"],
        structured_facts=result["selected_facts"],
    )
    pipeline.refined_answer_generator.generate_refined_answer.assert_called_once_with(
        question="What was Tesla revenue in 2024?",
        preliminary_answer="prelim answer using Fact 1",
        retrieved_chunks=result["selected_results"],
        graph_context="",
        structured_facts=result["selected_facts"],
    )


def test_pipeline_fact_aware_reranking_promotes_chunk_matching_fact_evidence():
    vector_mock = MagicMock()
    vector_mock.hosted = True
    vector_mock.backend_name = "openai_file_search"
    vector_mock.search.return_value = [
        {"chunk_id": "c1", "text": "Tesla discussed manufacturing expansion.", "source": "tesla.pdf"},
        {"chunk_id": "c2", "text": "Revenue was $97,690 million in 2024 for Tesla.", "source": "tesla.pdf"},
    ]

    facts_store = MagicMock()
    facts_store.summary.return_value = {"num_facts": 5}
    facts_store.search_facts.return_value = [
        {
            "source_name": "tesla.pdf",
            "metric_key": "revenue",
            "metric_label": "Revenue",
            "period": "2024",
            "page_label": "Page 4",
            "value_text": "$97,690 million",
            "evidence_text": "Revenue was $97,690 million in 2024.",
            "match_score": 12,
        }
    ]

    pipeline = QueryPipeline(
        vector_store=vector_mock,
        bm25_store=MagicMock(),
        hybrid_searcher=MagicMock(),
        answer_generator=MagicMock(),
        refined_answer_generator=MagicMock(),
        graphrag_engine=MagicMock(),
        query_cache=MagicMock(),
        facts_store=facts_store,
    )

    pipeline.answer_generator.generate_answer.return_value = "prelim answer"
    pipeline.refined_answer_generator.generate_refined_answer.return_value = "refined answer"
    pipeline.query_cache.load.return_value = None

    result = pipeline.run(
        query="What was Tesla revenue in 2024?",
        mode="file_search",
        indexed_docs=["tesla.pdf::hash"],
        top_k=5,
        use_cache=False,
    )

    assert result["fact_rerank_applied"] is True
    assert result["selected_results"][0]["chunk_id"] == "c2"
    assert result["selected_results"][0]["fact_rerank_score"] > 0


def test_pipeline_self_corrector_uses_structured_facts_in_relevance_context():
    vector_mock = MagicMock()
    vector_mock.hosted = False
    vector_mock.backend_name = "local_vector"
    vector_mock.search.return_value = [{"chunk_id": "c1", "text": "General Tesla discussion", "source": "tesla.pdf"}]

    corrector_mock = MagicMock()
    corrector_mock.grade_relevance.return_value = True

    facts_store = MagicMock()
    facts_store.summary.return_value = {"num_facts": 2}
    facts_store.search_facts.return_value = [
        {
            "source_name": "tesla.pdf",
            "metric_key": "revenue",
            "metric_label": "Revenue",
            "period": "2024",
            "page_label": "Page 4",
            "value_text": "$97,690 million",
            "evidence_text": "Revenue was $97,690 million in 2024.",
            "match_score": 11,
        }
    ]

    pipeline = QueryPipeline(
        vector_store=vector_mock,
        bm25_store=MagicMock(),
        hybrid_searcher=MagicMock(),
        answer_generator=MagicMock(),
        refined_answer_generator=MagicMock(),
        graphrag_engine=MagicMock(),
        query_cache=MagicMock(),
        self_corrector=corrector_mock,
        facts_store=facts_store,
    )

    pipeline.answer_generator.generate_answer.return_value = "prelim answer"
    pipeline.refined_answer_generator.generate_refined_answer.return_value = "refined answer"
    pipeline.query_cache.load.return_value = None

    pipeline.run(
        query="What was Tesla revenue in 2024?",
        mode="vector",
        indexed_docs=["tesla.pdf::hash"],
        top_k=5,
        use_cache=False,
        use_correction=True,
    )

    relevance_context = corrector_mock.grade_relevance.call_args.args[1]
    assert "Structured Facts:" in relevance_context
    assert "Fact 1" in relevance_context


def test_pipeline_graphrag_prefers_persisted_graph_neighborhood():
    vector_mock = MagicMock()
    vector_mock.hosted = True
    vector_mock.backend_name = "openai_file_search"
    vector_mock.search.return_value = [
        {"chunk_id": "c1", "text": "Tesla discussed revenue in 2024.", "source": "tesla.pdf"}
    ]

    facts_store = MagicMock()
    facts_store.summary.return_value = {"num_facts": 1}
    facts_store.search_facts.return_value = []

    graph_store = MagicMock()
    graph_store.graph_summary.return_value = {"num_nodes": 4, "num_edges": 3}
    graph_store.is_configured.return_value = True

    graph = nx.MultiDiGraph()
    graph.add_node("Document::tesla.pdf", label="tesla.pdf", entity_type="Document")
    graph.add_node("Organization::tesla", label="Tesla", entity_type="Organization")
    graph.add_node(
        "Metric::tesla.pdf::revenue::2024::1",
        label="Revenue: $97,690 million",
        entity_type="Metric",
    )
    graph.add_node("Period::2024", label="2024", entity_type="Period")
    graph.add_edge(
        "Document::tesla.pdf",
        "Organization::tesla",
        relationship_type="MENTIONS_ENTITY",
        source_doc="tesla.pdf",
    )
    graph.add_edge(
        "Document::tesla.pdf",
        "Metric::tesla.pdf::revenue::2024::1",
        relationship_type="REPORTS_METRIC",
        source_doc="tesla.pdf",
    )
    graph.add_edge(
        "Metric::tesla.pdf::revenue::2024::1",
        "Period::2024",
        relationship_type="FOR_PERIOD",
        source_doc="tesla.pdf",
    )
    graph_store.get_sources_graph.return_value = graph

    linker = MagicMock()
    linker.extract_query_entities.return_value = [{"name": "Tesla", "type": "Organization"}]
    graphrag_engine = MagicMock()
    graphrag_engine.query_graph_linker = linker

    pipeline = QueryPipeline(
        vector_store=vector_mock,
        bm25_store=MagicMock(),
        hybrid_searcher=MagicMock(),
        answer_generator=MagicMock(),
        refined_answer_generator=MagicMock(),
        graphrag_engine=graphrag_engine,
        query_cache=MagicMock(),
        facts_store=facts_store,
        graph_store=graph_store,
    )

    pipeline.answer_generator.generate_answer.return_value = "prelim answer"
    pipeline.refined_answer_generator.generate_refined_answer.return_value = "refined answer"
    pipeline.query_cache.load.return_value = None

    result = pipeline.run(
        query="What was Tesla revenue in 2024?",
        mode="graphrag",
        indexed_docs=["tesla.pdf::hash"],
        top_k=5,
        use_cache=False,
    )

    assert result["graph_context_origin"] == "persisted_graph"


def test_pipeline_uses_query_local_structured_facts_when_store_search_misses():
    vector_mock = MagicMock()
    vector_mock.hosted = True
    vector_mock.backend_name = "openai_file_search"
    vector_mock.search.return_value = [
        {
            "chunk_id": "c1",
            "text": "Automotive sales were $18,659 million in fiscal year 2024 and regulatory credits were $692 million.",
            "source": "tesla.pdf",
        }
    ]

    facts_store = MagicMock()
    facts_store.summary.return_value = {"num_facts": 10}
    facts_store.search_facts.return_value = []
    facts_store.rank_facts.side_effect = lambda query, facts, limit=6: [
        {**fact, "match_score": 10} for fact in facts[:limit]
    ]

    facts_extractor = MagicMock()
    facts_extractor.extract_from_section.return_value = [
        {
            "fact_id": "fact-1",
            "file_hash": "c1",
            "source_name": "tesla.pdf",
            "section_index": 1,
            "page_label": "Page 1",
            "metric_key": "automotive_sales",
            "metric_label": "Automotive sales",
            "period": "2024",
            "value_text": "$18,659 million",
            "value_numeric": 18659.0,
            "normalized_value": 18659000000.0,
            "unit": "million",
            "currency": "$",
            "evidence_text": "Automotive sales were $18,659 million in fiscal year 2024.",
        }
    ]

    pipeline = QueryPipeline(
        vector_store=vector_mock,
        bm25_store=MagicMock(),
        hybrid_searcher=MagicMock(),
        answer_generator=MagicMock(),
        refined_answer_generator=MagicMock(),
        graphrag_engine=MagicMock(),
        query_cache=MagicMock(),
        facts_extractor=facts_extractor,
        facts_store=facts_store,
        graph_store=MagicMock(),
    )

    pipeline.answer_generator.generate_answer.return_value = "prelim answer"
    pipeline.refined_answer_generator.generate_refined_answer.return_value = "refined answer"
    pipeline.query_cache.load.return_value = None

    result = pipeline.run(
        query="Show the key relationships among Tesla automotive sales and fiscal year 2024",
        mode="file_search",
        indexed_docs=["tesla.pdf::hash"],
        top_k=5,
        use_cache=False,
    )

    assert result["selected_facts"]
    assert result["selected_facts"][0]["metric_key"] == "automotive_sales"
    assert "Automotive sales" in result["facts_context_text"]
