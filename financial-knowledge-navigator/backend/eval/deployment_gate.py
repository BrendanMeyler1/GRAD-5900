from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.core.config import settings


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DeploymentGateEvaluator:
    def __init__(
        self,
        min_combined_overall: float = settings.deploy_min_combined_overall,
        min_ragas_overall: float = settings.deploy_min_ragas_overall,
        min_online_runs: int = settings.deploy_min_online_runs,
        min_feedback_count: int = settings.deploy_min_feedback_count,
        min_positive_rate: float = settings.deploy_min_positive_rate,
        max_avg_latency_ms: float = settings.deploy_max_avg_latency_ms,
    ):
        self.min_combined_overall = min_combined_overall
        self.min_ragas_overall = min_ragas_overall
        self.min_online_runs = min_online_runs
        self.min_feedback_count = min_feedback_count
        self.min_positive_rate = min_positive_rate
        self.max_avg_latency_ms = max_avg_latency_ms

    def thresholds(self) -> Dict[str, float]:
        return {
            "min_combined_overall": self.min_combined_overall,
            "min_ragas_overall": self.min_ragas_overall,
            "min_online_runs": self.min_online_runs,
            "min_feedback_count": self.min_feedback_count,
            "min_positive_rate": self.min_positive_rate,
            "max_avg_latency_ms": self.max_avg_latency_ms,
        }

    def _check(self, name: str, actual: float, threshold: float, comparator: str) -> Dict[str, Any]:
        if comparator == ">=":
            passed = actual >= threshold
        elif comparator == "<=":
            passed = actual <= threshold
        else:
            raise ValueError(f"Unsupported comparator: {comparator}")
        return {
            "name": name,
            "actual": actual,
            "threshold": threshold,
            "comparator": comparator,
            "passed": passed,
        }

    def evaluate(
        self,
        offline_eval_results: Optional[Dict[str, Any]],
        online_summary: Optional[Dict[str, Any]],
        online_mode_rows: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        offline_summary = (offline_eval_results or {}).get("summary", {})
        online_summary = online_summary or {}
        online_mode_rows = online_mode_rows or []
        online_by_mode = {row.get("mode"): row for row in online_mode_rows}

        mode_results: Dict[str, Any] = {}
        deployable_modes: List[Dict[str, Any]] = []

        for mode, mode_summary in offline_summary.items():
            online_row = online_by_mode.get(
                mode,
                {
                    "mode": mode,
                    "num_runs": 0,
                    "avg_latency_ms": 0.0,
                    "feedback_count": 0,
                    "positive_rate": 0.0,
                },
            )

            checks = [
                self._check(
                    "Offline combined overall",
                    float(mode_summary.get("average_combined_overall", 0.0) or 0.0),
                    self.min_combined_overall,
                    ">=",
                ),
                self._check(
                    "Offline standardized RAG overall",
                    float(mode_summary.get("average_ragas_overall", 0.0) or 0.0),
                    self.min_ragas_overall,
                    ">=",
                ),
                self._check(
                    "Online run count",
                    float(online_row.get("num_runs", 0) or 0),
                    float(self.min_online_runs),
                    ">=",
                ),
                self._check(
                    "Online feedback count",
                    float(online_row.get("feedback_count", 0) or 0),
                    float(self.min_feedback_count),
                    ">=",
                ),
                self._check(
                    "Online positive feedback rate",
                    float(online_row.get("positive_rate", 0.0) or 0.0),
                    self.min_positive_rate,
                    ">=",
                ),
                self._check(
                    "Online average latency",
                    float(online_row.get("avg_latency_ms", 0.0) or 0.0),
                    self.max_avg_latency_ms,
                    "<=",
                ),
            ]
            ready = all(check["passed"] for check in checks)
            mode_result = {
                "mode": mode,
                "ready": ready,
                "offline_summary": mode_summary,
                "online_summary": online_row,
                "checks": checks,
            }
            mode_results[mode] = mode_result
            if ready:
                deployable_modes.append(mode_result)

        best_candidate_mode = None
        if deployable_modes:
            best_candidate_mode = max(
                deployable_modes,
                key=lambda item: item["offline_summary"].get("average_combined_overall", 0.0),
            )["mode"]

        blockers: List[str] = []
        if not offline_summary:
            blockers.append("Run the offline golden-dataset evaluation first.")
        elif not deployable_modes:
            blockers.append("No retrieval mode currently satisfies the deployment gate thresholds.")

        return {
            "generated_at": _utc_now(),
            "thresholds": self.thresholds(),
            "overall_ready": bool(deployable_modes),
            "best_candidate_mode": best_candidate_mode,
            "mode_results": mode_results,
            "deployable_modes": [item["mode"] for item in deployable_modes],
            "blockers": blockers,
            "online_summary": online_summary,
        }
