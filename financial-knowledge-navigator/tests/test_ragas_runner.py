from backend.eval.ragas_runner import RagasRunner


def test_ragas_runner_returns_proxy_scores():
    runner = RagasRunner(evaluation_backend="proxy")

    result = runner.score_answer(
        question="How did Tesla revenue change in 2024?",
        ideal_answer="Tesla revenue increased in 2024.",
        retrieved_context="Tesla revenue increased in 2024 according to the filing.",
        graph_context="Tesla REPORTS_METRIC Revenue [source: filing]",
        candidate_answer="Tesla revenue increased in 2024.",
        expected_sources=["tesla.pdf"],
        retrieved_sources=["tesla.pdf"],
    )

    assert result["backend"] == "proxy"
    assert result["native_backend"] is False
    assert set(result["scores"]) == {
        "context_precision",
        "context_recall",
        "answer_relevancy",
        "answer_correctness",
        "faithfulness",
        "overall",
    }
    assert result["scores"]["context_precision"] == 1.0


def test_ragas_runner_uses_native_backend_when_available(monkeypatch):
    runner = RagasRunner(evaluation_backend="proxy")

    monkeypatch.setattr(runner, "_should_try_native", lambda: True)
    monkeypatch.setattr(
        runner,
        "_score_with_native_backend",
        lambda **kwargs: {
            "backend": "ragas",
            "native_backend": True,
            "scores": {
                "context_precision": 0.9,
                "context_recall": 0.8,
                "answer_relevancy": 0.7,
                "answer_correctness": 0.85,
                "faithfulness": 0.88,
                "overall": 0.826,
            },
            "summary": "Native ragas evaluation using the installed ragas package.",
        },
    )

    result = runner.score_answer(
        question="How did Tesla revenue change in 2024?",
        ideal_answer="Tesla revenue increased in 2024.",
        retrieved_context="Tesla revenue increased in 2024 according to the filing.",
        graph_context="Tesla REPORTS_METRIC Revenue [source: filing]",
        candidate_answer="Tesla revenue increased in 2024.",
        expected_sources=["tesla.pdf"],
        retrieved_sources=["tesla.pdf"],
    )

    assert result["backend"] == "ragas"
    assert result["native_backend"] is True
    assert result["scores"]["overall"] == 0.826


def test_ragas_runner_falls_back_to_proxy_when_native_backend_errors(monkeypatch):
    runner = RagasRunner(evaluation_backend="proxy")

    monkeypatch.setattr(runner, "_should_try_native", lambda: True)

    def explode(**kwargs):
        raise RuntimeError("ragas unavailable")

    monkeypatch.setattr(runner, "_score_with_native_backend", explode)

    result = runner.score_answer(
        question="How did Tesla revenue change in 2024?",
        ideal_answer="Tesla revenue increased in 2024.",
        retrieved_context="Tesla revenue increased in 2024 according to the filing.",
        graph_context="Tesla REPORTS_METRIC Revenue [source: filing]",
        candidate_answer="Tesla revenue increased in 2024.",
        expected_sources=["tesla.pdf"],
        retrieved_sources=["tesla.pdf"],
    )

    assert result["backend"] == "proxy"
    assert result["native_backend"] is False
    assert "Proxy fallback" in result["summary"]
