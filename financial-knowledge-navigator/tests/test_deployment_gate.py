from backend.eval.deployment_gate import DeploymentGateEvaluator


def test_deployment_gate_marks_mode_ready_when_all_thresholds_pass():
    evaluator = DeploymentGateEvaluator(
        min_combined_overall=0.6,
        min_ragas_overall=0.5,
        min_online_runs=3,
        min_feedback_count=2,
        min_positive_rate=0.6,
        max_avg_latency_ms=5000,
    )

    result = evaluator.evaluate(
        offline_eval_results={
            "summary": {
                "file_search": {
                    "average_combined_overall": 0.72,
                    "average_ragas_overall": 0.63,
                }
            }
        },
        online_summary={"num_runs": 8},
        online_mode_rows=[
            {
                "mode": "file_search",
                "num_runs": 8,
                "feedback_count": 3,
                "positive_rate": 2 / 3,
                "avg_latency_ms": 1400,
            }
        ],
    )

    assert result["overall_ready"] is True
    assert result["best_candidate_mode"] == "file_search"
    assert result["mode_results"]["file_search"]["ready"] is True


def test_deployment_gate_reports_blockers_when_online_thresholds_fail():
    evaluator = DeploymentGateEvaluator(
        min_combined_overall=0.6,
        min_ragas_overall=0.5,
        min_online_runs=5,
        min_feedback_count=3,
        min_positive_rate=0.7,
        max_avg_latency_ms=1000,
    )

    result = evaluator.evaluate(
        offline_eval_results={
            "summary": {
                "graphrag": {
                    "average_combined_overall": 0.74,
                    "average_ragas_overall": 0.66,
                }
            }
        },
        online_summary={"num_runs": 2},
        online_mode_rows=[
            {
                "mode": "graphrag",
                "num_runs": 2,
                "feedback_count": 1,
                "positive_rate": 0.0,
                "avg_latency_ms": 2200,
            }
        ],
    )

    assert result["overall_ready"] is False
    assert result["best_candidate_mode"] is None
    assert result["mode_results"]["graphrag"]["ready"] is False
    assert "No retrieval mode currently satisfies the deployment gate thresholds." in result["blockers"]


def test_deployment_gate_requires_offline_eval_results():
    evaluator = DeploymentGateEvaluator()
    result = evaluator.evaluate(
        offline_eval_results=None,
        online_summary={"num_runs": 0},
        online_mode_rows=[],
    )

    assert result["overall_ready"] is False
    assert result["mode_results"] == {}
    assert result["blockers"] == ["Run the offline golden-dataset evaluation first."]
