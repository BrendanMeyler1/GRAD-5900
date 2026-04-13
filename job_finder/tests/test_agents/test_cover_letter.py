"""Tests for Cover Letter agent."""

from unittest.mock import MagicMock

from agents.cover_letter import generate_cover_letter
from errors import LLMParseError


def test_generate_cover_letter_returns_expected_shape(sample_persona, sample_listing):
    mock_router = MagicMock()
    mock_router.route_json.return_value = {
        "cover_letter_text": (
            "Dear Hiring Team,\n\nI am excited to apply for the Senior Backend Engineer role..."
        ),
        "highlights": [
            "Built event-driven systems handling 2M+ events/day",
            "Reduced API latency by 40%",
        ],
        "tone": "enthusiastic",
    }

    result = generate_cover_letter(
        persona=sample_persona,
        listing=sample_listing,
        fit_score={"overall_score": 88},
        router=mock_router,
    )

    assert result["listing_id"] == sample_listing["listing_id"]
    assert result["persona_id"] == sample_persona["persona_id"]
    assert "Senior Backend Engineer" in result["cover_letter_text"]
    assert result["tone"] == "enthusiastic"
    assert len(result["highlights"]) == 2
    assert "cover_letter_id" in result
    assert "generated_at" in result


def test_generate_cover_letter_recovers_non_json_output(sample_persona, sample_listing):
    mock_router = MagicMock()
    mock_router.route_json.side_effect = LLMParseError(
        "failed to parse",
        raw_response=(
            "Here is a draft:\n\n"
            "Dear Hiring Team,\n\n"
            "I am excited to apply for the Senior Backend Engineer role.\n\n"
            "- Built backend systems at scale\n"
            "- Reduced latency through optimization\n"
        ),
    )

    result = generate_cover_letter(
        persona=sample_persona,
        listing=sample_listing,
        fit_score={"overall_score": 88},
        router=mock_router,
    )

    assert result["cover_letter_text"].startswith("Dear Hiring Team")
    assert result["tone"] == "enthusiastic"
    assert "Built backend systems at scale" in result["highlights"]
