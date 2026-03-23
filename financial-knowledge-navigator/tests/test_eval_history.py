import pytest

from backend.eval.history import EvaluationHistoryManager


def test_compare_runs_includes_standardized_rag_deltas():
    manager = EvaluationHistoryManager(eval_dir="tests/apptemp/eval_history")

    run_a = {
        "summary": {
            "file_search": {
                "average_combined_overall": 0.5,
                "average_heuristic_overall": 0.4,
                "average_llm_overall_0_to_5": 3.0,
                "average_ragas_overall": 0.3,
                "cache_hits": 1,
                "average_heuristic_metrics": {"token_overlap": 0.4},
                "average_llm_metrics": {"overall": 3.0},
                "average_ragas_metrics": {"overall": 0.3, "faithfulness": 0.2},
            }
        }
    }
    run_b = {
        "summary": {
            "file_search": {
                "average_combined_overall": 0.7,
                "average_heuristic_overall": 0.6,
                "average_llm_overall_0_to_5": 4.0,
                "average_ragas_overall": 0.55,
                "cache_hits": 3,
                "average_heuristic_metrics": {"token_overlap": 0.6},
                "average_llm_metrics": {"overall": 4.0},
                "average_ragas_metrics": {"overall": 0.55, "faithfulness": 0.5},
            }
        }
    }

    comparison = manager.compare_runs(run_a, run_b)

    assert comparison["file_search"]["average_ragas_overall_delta"] == pytest.approx(0.25)
    assert comparison["file_search"]["ragas_metric_deltas"]["faithfulness"] == pytest.approx(0.3)
