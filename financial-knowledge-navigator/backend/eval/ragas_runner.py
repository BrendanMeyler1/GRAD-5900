from typing import Any, Dict, List, Optional

from backend.core.config import settings
from backend.eval.metrics import normalize_text, tokenize, token_overlap_score


def _overlap_fraction(numerator_tokens: List[str], denominator_tokens: List[str]) -> float:
    denominator = set(denominator_tokens)
    if not denominator:
        return 0.0
    numerator = set(numerator_tokens)
    return len(numerator & denominator) / len(denominator)


class RagasRunner:
    """
    Lightweight standardized RAG evaluator.

    This runner intentionally uses local proxy metrics so the project can expose
    Ragas-style offline evaluation even when the optional `ragas` package is not
    installed. The return shape is stable and can be upgraded to a native Ragas
    backend later without changing the rest of the app.
    """

    backend_name = "proxy"

    def __init__(self, evaluation_backend: Optional[str] = None):
        self.requested_backend = (
            (evaluation_backend or settings.evaluation_backend or "auto").strip().lower()
        )
        self._native_checked = False
        self._native_available = False
        self._native_error = None
        self._native_modules: Dict[str, Any] = {}
        if self._should_try_native():
            self._load_native_backend()

    def _should_try_native(self) -> bool:
        return self.requested_backend in {"auto", "ragas", "native", "native_ragas"}

    def _proxy_score_answer(
        self,
        question: str,
        ideal_answer: str,
        retrieved_context: str,
        graph_context: str,
        candidate_answer: str,
        expected_sources: List[str] | None = None,
        retrieved_sources: List[str] | None = None,
    ) -> Dict[str, Any]:
        expected_sources = expected_sources or []
        retrieved_sources = retrieved_sources or []

        normalized_context = normalize_text(f"{retrieved_context} {graph_context}")
        context_tokens = tokenize(normalized_context)
        answer_tokens = tokenize(candidate_answer)
        question_tokens = tokenize(question)
        ideal_tokens = tokenize(ideal_answer)

        retrieved_source_set = {source.lower() for source in retrieved_sources if source}
        expected_source_set = {source.lower() for source in expected_sources if source}

        context_precision = (
            len(retrieved_source_set & expected_source_set) / len(retrieved_source_set)
            if retrieved_source_set
            else 0.0
        )
        context_recall = _overlap_fraction(ideal_tokens, context_tokens)
        answer_relevancy = _overlap_fraction(question_tokens, answer_tokens)
        answer_correctness = token_overlap_score(ideal_answer, candidate_answer)
        faithfulness = _overlap_fraction(context_tokens, answer_tokens)

        scores = {
            "context_precision": context_precision,
            "context_recall": context_recall,
            "answer_relevancy": answer_relevancy,
            "answer_correctness": answer_correctness,
            "faithfulness": faithfulness,
        }
        overall = sum(scores.values()) / len(scores) if scores else 0.0

        return {
            "backend": "proxy",
            "native_backend": False,
            "scores": {**scores, "overall": overall},
            "summary": (
                "Standardized proxy RAG metrics derived from retrieved context, "
                "expected sources, and answer overlap."
            ),
        }

    def _metric_aliases(self) -> Dict[str, List[str]]:
        return {
            "context_precision": ["context_precision", "ContextPrecision"],
            "context_recall": ["context_recall", "ContextRecall"],
            "answer_relevancy": ["answer_relevancy", "response_relevancy", "ResponseRelevancy"],
            "answer_correctness": [
                "answer_correctness",
                "AnswerCorrectness",
                "factual_correctness",
                "FactualCorrectness",
            ],
            "faithfulness": ["faithfulness", "Faithfulness"],
        }

    def _load_native_backend(self) -> None:
        if self._native_checked:
            return

        self._native_checked = True
        try:
            from ragas import evaluate as ragas_evaluate  # type: ignore
        except Exception as exc:  # pragma: no cover - depends on optional install
            self._native_error = str(exc)
            return

        EvaluationDataset = None
        SingleTurnSample = None
        try:  # pragma: no branch - version-dependent import
            from ragas.dataset_schema import EvaluationDataset, SingleTurnSample  # type: ignore
        except Exception:
            try:  # pragma: no cover - compatibility fallback
                from ragas import EvaluationDataset, SingleTurnSample  # type: ignore
            except Exception as exc:  # pragma: no cover
                self._native_error = str(exc)
                return

        try:
            from ragas import metrics as metrics_module  # type: ignore
        except Exception as exc:  # pragma: no cover
            self._native_error = str(exc)
            return

        resolved_metrics = []
        metric_name_map: Dict[str, str] = {}
        for public_name, aliases in self._metric_aliases().items():
            metric_obj = None
            for alias in aliases:
                if hasattr(metrics_module, alias):
                    candidate = getattr(metrics_module, alias)
                    metric_obj = candidate() if isinstance(candidate, type) else candidate
                    metric_name_map[public_name] = getattr(metric_obj, "name", public_name)
                    resolved_metrics.append(metric_obj)
                    break
            if metric_obj is None:
                metric_name_map[public_name] = public_name

        if not resolved_metrics:
            self._native_error = "No compatible ragas metrics could be resolved."
            return

        self._native_modules = {
            "evaluate": ragas_evaluate,
            "EvaluationDataset": EvaluationDataset,
            "SingleTurnSample": SingleTurnSample,
            "metrics": resolved_metrics,
            "metric_name_map": metric_name_map,
        }
        self._native_available = True
        self.backend_name = "ragas"

    def is_native_backend(self) -> bool:
        if self._should_try_native():
            self._load_native_backend()
        return self._native_available

    def _coerce_native_scores(self, result: Any) -> Dict[str, float]:
        if result is None:
            return {}

        if isinstance(result, dict):
            return {str(k): float(v) for k, v in result.items() if isinstance(v, (int, float))}

        scores_attr = getattr(result, "scores", None)
        if isinstance(scores_attr, dict):
            return {str(k): float(v) for k, v in scores_attr.items() if isinstance(v, (int, float))}

        if hasattr(result, "to_pandas"):
            try:
                df = result.to_pandas()
                if len(df.index):
                    row = df.iloc[0].to_dict()
                    return {str(k): float(v) for k, v in row.items() if isinstance(v, (int, float))}
            except Exception:
                pass

        try:
            as_dict = dict(result)
            return {str(k): float(v) for k, v in as_dict.items() if isinstance(v, (int, float))}
        except Exception:
            return {}

    def _score_with_native_backend(
        self,
        question: str,
        ideal_answer: str,
        retrieved_context: str,
        graph_context: str,
        candidate_answer: str,
    ) -> Dict[str, Any]:
        self._load_native_backend()
        if not self._native_available:
            raise RuntimeError(self._native_error or "Native ragas backend unavailable.")

        proxy_baseline = self._proxy_score_answer(
            question=question,
            ideal_answer=ideal_answer,
            retrieved_context=retrieved_context,
            graph_context=graph_context,
            candidate_answer=candidate_answer,
            expected_sources=[],
            retrieved_sources=[],
        )

        sample_payload = {
            "user_input": question,
            "response": candidate_answer,
            "reference": ideal_answer,
            "retrieved_contexts": [
                part for part in [retrieved_context, graph_context] if (part or "").strip()
            ] or [""],
        }
        dataset = self._native_modules["EvaluationDataset"](
            samples=[self._native_modules["SingleTurnSample"](**sample_payload)]
        )
        result = self._native_modules["evaluate"](
            dataset=dataset,
            metrics=self._native_modules["metrics"],
            show_progress=False,
            raise_exceptions=False,
        )
        native_scores = self._coerce_native_scores(result)
        metric_name_map = self._native_modules["metric_name_map"]

        merged_scores = {}
        for public_name in self._metric_aliases().keys():
            native_key = metric_name_map.get(public_name, public_name)
            merged_scores[public_name] = float(
                native_scores.get(native_key, proxy_baseline["scores"].get(public_name, 0.0))
            )
        merged_scores["overall"] = (
            sum(merged_scores.values()) / len(merged_scores) if merged_scores else 0.0
        )
        return {
            "backend": "ragas",
            "native_backend": True,
            "scores": merged_scores,
            "summary": "Native ragas evaluation using the installed ragas package.",
        }

    def score_answer(
        self,
        question: str,
        ideal_answer: str,
        retrieved_context: str,
        graph_context: str,
        candidate_answer: str,
        expected_sources: List[str] | None = None,
        retrieved_sources: List[str] | None = None,
    ) -> Dict[str, Any]:
        if self._should_try_native():
            try:
                return self._score_with_native_backend(
                    question=question,
                    ideal_answer=ideal_answer,
                    retrieved_context=retrieved_context,
                    graph_context=graph_context,
                    candidate_answer=candidate_answer,
                )
            except Exception as exc:
                fallback = self._proxy_score_answer(
                    question=question,
                    ideal_answer=ideal_answer,
                    retrieved_context=retrieved_context,
                    graph_context=graph_context,
                    candidate_answer=candidate_answer,
                    expected_sources=expected_sources,
                    retrieved_sources=retrieved_sources,
                )
                fallback["summary"] = (
                    "Proxy fallback for standardized RAG metrics because the native "
                    f"ragas backend was unavailable: {exc}"
                )
                return fallback

        return self._proxy_score_answer(
            question=question,
            ideal_answer=ideal_answer,
            retrieved_context=retrieved_context,
            graph_context=graph_context,
            candidate_answer=candidate_answer,
            expected_sources=expected_sources,
            retrieved_sources=retrieved_sources,
        )
