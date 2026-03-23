import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List


class EvaluationReportGenerator:
    def __init__(self, output_dir: str = "data/reports"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _timestamp(self) -> str:
        return datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    def _top_failure_cases(self, results_by_mode: Dict[str, List[Dict[str, Any]]], top_n: int = 3):
        failures = []
        for mode, results in results_by_mode.items():
            for result in results:
                failures.append(
                    {
                        "mode": mode,
                        "question_id": result.get("question_id"),
                        "question": result.get("question"),
                        "combined_overall": result.get("combined_overall", 0.0),
                        "llm_overall": result.get("llm_judge", {}).get("scores", {}).get("overall", 0),
                        "heuristic_overall": result.get("heuristic_overall", 0.0),
                        "summary": result.get("llm_judge", {}).get("summary", ""),
                    }
                )

        failures.sort(key=lambda x: x["combined_overall"])
        return failures[:top_n]

    def export_json_summary(self, eval_results: Dict[str, Any]) -> str:
        ts = self._timestamp()
        path = self.output_dir / f"evaluation_summary_{ts}.json"

        compact = {
            "timestamp": eval_results.get("timestamp"),
            "summary": eval_results.get("summary", {}),
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(compact, f, indent=2, ensure_ascii=False)

        return str(path)

    def export_csv_details(self, eval_results: Dict[str, Any]) -> str:
        ts = self._timestamp()
        path = self.output_dir / f"evaluation_details_{ts}.csv"

        fieldnames = [
            "mode",
            "question_id",
            "question",
            "combined_overall",
            "heuristic_overall",
            "llm_overall",
            "cache_hit",
            "retrieved_sources",
            "token_overlap",
            "source_overlap",
            "entity_coverage",
            "relationship_coverage",
            "answer_length",
            "judge_summary",
        ]

        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for mode, results in eval_results.get("results_by_mode", {}).items():
                for result in results:
                    heuristic_scores = result.get("heuristic_scores", {})
                    llm_scores = result.get("llm_judge", {}).get("scores", {})

                    writer.writerow(
                        {
                            "mode": mode,
                            "question_id": result.get("question_id"),
                            "question": result.get("question"),
                            "combined_overall": result.get("combined_overall"),
                            "heuristic_overall": result.get("heuristic_overall"),
                            "llm_overall": llm_scores.get("overall"),
                            "cache_hit": result.get("cache_hit"),
                            "retrieved_sources": " | ".join(result.get("retrieved_sources", [])),
                            "token_overlap": heuristic_scores.get("token_overlap"),
                            "source_overlap": heuristic_scores.get("source_overlap"),
                            "entity_coverage": heuristic_scores.get("entity_coverage"),
                            "relationship_coverage": heuristic_scores.get("relationship_coverage"),
                            "answer_length": heuristic_scores.get("answer_length"),
                            "judge_summary": result.get("llm_judge", {}).get("summary", ""),
                        }
                    )

        return str(path)

    def export_markdown_report(self, eval_results: Dict[str, Any]) -> str:
        ts = self._timestamp()
        path = self.output_dir / f"evaluation_report_{ts}.md"

        summary = eval_results.get("summary", {})
        results_by_mode = eval_results.get("results_by_mode", {})
        failures = self._top_failure_cases(results_by_mode, top_n=3)

        lines = []
        lines.append("# Financial Knowledge Navigator — Evaluation Report")
        lines.append("")
        lines.append(f"Generated: {eval_results.get('timestamp', '')}")
        lines.append("")
        lines.append("## Overview")
        lines.append("")
        lines.append("This report summarizes evaluation results across retrieval and reasoning modes.")
        lines.append("Modes compared:")
        lines.append("- Vector")
        lines.append("- Hybrid")
        lines.append("- GraphRAG")
        lines.append("")

        lines.append("## Summary by Mode")
        lines.append("")
        for mode, mode_summary in summary.items():
            lines.append(f"### {mode.upper()}")
            lines.append("")
            lines.append(f"- Average combined overall: {mode_summary.get('average_combined_overall', 0):.3f}")
            lines.append(f"- Average heuristic overall: {mode_summary.get('average_heuristic_overall', 0):.3f}")
            lines.append(f"- Average LLM overall (0-5): {mode_summary.get('average_llm_overall_0_to_5', 0):.3f}")
            lines.append(f"- Number of questions: {mode_summary.get('num_questions', 0)}")
            lines.append(f"- Pipeline cache hits: {mode_summary.get('cache_hits', 0)}")
            lines.append("")

            lines.append("#### Heuristic Metrics")
            lines.append("")
            for metric_name, value in mode_summary.get("average_heuristic_metrics", {}).items():
                lines.append(f"- {metric_name}: {value:.3f}")
            lines.append("")

            lines.append("#### LLM Judge Metrics")
            lines.append("")
            for metric_name, value in mode_summary.get("average_llm_metrics", {}).items():
                lines.append(f"- {metric_name}: {value:.3f}")
            lines.append("")

        lines.append("## Top Failure Cases")
        lines.append("")
        if failures:
            for failure in failures:
                lines.append(f"### {failure['mode'].upper()} — {failure['question_id']}")
                lines.append("")
                lines.append(f"**Question:** {failure['question']}")
                lines.append("")
                lines.append(f"- Combined overall: {failure['combined_overall']:.3f}")
                lines.append(f"- Heuristic overall: {failure['heuristic_overall']:.3f}")
                lines.append(f"- LLM overall: {failure['llm_overall']}")
                lines.append(f"- Judge summary: {failure['summary']}")
                lines.append("")
        else:
            lines.append("No failure cases found.")
            lines.append("")

        lines.append("## Per-Mode Notes")
        lines.append("")
        for mode, results in results_by_mode.items():
            lines.append(f"### {mode.upper()}")
            lines.append("")
            for result in results:
                lines.append(f"#### {result.get('question_id')} — {result.get('question')}")
                lines.append("")
                lines.append(f"- Combined overall: {result.get('combined_overall', 0):.3f}")
                lines.append(f"- Heuristic overall: {result.get('heuristic_overall', 0):.3f}")
                lines.append(f"- LLM overall: {result.get('llm_judge', {}).get('scores', {}).get('overall', 0)}")
                lines.append(f"- Cache hit: {result.get('cache_hit', False)}")
                lines.append(f"- Judge summary: {result.get('llm_judge', {}).get('summary', '')}")
                lines.append("")

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        return str(path)

    def export_all(self, eval_results: Dict[str, Any]) -> Dict[str, str]:
        return {
            "markdown_report": self.export_markdown_report(eval_results),
            "csv_details": self.export_csv_details(eval_results),
            "json_summary": self.export_json_summary(eval_results),
        }
