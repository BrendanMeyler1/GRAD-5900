"""
Question Responder agent.

Generates ATS free-text responses and caches reusable answers in
feedback/company_memory.db.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from llm_router.router import LLMRouter

logger = logging.getLogger("job_finder.agents.question_responder")

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "question_responder.md"
DEFAULT_COMPANY_MEMORY_DB = "feedback/company_memory.db"


def _load_prompt() -> str:
    raw = PROMPT_PATH.read_text(encoding="utf-8")
    if "## System Prompt" not in raw:
        return raw.strip()
    start = raw.index("## System Prompt") + len("## System Prompt")
    next_header = raw.find("\n## ", start)
    if next_header == -1:
        return raw[start:].strip()
    return raw[start:next_header].strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(text: str) -> str:
    lowered = text.strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")
    return normalized or "unknown"


def _normalize_question_key(field_id: str | None, question_text: str) -> str:
    """Normalize ATS question to a reusable company-memory key."""
    if field_id:
        field_slug = _slug(field_id)
        if field_slug:
            return field_slug

    question = question_text.lower()
    if "why" in question and "work" in question:
        return "why_work_here"
    if "salary" in question or "compensation" in question:
        return "salary_expectation"
    if "challenge" in question or "difficult" in question:
        return "describe_challenge"
    if "strength" in question:
        return "strengths"
    if "weakness" in question:
        return "weaknesses"
    return _slug(question_text)[:80]


def _ensure_db_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS companies (
            company_id       TEXT PRIMARY KEY,
            company_name     TEXT NOT NULL,
            ats_type         TEXT,
            field_patterns   TEXT,
            last_applied     TEXT,
            last_outcome     TEXT,
            created_at       TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS cached_answers (
            answer_id       TEXT PRIMARY KEY,
            company_id      TEXT NOT NULL,
            question_key    TEXT NOT NULL,
            question_text   TEXT NOT NULL,
            answer_text     TEXT NOT NULL,
            used_count      INTEGER DEFAULT 1,
            last_used       TEXT NOT NULL,
            FOREIGN KEY (company_id) REFERENCES companies(company_id)
        );
        """
    )


def _connect_company_memory(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    _ensure_db_schema(conn)
    return conn


def _company_identity(listing: dict[str, Any]) -> tuple[str, str, str]:
    company_name = (
        listing.get("company", {}).get("name")
        if isinstance(listing.get("company"), dict)
        else None
    )
    company_name = str(company_name or "unknown_company").strip()
    company_id = _slug(company_name)
    ats_type = str(listing.get("ats_type") or "unknown").strip()
    return company_id, company_name, ats_type


def _upsert_company(conn: sqlite3.Connection, company_id: str, company_name: str, ats_type: str) -> None:
    now = _utc_now()
    row = conn.execute(
        "SELECT company_id FROM companies WHERE company_id = ?",
        (company_id,),
    ).fetchone()
    if row:
        conn.execute(
            """
            UPDATE companies
            SET company_name = ?, ats_type = ?, last_applied = ?
            WHERE company_id = ?
            """,
            (company_name, ats_type, now, company_id),
        )
    else:
        conn.execute(
            """
            INSERT INTO companies (
                company_id, company_name, ats_type, field_patterns, last_applied, last_outcome, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (company_id, company_name, ats_type, "{}", now, None, now),
        )


def _get_cached_answer(
    conn: sqlite3.Connection,
    company_id: str,
    question_key: str,
) -> tuple[str, str] | None:
    row = conn.execute(
        """
        SELECT answer_id, answer_text
        FROM cached_answers
        WHERE company_id = ? AND question_key = ?
        ORDER BY last_used DESC
        LIMIT 1
        """,
        (company_id, question_key),
    ).fetchone()
    if not row:
        return None
    return str(row[0]), str(row[1])


def _touch_cached_answer(conn: sqlite3.Connection, answer_id: str) -> None:
    conn.execute(
        """
        UPDATE cached_answers
        SET used_count = used_count + 1, last_used = ?
        WHERE answer_id = ?
        """,
        (_utc_now(), answer_id),
    )


def _store_cached_answer(
    conn: sqlite3.Connection,
    company_id: str,
    question_key: str,
    question_text: str,
    response_text: str,
) -> None:
    conn.execute(
        """
        INSERT INTO cached_answers (
            answer_id, company_id, question_key, question_text, answer_text, used_count, last_used
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid4()),
            company_id,
            question_key,
            question_text,
            response_text,
            1,
            _utc_now(),
        ),
    )


def _normalize_grounding(raw: Any) -> list[str]:
    grounded_in: list[str] = []
    for item in raw or []:
        text = str(item).strip()
        if text:
            grounded_in.append(text)
    return grounded_in


def generate_question_response(
    listing: dict[str, Any],
    field_id: str,
    question_text: str,
    persona: dict[str, Any],
    fit_score: dict[str, Any] | None = None,
    router: LLMRouter | None = None,
    company_memory_db_path: str = DEFAULT_COMPANY_MEMORY_DB,
    allow_cache: bool = True,
) -> dict[str, Any]:
    """
    Generate and cache ATS free-text question responses.

    Output matches B.5 Question Response schema from the implementation plan.
    """
    question_key = _normalize_question_key(field_id, question_text)
    company_id, company_name, ats_type = _company_identity(listing)

    with _connect_company_memory(company_memory_db_path) as conn:
        _upsert_company(conn, company_id=company_id, company_name=company_name, ats_type=ats_type)

        if allow_cache:
            cached = _get_cached_answer(conn, company_id=company_id, question_key=question_key)
            if cached:
                answer_id, answer_text = cached
                _touch_cached_answer(conn, answer_id)
                conn.commit()
                return {
                    "question_id": str(uuid4()),
                    "listing_id": listing.get("listing_id"),
                    "field_id": field_id,
                    "question_text": question_text,
                    "response_text": answer_text,
                    "grounded_in": ["company_memory.cached_answers"],
                    "cached_from_company_memory": True,
                    "generated_at": _utc_now(),
                }

        if router is None:
            router = LLMRouter()

        system_prompt = _load_prompt()
        payload = {
            "persona": persona,
            "listing": listing,
            "fit_score": fit_score or {},
            "field_id": field_id,
            "question_text": question_text,
        }
        response = router.route_json(
            task_type="question_responding",
            system_prompt=system_prompt,
            user_prompt=json.dumps(payload, indent=2),
        )

        response_text = str(response.get("response_text", "")).strip()
        grounded_in = _normalize_grounding(response.get("grounded_in"))
        _store_cached_answer(
            conn,
            company_id=company_id,
            question_key=question_key,
            question_text=question_text,
            response_text=response_text,
        )
        conn.commit()

    return {
        "question_id": str(uuid4()),
        "listing_id": listing.get("listing_id"),
        "field_id": field_id,
        "question_text": question_text,
        "response_text": response_text,
        "grounded_in": grounded_in,
        "cached_from_company_memory": False,
        "generated_at": _utc_now(),
    }
