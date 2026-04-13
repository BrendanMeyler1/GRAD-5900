"""Tests for Resume Tailor agent."""

from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

from agents.resume_tailor import tailor_resume
from errors import LLMParseError


def test_tailor_resume_returns_structured_payload(sample_persona, sample_listing):
    test_dir = Path(".tmp")
    test_dir.mkdir(parents=True, exist_ok=True)
    bullets_path = test_dir / f"master_bullets_{uuid4().hex}.md"
    bullets_path.write_text("- Built event-driven systems\n- Improved latency by 40%\n")

    mock_router = MagicMock()
    mock_router.route_json.return_value = {
        "resume_text": "# {{FULL_NAME}}\n\n- Built scalable Python APIs.",
        "top_requirements": [
            "5+ years Python or Go",
            "Distributed systems at scale",
        ],
        "evidence_map": [
            {
                "requirement": "Distributed systems at scale",
                "evidence": "Designed services processing 2M+ events/day",
            }
        ],
        "notes": "Prioritized backend architecture and API performance evidence.",
    }

    result = tailor_resume(
        persona=sample_persona,
        listing=sample_listing,
        fit_score={"overall_score": 87},
        router=mock_router,
        master_bullets_path=bullets_path,
    )

    assert result["listing_id"] == sample_listing["listing_id"]
    assert result["persona_id"] == sample_persona["persona_id"]
    assert "{{FULL_NAME}}" in result["resume_text"]
    assert len(result["top_requirements"]) == 2
    assert result["evidence_map"][0]["requirement"] == "Distributed systems at scale"
    assert "resume_id" in result
    assert "generated_at" in result

    kwargs = mock_router.route_json.call_args.kwargs
    assert "Built event-driven systems" in kwargs["user_prompt"]


def test_tailor_resume_recovers_non_json_output(sample_persona, sample_listing):
    mock_router = MagicMock()
    mock_router.route_json.side_effect = LLMParseError(
        "failed to parse",
        raw_response=(
            "Based on the provided information, here's a tailored resume.\n\n"
            "## Tailored Resume Content\n\n"
            "**{{FULL_NAME}}**\n"
            "Senior Backend Engineer\n\n"
            "- Built scalable Python APIs\n"
            "- Improved reliability and latency\n"
        ),
    )

    result = tailor_resume(
        persona=sample_persona,
        listing=sample_listing,
        fit_score={"overall_score": 87},
        router=mock_router,
        master_bullets_path=None,
    )

    assert "{{FULL_NAME}}" in result["resume_text"]
    assert "Built scalable Python APIs" in result["resume_text"]
    assert result["notes"] == "Recovered from non-JSON model response."
