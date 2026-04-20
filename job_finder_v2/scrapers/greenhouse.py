"""
scrapers/greenhouse.py — Greenhouse public Board API.

Many tech companies (Stripe, Airbnb, Pinterest, Instacart, etc.) post jobs
on Greenhouse with public, unauthenticated board APIs at:

    https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true

This scraper takes a list of board tokens (company slugs) and returns all
matching jobs across them. Because the API is public, no key is required —
it's a reliable supplement to JSearch when you know specific target companies.

Usage:
    scraper = GreenhouseScraper(boards=["stripe", "airbnb"])
    jobs = await scraper.search("backend engineer", "remote", limit=30)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Iterable

import httpx

from scrapers.base import BaseScraper, JobListing, make_id

log = logging.getLogger(__name__)

_BOARD_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


# A curated starter list. Users can extend by passing custom boards=[...]
DEFAULT_BOARDS: tuple[str, ...] = (
    "stripe",
    "airbnb",
    "pinterest",
    "instacart",
    "robinhood",
    "figma",
    "klaviyo",
    "notion",
    "asana",
    "coinbase",
    "doordash",
    "reddit",
    "discord",
    "dropbox",
    "gitlab",
    "hashicorp",
    "mongodb",
    "elastic",
    "snowflake",
    "databricks",
    "cloudflare",
    "twilio",
    "zapier",
    "vercel",
    "anthropic",
    "openai",
)


class GreenhouseScraper(BaseScraper):
    """
    Greenhouse Board API scraper.

    Fetches the full job list for each configured board and filters
    locally by the query string. Greenhouse doesn't offer server-side
    full-text search, so we do case-insensitive substring matching
    against title and location.
    """

    source = "greenhouse"

    def __init__(
        self,
        boards: Iterable[str] | None = None,
        timeout: float = 20.0,
        concurrency: int = 6,
    ) -> None:
        self.boards = tuple(boards) if boards else DEFAULT_BOARDS
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": "job-finder-v2/1.0"},
        )
        self._sem = asyncio.Semaphore(concurrency)

    async def close(self) -> None:
        await self._client.aclose()

    async def search(
        self,
        query: str,
        location: str = "",
        limit: int = 30,
    ) -> list[JobListing]:
        q = query.lower().strip()
        loc = location.lower().strip()
        remote_only = loc == "remote"

        log.info(
            "greenhouse.start",
            extra={"query": q, "location": loc, "boards": len(self.boards)},
        )

        tasks = [self._fetch_board(token) for token in self.boards]
        all_results = await asyncio.gather(*tasks, return_exceptions=False)

        matched: list[JobListing] = []
        for board_jobs in all_results:
            for job in board_jobs:
                if not _matches(job, q, loc, remote_only):
                    continue
                matched.append(job)
                if len(matched) >= limit:
                    break
            if len(matched) >= limit:
                break

        log.info(
            "greenhouse.complete",
            extra={"query": q, "results": len(matched)},
        )
        return matched[:limit]

    # --- internal ---------------------------------------------------------

    async def _fetch_board(self, token: str) -> list[JobListing]:
        """Fetch all jobs for one board token. Returns [] on any error."""
        url = _BOARD_URL.format(token=token)
        async with self._sem:
            try:
                resp = await self._client.get(url, params={"content": "true"})
            except httpx.HTTPError as exc:
                log.warning(
                    "greenhouse.fetch_error",
                    extra={"board": token, "error": str(exc)},
                )
                return []

        if resp.status_code != 200:
            log.warning(
                "greenhouse.non_200",
                extra={"board": token, "status": resp.status_code},
            )
            return []

        try:
            payload = resp.json()
        except ValueError:
            log.warning("greenhouse.invalid_json", extra={"board": token})
            return []

        jobs = payload.get("jobs") or []
        parsed = [self._parse(j, token) for j in jobs]
        return [j for j in parsed if j is not None]

    def _parse(self, raw: dict[str, Any], board_token: str) -> JobListing | None:
        title = raw.get("title") or ""
        native_id = str(raw.get("id") or "")
        apply_url = raw.get("absolute_url") or ""
        if not title or not apply_url:
            return None

        # Greenhouse uses a structured location object
        location_obj = raw.get("location") or {}
        location = (location_obj.get("name") if isinstance(location_obj, dict) else "") or ""

        # Company: infer from board token (board tokens are typically the
        # lowercase company slug). Fall back to that if `content` doesn't help.
        company = (raw.get("company") or {}).get("name") if isinstance(raw.get("company"), dict) else None
        if not company:
            company = board_token.replace("-", " ").title()

        html_content = raw.get("content") or ""
        description = self.strip_html(html_content)

        return JobListing(
            id=make_id(self.source, apply_url, native_id),
            source=self.source,
            title=title,
            company=company,
            apply_url=apply_url,
            location=location,
            description=description,
            posted_at=raw.get("updated_at") or raw.get("first_published"),
            ats_type="greenhouse",
            raw=raw,
        )


def _matches(job: JobListing, q: str, loc: str, remote_only: bool) -> bool:
    """True if job matches the query filters."""
    if remote_only and not job.remote_ok and "remote" not in job.location.lower():
        return False
    if loc and not remote_only:
        if loc not in job.location.lower():
            return False
    if not q:
        return True
    haystack = f"{job.title} {job.description[:2000]}".lower()
    # Split query on whitespace; all tokens must appear (AND semantics)
    tokens = [t for t in q.split() if t]
    return all(tok in haystack for tok in tokens)
