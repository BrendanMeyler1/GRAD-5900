"""Tests for Fit Scorer agent."""

from unittest.mock import MagicMock

from agents.fit_scorer import score_fit
from errors import LLMParseError


def test_score_fit_returns_normalized_schema(sample_persona, sample_listing):
    mock_router = MagicMock()
    mock_router.route_json.return_value = {
        "overall_score": 84,
        "breakdown": {
            "skills_match": 90,
            "experience_level": 82,
            "domain_relevance": 86,
            "culture_signals": 70,
            "location_match": 100,
        },
        "gaps": [
            {
                "requirement": "Kafka experience",
                "severity": "minor",
                "mitigation": "Strong event-driven architecture background",
            }
        ],
        "strengths": [
            {
                "requirement": "Distributed systems",
                "evidence": "Built high-throughput APIs and async services",
            }
        ],
        "talking_points": ["Strong backend architecture experience"],
        "recommendation": "APPLY",
    }

    result = score_fit(
        persona=sample_persona,
        listing=sample_listing,
        router=mock_router,
    )

    assert result["overall_score"] == 84
    assert result["recommendation"] == "APPLY"
    assert result["listing_id"] == sample_listing["listing_id"]
    assert result["persona_id"] == sample_persona["persona_id"]
    assert "fit_id" in result
    assert "scored_at" in result
    assert result["breakdown"]["skills_match"] == 90
    assert result["gaps"][0]["severity"] == "minor"


def test_score_fit_derives_recommendation_when_invalid(sample_persona, sample_listing):
    mock_router = MagicMock()
    mock_router.route_json.return_value = {
        "overall_score": 44,
        "breakdown": {
            "skills_match": 40,
            "experience_level": 45,
            "domain_relevance": 42,
            "culture_signals": 50,
            "location_match": 60,
        },
        "gaps": [],
        "strengths": [],
        "talking_points": [],
        "recommendation": "UNKNOWN",
    }

    result = score_fit(
        persona=sample_persona,
        listing=sample_listing,
        router=mock_router,
    )

    assert result["overall_score"] == 44
    assert result["recommendation"] == "SKIP"


def test_score_fit_recovers_from_non_json_response(sample_persona, sample_listing):
    mock_router = MagicMock()
    mock_router.route_json.side_effect = LLMParseError(
        "failed to parse",
        raw_response=(
            "## Fit Analysis: Senior Backend Engineer at Acme Test Corp\n\n"
            "**Overall Fit Score: 85/100**\n\n"
            "**Where this archetype thrives:**\n"
            "- Platform teams\n"
            "- High-throughput backend systems\n"
        ),
    )

    result = score_fit(
        persona=sample_persona,
        listing=sample_listing,
        router=mock_router,
    )

    assert result["overall_score"] == 85
    assert result["recommendation"] == "APPLY"
    assert result["breakdown"]["skills_match"] == 85
    assert "Platform teams" in result["talking_points"]


def test_score_fit_recovers_when_only_recommendation_present(sample_persona, sample_listing):
    mock_router = MagicMock()
    mock_router.route_json.side_effect = LLMParseError(
        "failed to parse",
        raw_response="Final recommendation: MAYBE",
    )

    result = score_fit(
        persona=sample_persona,
        listing=sample_listing,
        router=mock_router,
    )

    assert result["recommendation"] == "MAYBE"
    assert result["overall_score"] == 60
