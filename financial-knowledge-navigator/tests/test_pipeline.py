from unittest.mock import MagicMock
from backend.query_pipeline import QueryPipeline

def test_pipeline_simple_query():
    """Test standard single-hop vector retrieval flow."""
    vector_mock = MagicMock()
    vector_mock.search.return_value = [{"chunk_id": "c1", "text": "mock text", "source": "s1"}]
    
    pipeline = QueryPipeline(
        vector_store=vector_mock,
        bm25_store=MagicMock(),
        hybrid_searcher=MagicMock(),
        answer_generator=MagicMock(),
        refined_answer_generator=MagicMock(),
        graphrag_engine=MagicMock(),
        query_cache=MagicMock(),
    )
    
    pipeline.answer_generator.generate_answer.return_value = "prelim answer"
    pipeline.graphrag_engine.build_graph_context.return_value = {"graph_context_text": "graph ctx", "matched_nodes": []}
    pipeline.refined_answer_generator.generate_refined_answer.return_value = "refined answer"
    pipeline.query_cache.load.return_value = None
    
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
    vector_mock.search.assert_called_once_with(query="What is the revenue?", top_k=5)

def test_pipeline_self_correction_failure_recovery():
    """Test that self-corrector triggers on bad retrieval and runs second pass."""
    
    vector_mock = MagicMock()
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
    )
    
    pipeline.graphrag_engine.build_graph_context.return_value = {"graph_context_text": "", "matched_nodes": []}
    pipeline.query_cache.load.return_value = None
    
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


def test_pipeline_falls_back_when_graph_context_build_fails():
    vector_mock = MagicMock()
    vector_mock.search.return_value = [{"chunk_id": "c1", "text": "mock text", "source": "s1"}]

    pipeline = QueryPipeline(
        vector_store=vector_mock,
        bm25_store=MagicMock(),
        hybrid_searcher=MagicMock(),
        answer_generator=MagicMock(),
        refined_answer_generator=MagicMock(),
        graphrag_engine=MagicMock(),
        query_cache=MagicMock(),
    )

    pipeline.answer_generator.generate_answer.return_value = "prelim answer"
    pipeline.graphrag_engine.build_graph_context.side_effect = RuntimeError("graph failure")
    pipeline.refined_answer_generator.generate_refined_answer.return_value = "refined answer"
    pipeline.query_cache.load.return_value = None

    result = pipeline.run(
        query="What is the revenue?",
        mode="vector",
        indexed_docs=["doc1"],
        top_k=5,
        use_cache=False,
    )

    assert result["refined_answer"] == "refined answer"
    assert result["matched_nodes"] == []
    assert "Graph context unavailable" in result["graph_context_text"]
