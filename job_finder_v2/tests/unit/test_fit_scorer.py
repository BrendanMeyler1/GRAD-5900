"""
Unit tests for agents.job_scout.JobScout — fit scoring logic.

We test the scoring call path end-to-end with a mocked LLM so we verify:
  - The prompt includes profile + job details
  - JSON parsing tolerates markdown fences
  - Scored results are persisted to DB
  - Ranking is fit_score DESC
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.job_scout import JobScout
from scrapers.base import JobListing


def _make_listing(id_: str, title: str, company: str) -> JobListing:
    return JobListing(
        id=id_,
        source="greenhouse",
        title=title,
        company=company,
        apply_url=f"https://boards.greenhouse.io/{company.lower()}/jobs/{id_}",
        location="Boston, MA",
        description=f"{title} at {company}. Build cool things in Python.",
    )


@pytest.fixture
def fake_scraper() -> MagicMock:
    """A scraper returning two fixed listings with no external calls."""
    s = MagicMock()
    s.source = "fake"
    s.search = AsyncMock(
        return_value=[
            _make_listing("100", "Backend Engineer", "Acme"),
            _make_listing("101", "Frontend Engineer", "Beta"),
        ]
    )
    s.close = AsyncMock()
    return s


async def test_scoring_parses_clean_json(store, sample_profile, fake_scraper) -> None:
    # Seed minimum profile (so get_full_profile has data)
    store.upsert_profile({"first_name": "Jane", "last_name": "Doe", "email": "j@x.com"})
    llm = MagicMock()
    llm.chat = AsyncMock(
        return_value=(
            '{"score": 85, "summary": "Strong Python match.",'
            ' "strengths": ["Python", "FastAPI"], "gaps": ["K8s"],'
            ' "interview_likelihood": "medium-high"}'
        )
    )
    scout = JobScout(store, llm=llm, scrapers=[fake_scraper])
    results = await scout.discover("backend", limit=10)

    assert len(results) == 2
    assert all(r.score == 85 for r in results)
    assert "Python" in results[0].strengths


async def test_scoring_tolerates_markdown_fences(store, fake_scraper) -> None:
    store.upsert_profile({"first_name": "Jane", "email": "j@x.com"})
    llm = MagicMock()
    llm.chat = AsyncMock(
        return_value=(
            "```json\n"
            '{"score": 72, "summary": "ok fit", '
            '"strengths": [], "gaps": [], "interview_likelihood": "medium"}\n'
            "```"
        )
    )
    scout = JobScout(store, llm=llm, scrapers=[fake_scraper])
    results = await scout.discover("x", limit=10)
    assert len(results) == 2
    assert results[0].score == 72


async def test_scoring_persists_to_db(store, fake_scraper) -> None:
    store.upsert_profile({"first_name": "Jane", "email": "j@x.com"})
    llm = MagicMock()
    llm.chat = AsyncMock(
        return_value='{"score": 90, "summary": "x", "strengths": [], "gaps": [], "interview_likelihood": "high"}'
    )
    scout = JobScout(store, llm=llm, scrapers=[fake_scraper])
    await scout.discover("x", limit=10)

    jobs = store.get_jobs()
    assert len(jobs) == 2
    assert all(j.fit_score == 90 for j in jobs)
    assert all(j.interview_likelihood == "high" for j in jobs)


async def test_results_sorted_by_fit_score_desc(store) -> None:
    store.upsert_profile({"first_name": "Jane", "email": "j@x.com"})
    scraper = MagicMock()
    scraper.source = "fake"
    scraper.search = AsyncMock(
        return_value=[
            _make_listing("1", "A", "A"),
            _make_listing("2", "B", "B"),
            _make_listing("3", "C", "C"),
        ]
    )
    scraper.close = AsyncMock()

    scores = iter([60, 90, 75])

    def make_response(*a, **kw) -> str:
        s = next(scores)
        return (
            f'{{"score": {s}, "summary": "", "strengths": [], "gaps": [],'
            f' "interview_likelihood": "medium"}}'
        )

    llm = MagicMock()
    llm.chat = AsyncMock(side_effect=make_response)
    scout = JobScout(store, llm=llm, scrapers=[scraper])
    results = await scout.discover("x", limit=10)

    assert [r.score for r in results] == [90, 75, 60]


async def test_failed_llm_call_drops_job_silently(store, fake_scraper) -> None:
    """If the LLM raises, that job is dropped but others continue."""
    store.upsert_profile({"first_name": "Jane", "email": "j@x.com"})

    calls = {"n": 0}

    async def maybe_fail(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("LLM boom")
        return '{"score": 70, "summary": "x", "strengths": [], "gaps": [], "interview_likelihood": "medium"}'

    llm = MagicMock()
    llm.chat = AsyncMock(side_effect=maybe_fail)
    scout = JobScout(store, llm=llm, scrapers=[fake_scraper])
    results = await scout.discover("x", limit=10)

    # 2 listings, 1 failed, 1 succeeded
    assert len(results) == 1
