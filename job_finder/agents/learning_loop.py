"""
Learning Loop Agent.

Scans the failures.db for the most frequent blocking errors within an ATS and utilizes 
the LLMRouter to deduce semantic fixes or static fallback responses, placing them 
directly into the company_memory.db so future runs succeed seamlessly.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Any

from llm_router.router import LLMRouter
from feedback.company_memory_store import CompanyMemoryStore
from feedback.failures_store import FailureStore

logger = logging.getLogger("job_finder.agents.learning_loop")

PROMPT = """
You are the Learning Loop Agent for an autonomous job application system.
Your job is to analyze form-fill and submission failures in ATS platforms and generate
a generic, robust fallback response or mitigation strategy to prevent future failure.

Here is the aggregated failure data:
{{FAILURE_DATA}}

Please output a JSON payload containing exactly:
- `mitigation_type`: "fallback_answer" | "selector_remap" | "skip_instruction"
- `cache_key`: The normalized identifier of the problematic field (e.g., "salary_expectation", "race_veteran_status")
- `fallback_response`: The desired static answer we should input in the future when this field is encountered, if applicable.
- `explanation`: Brief summary of why this mitigation resolves the cluster of failures.
"""

class LearningLoopAgent:
    def __init__(
        self,
        router: LLMRouter | None = None,
        failures_db: str = "feedback/failures.db",
        memory_db: str = "feedback/company_memory.db",
    ):
        self.router = router or LLMRouter()
        self.failure_store = FailureStore(db_path=failures_db)
        self.memory_store = CompanyMemoryStore(db_path=memory_db)

    def _utc_now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def run_mitigation_cycle(self, hours_lookback: int = 24) -> dict[str, Any]:
        """Fetch recent failures, cluster them, and inject fallbacks into memory."""
        since_dt = datetime.now(timezone.utc) - timedelta(hours=max(1, hours_lookback))
        since_iso = since_dt.isoformat()

        # Connect directly to failures DB to extract grouped failures
        with sqlite3.connect(self.failure_store.db_path) as conn:
            rows = conn.execute(
                """
                SELECT failure_step, error_type, COUNT(*) as fail_count, MAX(error_message)
                FROM failures
                WHERE timestamp >= ?
                GROUP BY failure_step, error_type
                ORDER BY fail_count DESC
                """,
                (since_iso,)
            ).fetchall()

        if not rows:
            logger.info("Learning loop cycle detected no recent failures.")
            return {"status": "success", "mitigations_applied": 0, "clusters_analyzed": 0}

        applied = 0
        for row in rows:
            failure_step, error_type, count, sample_msg = row

            if count < 2:
                # We only want to mitigate systemic patterns, not one-offs
                continue

            payload_data = json.dumps({
                "failure_step": failure_step,
                "error_type": error_type,
                "occurrences": count,
                "sample_message": sample_msg
            }, indent=2)

            try:
                response = self.router.route_json(
                    task_type="learning_mitigation",
                    system_prompt="You apply systematic mitigations to web forms.",
                    user_prompt=PROMPT.replace("{{FAILURE_DATA}}", payload_data)
                )

                mit_type = response.get("mitigation_type")
                cache_key = response.get("cache_key")
                fallback = response.get("fallback_response")

                if mit_type == "fallback_answer" and cache_key and fallback:
                    company_id = "universal_fallback" # High-level fallback across all ATS
                    
                    with sqlite3.connect(self.memory_store.db_path) as mem_conn:
                        mem_conn.execute(
                            """
                            INSERT OR REPLACE INTO cached_answers (
                                answer_id, company_id, question_key, question_text, answer_text, used_count, last_used
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                f"mitigation_{cache_key}_{int(datetime.now().timestamp())}",
                                company_id,
                                cache_key,
                                "Mitigated via Learning Loop",
                                fallback,
                                1,
                                self._utc_now()
                            )
                        )
                        mem_conn.commit()
                    
                    applied += 1
                    logger.info(f"Applied mitigation for {cache_key}: {fallback}")

            except Exception as e:
                logger.error(f"Failed to generate mitigation for cluster {error_type}: {e}")

        return {
            "status": "success",
            "mitigations_applied": applied,
            "clusters_analyzed": len(rows)
        }
