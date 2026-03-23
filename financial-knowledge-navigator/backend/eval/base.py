from abc import ABC, abstractmethod
from typing import Any, Dict, List


class AnswerJudge(ABC):
    @abstractmethod
    def judge_answer(
        self,
        question: str,
        ideal_answer: str,
        retrieved_context: str,
        graph_context: str,
        candidate_answer: str,
        mode: str,
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        """Return structured scores and rationales for a generated answer."""


class DatasetEvaluator(ABC):
    @abstractmethod
    def run_dataset(
        self,
        dataset: List[Dict],
        indexed_docs: List[str],
        modes: List[str] = None,
        top_k: int = 5,
    ) -> Dict[str, Any]:
        """Run a benchmark dataset across one or more retrieval modes."""

    @staticmethod
    @abstractmethod
    def save_results(results: Dict[str, Any], output_dir: str = "data/eval_results") -> str:
        """Persist evaluation output and return the saved path."""
