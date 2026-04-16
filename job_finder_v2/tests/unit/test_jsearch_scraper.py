"""Unit tests for scrapers.jsearch.JSearchScraper — response parsing + ATS detection."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from scrapers.jsearch import JSearchScraper


# A realistic JSearch response chunk, trimmed to what we actually read.
_FIXTURE = {
    "status": "OK",
    "request_id": "abc",
    "data": [
        {
            "job_id": "abc-123",
            "job_title": "Backend Engineer",
            "employer_name": "Stripe",
            "job_apply_link": "https://boards.greenhouse.io/stripe/jobs/555",
            "job_city": "New York",
            "job_state": "NY",
            "job_country": "US",
            "job_is_remote": False,
            "job_description": "Build payment APIs.",
            "job_posted_at_datetime_utc": "2026-04-10T12:00:00Z",
            "job_min_salary": 120000,
            "job_max_salary": 180000,
            "job_salary_currency": "USD",
            "job_employment_type": "FULLTIME",
        },
        {
            "job_id": "xyz-789",
            "job_title": "Full-stack Engineer",
            "employer_name": "Ramp",
            "job_apply_link": "https://jobs.lever.co/ramp/abc",
            "job_city": "",
            "job_state": "",
            "job_country": "US",
            "job_is_remote": True,
            "job_description": "Build tools for finance teams. Remote-friendly.",
        },
        {
            # Bad row — no apply link — should be skipped
            "job_title": "No URL",
            "employer_name": "BadCo",
        },
    ],
}


@pytest.fixture
def scraper() -> JSearchScraper:
    s = JSearchScraper(api_key="test-key")
    # Short-circuit HTTP — we only want to exercise _parse + pagination logic
    s._fetch_page = AsyncMock(return_value=_FIXTURE)  # type: ignore[method-assign]
    return s


async def test_parses_valid_listings(scraper: JSearchScraper) -> None:
    results = await scraper.search(query="backend engineer", location="New York, NY", limit=10)
    # Two valid rows (third has no apply_link)
    assert len(results) == 2
    stripe = results[0]
    assert stripe.title == "Backend Engineer"
    assert stripe.company == "Stripe"
    assert stripe.location == "New York, NY, US"
    assert stripe.salary_min == 120_000
    assert stripe.ats_type == "greenhouse"  # auto-detected from apply URL


async def test_detects_lever_ats_type(scraper: JSearchScraper) -> None:
    results = await scraper.search(query="engineer", location="", limit=10)
    ramp = next(j for j in results if j.company == "Ramp")
    assert ramp.ats_type == "lever"
    assert ramp.remote_ok is True


async def test_skips_rows_missing_required_fields(scraper: JSearchScraper) -> None:
    results = await scraper.search(query="engineer", location="", limit=10)
    # 3 rows in, 2 out — the no-URL row is filtered
    assert len(results) == 2


async def test_returns_empty_when_no_api_key() -> None:
    s = JSearchScraper(api_key=None)
    results = await s.search(query="anything", limit=10)
    assert results == []
    await s.close()


async def test_honors_limit(scraper: JSearchScraper) -> None:
    results = await scraper.search(query="engineer", location="", limit=1)
    assert len(results) == 1
