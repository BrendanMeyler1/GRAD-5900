from unittest.mock import MagicMock

from backend.eval.ragas_runner import RagasRunner
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
    query_pipeline.supported_modes.return_value = ["file_search", "graphrag"]

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


def test_eval_runner_defaults_to_query_pipeline_supported_modes():
    query_pipeline = MagicMock()
    query_pipeline.supported_modes.return_value = ["file_search", "graphrag"]
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
    dataset = [{"id": "q1", "question": "What changed?"}]

    results = runner.run_dataset(dataset=dataset, indexed_docs=["doc::hash"])

    assert set(results["summary"]) == {"file_search", "graphrag"}
    assert query_pipeline.run.call_count == 2


def test_eval_runner_includes_standardized_rag_metrics_when_runner_is_present():
    query_pipeline = MagicMock()
    query_pipeline.supported_modes.return_value = ["file_search"]
    query_pipeline.run.return_value = {
        "selected_results": [{"source": "10-K.pdf", "text": "Revenue increased", "chunk_id": "c1"}],
        "retrieved_context_text": "Revenue increased according to the 10-K.",
        "graph_context_text": "",
        "preliminary_answer": "Revenue increased.",
        "refined_answer": "Revenue increased.",
        "cache_hit": False,
    }

    llm_judge = MagicMock()
    llm_judge.judge_answer.return_value = {
        "scores": {
            "faithfulness": 4,
            "relevance": 4,
            "completeness": 4,
            "groundedness": 4,
            "graph_usefulness": 0,
            "reasoning_quality": 4,
            "overall": 4,
        },
        "rationales": {},
        "summary": "Strong answer.",
    }

    runner = EvaluationRunner(
        query_pipeline=query_pipeline,
        llm_judge=llm_judge,
        ragas_runner=RagasRunner(evaluation_backend="proxy"),
    )
    dataset = [
        {
            "id": "q1",
            "question": "What happened to revenue?",
            "ideal_answer": "Revenue increased.",
            "sources": ["10-K.pdf"],
        }
    ]

    results = runner.run_dataset(dataset=dataset, indexed_docs=["doc::hash"], modes=["file_search"])
    mode_summary = results["summary"]["file_search"]
    item_result = results["results_by_mode"]["file_search"][0]

    assert mode_summary["ragas_backend"] == "proxy"
    assert "average_ragas_metrics" in mode_summary
    assert mode_summary["average_ragas_overall"] >= 0.0
    assert item_result["ragas"]["scores"]["overall"] >= 0.0
