"""
agents/job_scout.py — Worker agent: discover jobs and score them.

Pipeline:
    1. Run all configured scrapers concurrently (JSearch + Greenhouse + Lever).
    2. Deduplicate across sources using JobListing.dedup_key().
    3. For each unique listing, make a single Claude call with the user's
       profile and the job description → returns {score, strengths, gaps,
       summary, interview_likelihood}.
    4. Upsert each scored job into the DB and return the list sorted by
       fit score.

This runs as a background task from /api/jobs/search.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from db.store import FullProfile, JobListing as DBJobListing, Store
from llm.client import FAST_MODEL, LLMClient, load_prompt
from scrapers import GreenhouseScraper, JSearchScraper, JobListing, LeverScraper

log = logging.getLogger(__name__)


@dataclass
class ScoredJob:
    """A job listing enriched with fit-score metadata from the LLM."""

    listing: JobListing
    score: float
    summary: str
    strengths: list[str]
    gaps: list[str]
    interview_likelihood: str  # low | medium | medium-high | high


class JobScout:
    """
    Manager worker for job discovery + scoring.

    Usage:
        scout = JobScout(store, llm)
        results = await scout.discover("backend engineer", "Boston", limit=25)
    """

    def __init__(
        self,
        store: Store,
        llm: LLMClient | None = None,
        scrapers: list[Any] | None = None,
    ) -> None:
        self.store = store
        self.llm = llm or LLMClient()
        self._scrapers = scrapers  # injected for tests; None = defaults
        self._fit_prompt = load_prompt("fit_scorer")

    async def discover(
        self,
        query: str,
        location: str = "",
        limit: int = 25,
    ) -> list[ScoredJob]:
        """
        Full discovery: scrape → dedupe → score → persist.

        Returns jobs sorted by fit score descending.
        """
        log.info(
            "job_scout.start",
            extra={"query": query, "location": location, "limit": limit},
        )

        profile = self.store.get_full_profile()
        if profile is None:
            log.warning("job_scout.no_profile")
            return []

        listings = await self._scrape_all(query, location, limit)
        listings = self._deduplicate(listings)
        log.info(
            "job_scout.scraped",
            extra={"unique": len(listings), "query": query},
        )

        # Score concurrently. Cap at 3 to stay well under Anthropic's rate limit.
        # Each call uses Haiku (faster + separate quota from Opus).
        sem = asyncio.Semaphore(3)

        async def score_one(listing: JobListing) -> ScoredJob | None:
            async with sem:
                # Small per-slot jitter so all 3 slots don't fire simultaneously
                await asyncio.sleep(0.15)
                return await self._score_fit(listing, profile)

        scored_results = await asyncio.gather(
            *(score_one(lst) for lst in listings),
            return_exceptions=False,
        )
        scored = [s for s in scored_results if s is not None]

        # Persist to DB
        for s in scored:
            self._upsert(s)

        scored.sort(key=lambda s: s.score, reverse=True)

        # Log the scrape run
        self.store.log_scrape_run(
            source="combined",
            query=query,
            location=location,
            results_count=len(scored),
        )

        log.info(
            "job_scout.complete",
            extra={
                "query": query,
                "scored": len(scored),
                "top_score": scored[0].score if scored else 0,
            },
        )
        return scored[:limit]

    # --- internal ---------------------------------------------------------

    async def _scrape_all(
        self, query: str, location: str, limit: int
    ) -> list[JobListing]:
        """Run all scrapers concurrently. Each failure is isolated."""
        scrapers = self._scrapers or [
            JSearchScraper(),
            GreenhouseScraper(),
            LeverScraper(),
        ]
        tasks = [
            self._safe_search(s, query, location, limit) for s in scrapers
        ]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        all_jobs: list[JobListing] = []
        for r in results:
            all_jobs.extend(r)

        # Close any scrapers we created
        if self._scrapers is None:
            for s in scrapers:
                await s.close()

        return all_jobs

    @staticmethod
    async def _safe_search(
        scraper: Any, query: str, location: str, limit: int
    ) -> list[JobListing]:
        try:
            return await scraper.search(query=query, location=location, limit=limit)
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "job_scout.scraper_failed",
                extra={"source": getattr(scraper, "source", "?"), "error": str(exc)},
            )
            return []

    @staticmethod
    def _deduplicate(listings: list[JobListing]) -> list[JobListing]:
        """Keep first occurrence per dedup_key."""
        seen: dict[str, JobListing] = {}
        for l in listings:
            key = l.dedup_key()
            if key not in seen:
                seen[key] = l
        return list(seen.values())

    async def _score_fit(
        self, listing: JobListing, profile: FullProfile
    ) -> ScoredJob | None:
        """Single Claude call per listing. Returns None on parse failure."""
        user_content = f"""CANDIDATE PROFILE:
{profile.to_context_string()}

JOB POSTING:
Title: {listing.title}
Company: {listing.company}
Location: {listing.location}
{'Remote: yes' if listing.remote_ok else ''}

Description:
{listing.description[:6000]}

Score this match. Return only the JSON object described in your instructions.
"""
        try:
            # Use Haiku for scoring: 10× cheaper, much higher rate limit, plenty smart
            # enough for JSON fit assessment.
            raw = await self.llm.chat(
                messages=[{"role": "user", "content": user_content}],
                system=self._fit_prompt,
                max_tokens=600,
                model=FAST_MODEL,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "job_scout.fit_llm_failed",
                extra={"job_id": listing.id, "error": str(exc)},
            )
            return None

        if not isinstance(raw, str):
            raw = str(raw)
        parsed = _parse_fit_json(raw)
        if parsed is None:
            log.warning("job_scout.fit_parse_failed", extra={"job_id": listing.id})
            return None

        score = float(parsed.get("score", 0))
        score = max(0.0, min(100.0, score))

        return ScoredJob(
            listing=listing,
            score=score,
            summary=parsed.get("summary", ""),
            strengths=list(parsed.get("strengths", []) or [])[:6],
            gaps=list(parsed.get("gaps", []) or [])[:6],
            interview_likelihood=parsed.get("interview_likelihood", "medium"),
        )

    def _upsert(self, scored: ScoredJob) -> None:
        """Persist the scored job to the DB."""
        l = scored.listing
        self.store.upsert_job(
            {
                "id": l.id,
                "source": l.source,
                "ats_type": l.ats_type,
                "title": l.title,
                "company": l.company,
                "location": l.location,
                "remote_ok": l.remote_ok,
                "description": l.description,
                "apply_url": l.apply_url,
                "posted_at": l.posted_at,
                "fit_score": scored.score,
                "fit_summary": scored.summary,
                "fit_strengths": scored.strengths,
                "fit_gaps": scored.gaps,
                "salary_min": l.salary_min,
                "salary_max": l.salary_max,
                "employment_type": l.employment_type,
                "status": "new",
            }
        )
        # Persist fit details (including interview_likelihood) via dedicated method
        self.store.update_job_fit(
            l.id,
            score=scored.score,
            summary=scored.summary,
            strengths=scored.strengths,
            gaps=scored.gaps,
            interview_likelihood=scored.interview_likelihood,
        )


def _parse_fit_json(raw: str) -> dict[str, Any] | None:
    """Tolerant JSON extraction from LLM response."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```$", "", text)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
