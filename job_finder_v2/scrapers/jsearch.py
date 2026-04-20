"""
scrapers/jsearch.py — JSearch RapidAPI job aggregator.

JSearch (https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch) aggregates
listings from LinkedIn, Indeed, Google Jobs, ZipRecruiter, Glassdoor, and
others through one API. It's the primary job discovery source because it
covers the whole job board ecosystem in a single authenticated call.

Free tier: 200 requests/month. Each `search` consumes 1 request regardless
of how many results come back. The caller is responsible for not burning
quota on every keystroke.

Usage:
    from scrapers.jsearch import JSearchScraper
    scraper = JSearchScraper()
    jobs = await scraper.search("backend engineer", "Boston, MA", limit=25)
    await scraper.close()
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from config import settings
from scrapers.base import BaseScraper, JobListing, make_id

log = logging.getLogger(__name__)

_JSEARCH_HOST = "jsearch.p.rapidapi.com"
_JSEARCH_URL = f"https://{_JSEARCH_HOST}/search"
_PAGE_SIZE = 10  # JSearch returns ~10 per page


class JSearchScraper(BaseScraper):
    """
    Calls JSearch's `/search` endpoint and maps the response into
    `JobListing` objects. Auto-pages until `limit` results are collected.

    Retries on 429 (rate limit) with exponential backoff up to 3 attempts.
    Returns an empty list on permanent failure — never raises.
    """

    source = "jsearch"

    _UNSET: object = object()  # sentinel to distinguish "not provided" from explicit None

    def __init__(self, api_key: str | None = _UNSET, timeout: float = 30.0) -> None:  # type: ignore[assignment]
        # Only fall back to settings when no argument was passed at all.
        # Explicitly passing api_key=None means "no key" (useful in tests).
        self.api_key = api_key if api_key is not JSearchScraper._UNSET else settings.jsearch_api_key
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={
                "X-RapidAPI-Host": _JSEARCH_HOST,
                "X-RapidAPI-Key": self.api_key or "",
            },
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def search(
        self,
        query: str,
        location: str = "",
        limit: int = 20,
    ) -> list[JobListing]:
        if not self.api_key:
            log.warning(
                "jsearch.skipped",
                extra={"reason": "no_api_key"},
            )
            return []

        # JSearch's query field accepts "role in city" natively.
        search_query = query.strip()
        if location and location.lower() != "remote":
            search_query = f"{search_query} in {location}".strip()

        pages_needed = max(1, -(-limit // _PAGE_SIZE))  # ceil division
        log.info(
            "jsearch.start",
            extra={
                "query": search_query,
                "location": location,
                "limit": limit,
                "pages": pages_needed,
            },
        )

        results: list[JobListing] = []
        for page in range(1, pages_needed + 1):
            payload = await self._fetch_page(
                query=search_query,
                page=page,
                remote_only=(location.lower() == "remote"),
            )
            if not payload:
                break
            data = payload.get("data") or []
            for raw in data:
                listing = self._parse(raw)
                if listing:
                    results.append(listing)
                if len(results) >= limit:
                    break
            if len(results) >= limit or len(data) < _PAGE_SIZE:
                break

        log.info(
            "jsearch.complete",
            extra={"query": search_query, "results": len(results)},
        )
        return results[:limit]

    # --- internal ---------------------------------------------------------

    async def _fetch_page(
        self,
        query: str,
        page: int,
        remote_only: bool,
    ) -> dict[str, Any] | None:
        """Single GET with retry on 429/5xx. Returns JSON or None on failure."""
        params: dict[str, Any] = {
            "query": query,
            "page": str(page),
            "num_pages": "1",
            "date_posted": "month",
        }
        if remote_only:
            params["remote_jobs_only"] = "true"

        for attempt in range(3):
            try:
                resp = await self._client.get(_JSEARCH_URL, params=params)
            except httpx.HTTPError as exc:
                log.warning(
                    "jsearch.network_error",
                    extra={"attempt": attempt + 1, "error": str(exc)},
                )
                await asyncio.sleep(2 ** attempt)
                continue

            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError:
                    log.error("jsearch.invalid_json", extra={"page": page})
                    return None

            if resp.status_code in (429, 502, 503, 504):
                log.warning(
                    "jsearch.retryable_status",
                    extra={
                        "status": resp.status_code,
                        "attempt": attempt + 1,
                        "page": page,
                    },
                )
                await asyncio.sleep(2 ** attempt)
                continue

            log.error(
                "jsearch.http_error",
                extra={
                    "status": resp.status_code,
                    "body_snippet": resp.text[:200],
                    "page": page,
                },
            )
            return None

        log.error("jsearch.exhausted_retries", extra={"page": page})
        return None

    def _parse(self, raw: dict[str, Any]) -> JobListing | None:
        """Map a JSearch job dict → JobListing. Returns None if unusable."""
        apply_url = (
            raw.get("job_apply_link")
            or raw.get("job_google_link")
            or (raw.get("apply_options") or [{}])[0].get("apply_link")
            or ""
        )
        title = raw.get("job_title") or ""
        company = raw.get("employer_name") or ""

        if not apply_url or not title or not company:
            return None

        native_id = raw.get("job_id")
        location_parts = [
            raw.get("job_city") or "",
            raw.get("job_state") or "",
            raw.get("job_country") or "",
        ]
        location = ", ".join(p for p in location_parts if p)

        # Salary — JSearch reports min/max in source currency; keep USD or None
        salary_min = raw.get("job_min_salary")
        salary_max = raw.get("job_max_salary")
        if raw.get("job_salary_currency") not in (None, "USD"):
            salary_min = salary_max = None

        description = raw.get("job_description") or ""

        return JobListing(
            id=make_id(self.source, apply_url, native_id),
            source=self.source,
            title=title,
            company=company,
            apply_url=apply_url,
            location=location,
            remote_ok=bool(raw.get("job_is_remote")),
            description=description,
            posted_at=raw.get("job_posted_at_datetime_utc"),
            salary_min=int(salary_min) if isinstance(salary_min, (int, float)) else None,
            salary_max=int(salary_max) if isinstance(salary_max, (int, float)) else None,
            employment_type=raw.get("job_employment_type"),
            raw=raw,
        )
