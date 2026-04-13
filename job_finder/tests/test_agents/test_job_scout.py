"""Tests for Job Scout agent."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from agents.job_scout import build_decision_queue, scout_jobs


def _iso_date(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).date().isoformat()


def _listings():
    return [
        {
            "listing_id": "l1",
            "source": "greenhouse",
            "source_url": "https://boards.greenhouse.io/acme/jobs/111",
            "apply_url": "https://boards.greenhouse.io/acme/jobs/111#app",
            "company": {"name": "Acme Corp", "industry": "fintech"},
            "role": {
                "title": "Senior Backend Engineer",
                "location": "Remote US",
                "posted_date": _iso_date(2),
                "requirements": ["Python", "FastAPI", "AWS"],
                "description_text": "Build backend services and APIs in Python.",
            },
            "high_sensitivity_fields_required": 0,
        },
        {
            "listing_id": "l2",
            "source": "aggregator",
            "source_url": "https://www.indeed.com/viewjob?jk=abc",
            "apply_url": "not_a_valid_link",
            "company": {"name": "LegacyCo", "industry": "retail"},
            "role": {
                "title": "Generalist Engineer",
                "location": "Onsite",
                "posted_date": _iso_date(75),
                "requirements": ["Java"],
                "description_text": "Maintain legacy Java systems.",
            },
            "high_sensitivity_fields_required": 4,
        },
        {
            "listing_id": "l3",
            "source": "greenhouse",
            "source_url": "https://boards.greenhouse.io/acme/jobs/222",
            "apply_url": "https://boards.greenhouse.io/acme/jobs/222#app",
            "company": {"name": "Acme Corp", "industry": "fintech"},
            "role": {
                "title": "Senior Backend Engineer",
                "location": "Remote US",
                "posted_date": _iso_date(3),
                "requirements": ["Python", "Distributed systems"],
                "description_text": "Build backend services and APIs in Python.",
            },
            "high_sensitivity_fields_required": 0,
        },
    ]


def test_scout_jobs_enriches_and_ranks(sample_persona):
    ranked = scout_jobs(_listings(), persona=sample_persona, use_llm=False)

    assert len(ranked) == 3
    assert ranked[0]["listing_id"] == "l1"
    assert ranked[0]["ats_type"] == "greenhouse"
    assert "alive_score" in ranked[0]
    assert ranked[0]["smart_skip_recommended"] is False

    stale = [item for item in ranked if item["listing_id"] == "l2"][0]
    assert stale["smart_skip_recommended"] is True
    assert "broken_apply_link" in stale["smart_skip_reasons"]
    assert "excessive_high_sensitivity_fields" in stale["smart_skip_reasons"]

    duplicate = [item for item in ranked if item["listing_id"] == "l3"][0]
    assert "possible_duplicate_posting" in duplicate["alive_score"]["flags"]


def test_scout_jobs_uses_llm_soft_signals(sample_persona):
    mock_router = MagicMock()
    mock_router.route_json.return_value = {
        "signals": {
            "recruiter_activity": 0.95,
            "headcount_trend": 0.80,
            "financial_health": 0.90,
        },
        "risk_flags": ["soft_signal_high_confidence"],
        "notes": "Hiring appears active with strong signals.",
    }

    ranked = scout_jobs(
        [_listings()[0]],
        persona=sample_persona,
        router=mock_router,
        use_llm=True,
    )

    assert ranked[0]["alive_score"]["signals"]["recruiter_activity"] == 0.95
    assert ranked[0]["scout_notes"] == "Hiring appears active with strong signals."
    assert "soft_signal_high_confidence" in ranked[0]["alive_score"]["flags"]


def test_build_decision_queue_splits_deprioritized(sample_persona):
    decision_queue = build_decision_queue(
        listings=_listings(),
        persona=sample_persona,
        use_llm=False,
    )

    queue_ids = {item["listing_id"] for item in decision_queue["queue"]}
    deprioritized_ids = {item["listing_id"] for item in decision_queue["deprioritized"]}

    assert "l2" in deprioritized_ids
    assert "l1" in queue_ids
