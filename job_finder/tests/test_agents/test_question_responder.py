"""Tests for Question Responder agent."""

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

from agents.question_responder import generate_question_response


def test_generate_question_response_creates_and_uses_cache(sample_persona, sample_listing):
    test_dir = Path(".tmp")
    test_dir.mkdir(parents=True, exist_ok=True)
    db_path = test_dir / f"company_memory_{uuid4().hex}.db"

    question_text = "Why do you want to work at Acme Test Corp?"
    field_id = "why_work_here"

    first_router = MagicMock()
    first_router.route_json.return_value = {
        "response_text": (
            "Your platform focus on real-time financial processing aligns with my "
            "experience building event-driven systems and high-performance APIs."
        ),
        "grounded_in": [
            "persona.experience[0].bullets[0]",
            "fit_score.talking_points[0]",
        ],
        "confidence": 0.86,
    }

    first = generate_question_response(
        listing=sample_listing,
        field_id=field_id,
        question_text=question_text,
        persona=sample_persona,
        fit_score={"talking_points": ["Strong backend relevance"]},
        router=first_router,
        company_memory_db_path=str(db_path),
    )

    assert first["cached_from_company_memory"] is False
    assert "real-time financial processing" in first["response_text"]
    assert len(first["grounded_in"]) == 2

    second_router = MagicMock()
    second = generate_question_response(
        listing=sample_listing,
        field_id=field_id,
        question_text=question_text,
        persona=sample_persona,
        fit_score={"talking_points": ["Strong backend relevance"]},
        router=second_router,
        company_memory_db_path=str(db_path),
    )

    assert second["cached_from_company_memory"] is True
    assert second["response_text"] == first["response_text"]
    second_router.route_json.assert_not_called()

    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT used_count FROM cached_answers WHERE question_key = ?",
            (field_id,),
        ).fetchone()
    assert row is not None
    assert row[0] == 2
