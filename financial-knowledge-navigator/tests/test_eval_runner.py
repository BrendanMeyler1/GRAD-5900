from unittest.mock import MagicMock

from backend.eval.runner import EvaluationRunner


def _build_pipeline_result():
    return {
        "selected_results": [{"source": "10-K.pdf", "text": "context", "chunk_id": "c1"}],
        "retrieved_context_text": "retrieved context",
        "graph_context_text": "Apple LINKED_TO Services",
        "preliminary_answer": "",
        "refined_answer": "",
        "cache_hit": False,
    }


def test_non_graphrag_modes_do_not_get_graph_context_credit():
    query_pipeline = MagicMock()
    query_pipeline.run.return_value = _build_pipeline_result()

    llm_judge = MagicMock()
    llm_judge.judge_answer.return_value = {
        "scores": {
            "faithfulness": 0,
            "relevance": 0,
            "completeness": 0,
            "groundedness": 0,
            "graph_usefulness": 0,
            "reasoning_quality": 0,
            "overall": 0,
        },
        "rationales": {},
        "summary": "",
    }

    runner = EvaluationRunner(query_pipeline=query_pipeline, llm_judge=llm_judge)
    item = {
        "id": "q1",
        "question": "What is Apple linked to?",
        "ideal_answer": "Apple is linked to Services.",
        "key_entities": ["Apple"],
        "expected_relationships": ["LINKED_TO"],
        "sources": ["10-K.pdf"],
    }

    vector_result = runner._run_single_mode(item, mode="vector", indexed_docs=["doc::hash"])
    graphrag_result = runner._run_single_mode(item, mode="graphrag", indexed_docs=["doc::hash"])

    assert vector_result["heuristic_scores"]["entity_coverage"] == 0.0
    assert vector_result["heuristic_scores"]["relationship_coverage"] == 0.0
    assert graphrag_result["heuristic_scores"]["entity_coverage"] == 1.0
    assert graphrag_result["heuristic_scores"]["relationship_coverage"] == 1.0
