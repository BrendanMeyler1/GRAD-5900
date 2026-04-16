"""Unit tests for scrapers.greenhouse.GreenhouseScraper."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from scrapers.greenhouse import GreenhouseScraper


_STRIPE_RESPONSE = {
    "jobs": [
        {
            "id": 1001,
            "title": "Backend Engineer",
            "absolute_url": "https://boards.greenhouse.io/stripe/jobs/1001",
            "location": {"name": "New York, NY"},
            "content": "<p>Build <b>payment</b> infrastructure at scale. Python + PostgreSQL.</p>",
        },
        {
            "id": 1002,
            "title": "Product Manager",
            "absolute_url": "https://boards.greenhouse.io/stripe/jobs/1002",
            "location": {"name": "San Francisco, CA"},
            "content": "<p>Lead product strategy.</p>",
        },
    ]
}


@pytest.fixture
def scraper() -> GreenhouseScraper:
    s = GreenhouseScraper(boards=["stripe"])
    s._fetch_board = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            s._parse(j, "stripe") for j in _STRIPE_RESPONSE["jobs"]
        ]
    )
    return s


async def test_strips_html_in_description(scraper: GreenhouseScraper) -> None:
    results = await scraper.search(query="backend", location="", limit=5)
    assert len(results) == 1
    job = results[0]
    assert "<p>" not in job.description
    assert "<b>" not in job.description
    assert "payment" in job.description.lower()


async def test_title_substring_match_filters(scraper: GreenhouseScraper) -> None:
    # Query "backend" only matches 1 of the 2 postings
    results = await scraper.search(query="backend", location="", limit=10)
    assert len(results) == 1
    assert results[0].title == "Backend Engineer"


async def test_location_filter(scraper: GreenhouseScraper) -> None:
    results = await scraper.search(query="", location="New York", limit=10)
    titles = [r.title for r in results]
    assert "Backend Engineer" in titles
    assert "Product Manager" not in titles  # SF location


async def test_company_inferred_from_board_token(scraper: GreenhouseScraper) -> None:
    results = await scraper.search(query="backend", location="", limit=5)
    assert results[0].company == "Stripe"


async def test_ats_type_is_greenhouse(scraper: GreenhouseScraper) -> None:
    results = await scraper.search(query="backend", location="", limit=5)
    assert results[0].ats_type == "greenhouse"
    assert results[0].source == "greenhouse"
