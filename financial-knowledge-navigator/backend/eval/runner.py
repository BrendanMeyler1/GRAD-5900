import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any

from backend.eval.metrics import (
    token_overlap_score,
    source_overlap_score,
    entity_coverage_score,
    relationship_coverage_score,
    answer_length_score,
    average_scores,
)


class EvaluationRunner:
    def __init__(
        self,
        query_pipeline,
        llm_judge,
    ):
        self.query_pipeline = query_pipeline
        self.llm_judge = llm_judge

    def _run_single_mode(self, item: Dict, mode: str, indexed_docs: List[str], top_k: int = 5) -> Dict[str, Any]:
        question = item["question"]
        ideal_answer = item.get("ideal_answer", "")
        expected_sources = item.get("sources", [])
        expected_entities = item.get("key_entities", [])
        expected_relationships = item.get("expected_relationships", [])

        pipeline_result = self.query_pipeline.run(
            query=question,
            mode=mode,
            indexed_docs=indexed_docs,
            top_k=top_k,
            use_cache=True,
        )

        retrieved = pipeline_result["selected_results"]
        retrieved_sources = [r.get("source", "") for r in retrieved]
        retrieved_context_text = pipeline_result["retrieved_context_text"]
        graph_context_text = pipeline_result["graph_context_text"]
        baseline_answer = pipeline_result["preliminary_answer"]
        refined_answer = pipeline_result["refined_answer"]

        final_answer = refined_answer if mode == "graphrag" else baseline_answer
        graph_context_for_mode = graph_context_text if mode == "graphrag" else ""

        heuristic_scores = {
            "token_overlap": token_overlap_score(ideal_answer, final_answer),
            "source_overlap": source_overlap_score(expected_sources, retrieved_sources),
            "entity_coverage": entity_coverage_score(
                expected_entities,
                final_answer,
                graph_context_for_mode,
            ),
            "relationship_coverage": relationship_coverage_score(
                expected_relationships,
                graph_context_for_mode,
            ),
            "answer_length": answer_length_score(final_answer),
        }

        heuristic_overall = average_scores(heuristic_scores)

        judge_result = self.llm_judge.judge_answer(
            question=question,
            ideal_answer=ideal_answer,
            retrieved_context=retrieved_context_text,
            graph_context=graph_context_text if mode == "graphrag" else "",
            candidate_answer=final_answer,
            mode=mode,
            use_cache=True,
        )

        llm_scores = judge_result["scores"]
        llm_overall_normalized = llm_scores["overall"] / 5.0
        combined_overall = (heuristic_overall + llm_overall_normalized) / 2.0

        return {
            "question_id": item.get("id"),
            "question": question,
            "mode": mode,
            "cache_hit": pipeline_result.get("cache_hit", False),
            "retrieved_sources": retrieved_sources,
            "retrieved_context_text": retrieved_context_text,
            "baseline_answer": baseline_answer,
            "graph_context_text": graph_context_text,
            "final_answer": final_answer,
            "heuristic_scores": heuristic_scores,
            "heuristic_overall": heuristic_overall,
            "llm_judge": judge_result,
            "combined_overall": combined_overall,
        }

    def run_dataset(
        self,
        dataset: List[Dict],
        indexed_docs: List[str],
        modes: List[str] = None,
        top_k: int = 5,
    ) -> Dict[str, Any]:
        if modes is None:
            modes = ["vector", "hybrid", "graphrag"]

        results_by_mode = {mode: [] for mode in modes}

        for item in dataset:
            for mode in modes:
                result = self._run_single_mode(
                    item,
                    mode=mode,
                    indexed_docs=indexed_docs,
                    top_k=top_k,
                )
                results_by_mode[mode].append(result)

        summary = {}
        for mode, results in results_by_mode.items():
            if not results:
                summary[mode] = {"average_combined_overall": 0.0, "num_questions": 0}
                continue

            avg_combined = sum(r["combined_overall"] for r in results) / len(results)
            avg_heuristic = sum(r["heuristic_overall"] for r in results) / len(results)
            avg_llm_overall = sum(r["llm_judge"]["scores"]["overall"] for r in results) / len(results)
            cache_hits = sum(1 for r in results if r.get("cache_hit"))

            avg_heuristic_metrics = {}
            for metric_name in results[0]["heuristic_scores"].keys():
                avg_heuristic_metrics[metric_name] = sum(
                    r["heuristic_scores"][metric_name] for r in results
                ) / len(results)

            avg_llm_metrics = {}
            for metric_name in results[0]["llm_judge"]["scores"].keys():
                avg_llm_metrics[metric_name] = sum(
                    r["llm_judge"]["scores"][metric_name] for r in results
                ) / len(results)

            summary[mode] = {
                "average_combined_overall": avg_combined,
                "average_heuristic_overall": avg_heuristic,
                "average_llm_overall_0_to_5": avg_llm_overall,
                "average_heuristic_metrics": avg_heuristic_metrics,
                "average_llm_metrics": avg_llm_metrics,
                "num_questions": len(results),
                "cache_hits": cache_hits,
            }

        return {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "summary": summary,
            "results_by_mode": results_by_mode,
        }

    @staticmethod
    def save_results(results: Dict[str, Any], output_dir: str = "data/eval_results") -> str:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        file_path = output_path / f"evaluation_{timestamp}.json"

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

        return str(file_path)
