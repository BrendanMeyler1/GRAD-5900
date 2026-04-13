"""
Failure logging store backed by feedback/failures.db.

Used to persist structured failure records and query top failure patterns.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from setup.init_db import init_failures_db


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FailureStore:
    """Structured failure logger for ATS workflow resilience."""

    def __init__(self, db_path: str = "feedback/failures.db") -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        init_failures_db(db_path=db_path)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def log_failure(
        self,
        application_id: str | None,
        ats_type: str,
        company: str,
        failure_step: str,
        error_type: str,
        error_message: str | None = None,
        field_name: str | None = None,
        field_label: str | None = None,
        selector_strategies: list[str] | None = None,
        strategy_that_worked: str | None = None,
        fix_applied: str | None = None,
        timestamp: str | None = None,
    ) -> str:
        """
        Insert a failure record and return failure_id.
        """
        failure_id = str(uuid4())
        ts = timestamp or _utc_now()
        selector_blob = json.dumps(selector_strategies or [])

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO failures (
                    failure_id, application_id, ats_type, company, failure_step,
                    error_type, field_name, field_label, selector_strategies,
                    strategy_that_worked, fix_applied, error_message, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    failure_id,
                    application_id,
                    ats_type,
                    company,
                    failure_step,
                    error_type,
                    field_name,
                    field_label,
                    selector_blob,
                    strategy_that_worked,
                    fix_applied,
                    error_message,
                    ts,
                ),
            )
        return failure_id

    def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """
        Fetch recent failures newest-first.
        """
        limit = max(1, int(limit))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT failure_id, application_id, ats_type, company, failure_step,
                       error_type, field_name, field_label, selector_strategies,
                       strategy_that_worked, fix_applied, error_message, timestamp
                FROM failures
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        result = []
        for row in rows:
            try:
                selector_strategies = json.loads(row[8]) if row[8] else []
            except json.JSONDecodeError:
                selector_strategies = []
            result.append(
                {
                    "failure_id": row[0],
                    "application_id": row[1],
                    "ats_type": row[2],
                    "company": row[3],
                    "failure_step": row[4],
                    "error_type": row[5],
                    "field_name": row[6],
                    "field_label": row[7],
                    "selector_strategies": selector_strategies,
                    "strategy_that_worked": row[9],
                    "fix_applied": row[10],
                    "error_message": row[11],
                    "timestamp": row[12],
                }
            )
        return result

    def top_failure_patterns(self, limit: int = 10) -> list[dict[str, Any]]:
        """
        Aggregate top failure patterns by ATS + error type + step.
        """
        limit = max(1, int(limit))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT ats_type, error_type, failure_step, COUNT(*) AS cnt
                FROM failures
                GROUP BY ats_type, error_type, failure_step
                ORDER BY cnt DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "ats_type": row[0],
                "error_type": row[1],
                "failure_step": row[2],
                "count": row[3],
            }
            for row in rows
        ]
