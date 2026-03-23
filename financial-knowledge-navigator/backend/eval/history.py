import json
from pathlib import Path
from typing import Dict, List, Any, Optional


class EvaluationHistoryManager:
    def __init__(self, eval_dir: str = "data/eval_results"):
        self.eval_dir = Path(eval_dir)
        self.eval_dir.mkdir(parents=True, exist_ok=True)

    def list_runs(self) -> List[Dict[str, Any]]:
        runs = []

        for path in sorted(self.eval_dir.glob("evaluation_*.json"), reverse=True):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    payload = json.load(f)

                summary = payload.get("summary", {})
                runs.append(
                    {
                        "file_name": path.name,
                        "file_path": str(path),
                        "timestamp": payload.get("timestamp", ""),
                        "modes": list(summary.keys()),
                        "summary": summary,
                    }
                )
            except Exception:
                continue

        return runs

    def load_run(self, file_name: str) -> Optional[Dict[str, Any]]:
        path = self.eval_dir / file_name
        if not path.exists():
            return None

        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def get_best_mode(self, run_data: Dict[str, Any]) -> Dict[str, Any]:
        summary = run_data.get("summary", {})
        if not summary:
            return {"mode": None, "score": None}

        best_mode = None
        best_score = -1.0

        for mode, mode_summary in summary.items():
            score = mode_summary.get("average_combined_overall", 0.0)
            if score > best_score:
                best_mode = mode
                best_score = score

        return {"mode": best_mode, "score": best_score}

    def compare_runs(self, run_a: Dict[str, Any], run_b: Dict[str, Any]) -> Dict[str, Any]:
        """
        Compare run_b against run_a.
        Positive deltas mean run_b improved over run_a.
        """
        summary_a = run_a.get("summary", {})
        summary_b = run_b.get("summary", {})

        all_modes = sorted(set(summary_a.keys()) | set(summary_b.keys()))
        comparison = {}

        for mode in all_modes:
            mode_a = summary_a.get(mode, {})
            mode_b = summary_b.get(mode, {})

            row = {
                "average_combined_overall_delta": (
                    mode_b.get("average_combined_overall", 0.0)
                    - mode_a.get("average_combined_overall", 0.0)
                ),
                "average_heuristic_overall_delta": (
                    mode_b.get("average_heuristic_overall", 0.0)
                    - mode_a.get("average_heuristic_overall", 0.0)
                ),
                "average_llm_overall_delta": (
                    mode_b.get("average_llm_overall_0_to_5", 0.0)
                    - mode_a.get("average_llm_overall_0_to_5", 0.0)
                ),
                "cache_hits_delta": (
                    mode_b.get("cache_hits", 0)
                    - mode_a.get("cache_hits", 0)
                ),
                "heuristic_metric_deltas": {},
                "llm_metric_deltas": {},
            }

            heur_a = mode_a.get("average_heuristic_metrics", {})
            heur_b = mode_b.get("average_heuristic_metrics", {})
            heur_keys = sorted(set(heur_a.keys()) | set(heur_b.keys()))

            for key in heur_keys:
                row["heuristic_metric_deltas"][key] = heur_b.get(key, 0.0) - heur_a.get(key, 0.0)

            llm_a = mode_a.get("average_llm_metrics", {})
            llm_b = mode_b.get("average_llm_metrics", {})
            llm_keys = sorted(set(llm_a.keys()) | set(llm_b.keys()))

            for key in llm_keys:
                row["llm_metric_deltas"][key] = llm_b.get(key, 0.0) - llm_a.get(key, 0.0)

            comparison[mode] = row

        return comparison
