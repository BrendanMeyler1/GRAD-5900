from pathlib import Path
from typing import Dict, Any
from datetime import datetime


class EvaluationHistoryReportGenerator:
    def __init__(self, output_dir: str = "data/reports"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _timestamp(self) -> str:
        return datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    def export_comparison_markdown(
        self,
        run_a_name: str,
        run_b_name: str,
        comparison: Dict[str, Any],
    ) -> str:
        ts = self._timestamp()
        path = self.output_dir / f"run_comparison_{ts}.md"

        lines = []
        lines.append("# Evaluation Run Comparison Report")
        lines.append("")
        lines.append(f"Run A: `{run_a_name}`")
        lines.append(f"Run B: `{run_b_name}`")
        lines.append("")
        lines.append("Positive deltas mean Run B improved over Run A.")
        lines.append("")

        for mode, row in comparison.items():
            lines.append(f"## {mode.upper()}")
            lines.append("")
            lines.append(f"- Combined overall delta: {row['average_combined_overall_delta']:.3f}")
            lines.append(f"- Heuristic overall delta: {row['average_heuristic_overall_delta']:.3f}")
            lines.append(f"- LLM overall delta: {row['average_llm_overall_delta']:.3f}")
            lines.append(f"- Cache hits delta: {row['cache_hits_delta']}")
            lines.append("")

            lines.append("### Heuristic Metric Deltas")
            lines.append("")
            for key, value in row["heuristic_metric_deltas"].items():
                lines.append(f"- {key}: {value:.3f}")
            lines.append("")

            lines.append("### LLM Metric Deltas")
            lines.append("")
            for key, value in row["llm_metric_deltas"].items():
                lines.append(f"- {key}: {value:.3f}")
            lines.append("")

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        return str(path)
