"""
Company memory store backed by feedback/company_memory.db.

Provides reusable answer caching and replay-reference tracking per company.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from setup.init_db import init_company_memory_db


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in (value or "").strip())
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_") or "unknown_company"


class CompanyMemoryStore:
    """Persistent company-level memory for faster repeat applications."""

    def __init__(self, db_path: str = "feedback/company_memory.db") -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        init_company_memory_db(db_path=db_path)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def upsert_company(
        self,
        company_name: str,
        ats_type: str | None = None,
        field_patterns: str | None = None,
    ) -> str:
        """
        Create or update company row and return company_id.
        """
        company_id = _slug(company_name)
        now = _utc_now()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT company_id FROM companies WHERE company_id = ?",
                (company_id,),
            ).fetchone()
            if row:
                conn.execute(
                    """
                    UPDATE companies
                    SET company_name = ?, ats_type = COALESCE(?, ats_type),
                        field_patterns = COALESCE(?, field_patterns), last_applied = ?
                    WHERE company_id = ?
                    """,
                    (company_name, ats_type, field_patterns, now, company_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO companies (
                        company_id, company_name, ats_type, field_patterns,
                        last_applied, last_outcome, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (company_id, company_name, ats_type, field_patterns, now, None, now),
                )
        return company_id

    def cache_answer(
        self,
        company_name: str,
        question_key: str,
        question_text: str,
        answer_text: str,
        ats_type: str | None = None,
    ) -> str:
        """
        Cache an answer snippet for a company + normalized question key.
        If one exists, increment usage and replace text with latest.
        """
        company_id = self.upsert_company(company_name=company_name, ats_type=ats_type)
        now = _utc_now()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT answer_id FROM cached_answers
                WHERE company_id = ? AND question_key = ?
                ORDER BY last_used DESC
                LIMIT 1
                """,
                (company_id, question_key),
            ).fetchone()
            if row:
                answer_id = row[0]
                conn.execute(
                    """
                    UPDATE cached_answers
                    SET question_text = ?, answer_text = ?,
                        used_count = used_count + 1, last_used = ?
                    WHERE answer_id = ?
                    """,
                    (question_text, answer_text, now, answer_id),
                )
                return answer_id

            answer_id = str(uuid4())
            conn.execute(
                """
                INSERT INTO cached_answers (
                    answer_id, company_id, question_key, question_text,
                    answer_text, used_count, last_used
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (answer_id, company_id, question_key, question_text, answer_text, 1, now),
            )
            return answer_id

    def get_cached_answer(
        self,
        company_name: str,
        question_key: str,
    ) -> dict[str, Any] | None:
        """
        Return latest cached answer for company+question key.
        """
        company_id = _slug(company_name)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT answer_id, question_text, answer_text, used_count, last_used
                FROM cached_answers
                WHERE company_id = ? AND question_key = ?
                ORDER BY last_used DESC
                LIMIT 1
                """,
                (company_id, question_key),
            ).fetchone()
            if not row:
                return None
            conn.execute(
                """
                UPDATE cached_answers
                SET used_count = used_count + 1, last_used = ?
                WHERE answer_id = ?
                """,
                (_utc_now(), row[0]),
            )
        return {
            "answer_id": row[0],
            "question_key": question_key,
            "question_text": row[1],
            "answer_text": row[2],
            "used_count": row[3] + 1,
            "last_used": _utc_now(),
        }

    def add_replay_ref(self, company_name: str, trace_id: str, ats_type: str | None = None) -> None:
        """
        Store replay trace reference for a company.
        """
        company_id = self.upsert_company(company_name=company_name, ats_type=ats_type)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO replay_refs (company_id, trace_id) VALUES (?, ?)",
                (company_id, trace_id),
            )

    def get_replay_refs(self, company_name: str) -> list[str]:
        """
        Return all replay trace IDs for a company.
        """
        company_id = _slug(company_name)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT trace_id FROM replay_refs WHERE company_id = ?",
                (company_id,),
            ).fetchall()
        return [str(row[0]) for row in rows]
