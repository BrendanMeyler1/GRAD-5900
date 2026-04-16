"""
scrapers/lever.py — Lever.co public job board scraper.

Lever hosts company job boards at `https://jobs.lever.co/{company}` with
stable HTML structure. No auth required. Accepts a list of company slugs
and returns matching jobs.

Usage:
    scraper = LeverScraper(companies=["netflix", "figma"])
    jobs = await scraper.search("engineer", "", limit=20)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Iterable

import httpx
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, JobListing, make_id

log = logging.getLogger(__name__)

_BOARD_URL = "https://jobs.lever.co/{company}"


DEFAULT_COMPANIES: tuple[str, ...] = (
    "netflix",
    "palantir",
    "ramp",
    "plaid",
    "brex",
    "rippling",
    "retool",
    "mercury",
    "scale",
    "huggingface",
    "replicate",
    "perplexity-ai",
    "mistral",
)


class LeverScraper(BaseScraper):
    """
    Scrapes public Lever job boards by parsing their HTML.

    Lever pages are rendered server-side so a straight HTTP fetch works
    without a browser. The HTML structure is stable — each posting lives
    in `<div class="posting">` with nested title/location/team elements.
    """

    source = "lever"

    def __init__(
        self,
        companies: Iterable[str] | None = None,
        timeout: float = 20.0,
        concurrency: int = 6,
    ) -> None:
        self.companies = tuple(companies) if companies else DEFAULT_COMPANIES
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; job-finder-v2/1.0; +local-agent)"
                ),
            },
            follow_redirects=True,
        )
        self._sem = asyncio.Semaphore(concurrency)

    async def close(self) -> None:
        await self._client.aclose()

    async def search(
        self,
        query: str,
        location: str = "",
        limit: int = 20,
    ) -> list[JobListing]:
        q = query.lower().strip()
        loc = location.lower().strip()
        remote_only = loc == "remote"

        log.info(
            "lever.start",
            extra={"query": q, "location": loc, "companies": len(self.companies)},
        )

        tasks = [self._fetch_company(c) for c in self.companies]
        all_jobs = await asyncio.gather(*tasks, return_exceptions=False)

        matched: list[JobListing] = []
        for company_jobs in all_jobs:
            for job in company_jobs:
                if not _matches(job, q, loc, remote_only):
                    continue
                matched.append(job)
                if len(matched) >= limit:
                    break
            if len(matched) >= limit:
                break

        log.info("lever.complete", extra={"results": len(matched)})
        return matched[:limit]

    # --- internal ---------------------------------------------------------

    async def _fetch_company(self, company: str) -> list[JobListing]:
        url = _BOARD_URL.format(company=company)
        async with self._sem:
            try:
                resp = await self._client.get(url)
            except httpx.HTTPError as exc:
                log.warning(
                    "lever.fetch_error",
                    extra={"company": company, "error": str(exc)},
                )
                return []

        if resp.status_code != 200:
            log.warning(
                "lever.non_200",
                extra={"company": company, "status": resp.status_code},
            )
            return []

        return await asyncio.to_thread(self._parse_html, resp.text, company)

    def _parse_html(self, html: str, company: str) -> list[JobListing]:
        """Parse a Lever board page → list of JobListing."""
        soup = BeautifulSoup(html, "html.parser")
        display_name = self._display_name(soup, company)
        postings = soup.select("div.posting")

        results: list[JobListing] = []
        for posting in postings:
            link = posting.select_one("a.posting-title")
            if not link:
                continue
            href = link.get("href", "").strip()
            if not href:
                continue
            title_el = link.select_one("h5, .posting-name")
            title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)
            if not title:
                continue

            categories = posting.select(".posting-categories span") or posting.select(
                ".posting-category"
            )
            cat_texts = [c.get_text(" ", strip=True) for c in categories]
            location = next(
                (c for c in cat_texts if "·" not in c and len(c) < 80),
                "",
            )

            # Full description requires fetching the posting page. We skip
            # that here to keep the scrape fast — downstream fit scoring
            # uses whatever is on the board card plus the title.
            snippet_el = posting.select_one(".posting-description, .description")
            description = snippet_el.get_text(" ", strip=True) if snippet_el else ""

            results.append(
                JobListing(
                    id=make_id(self.source, href),
                    source=self.source,
                    title=title,
                    company=display_name,
                    apply_url=href,
                    location=location,
                    description=description,
                    ats_type="lever",
                    raw={"categories": cat_texts},
                )
            )
        return results

    @staticmethod
    def _display_name(soup: BeautifulSoup, slug: str) -> str:
        """Prefer the human-readable company name from the page header."""
        title_tag = soup.find("title")
        if title_tag and title_tag.string:
            text = title_tag.string.strip()
            # Lever titles look like "Company — Jobs" or "Jobs at Company"
            if "—" in text:
                return text.split("—")[0].strip()
            if "Jobs at " in text:
                return text.replace("Jobs at ", "").strip()
        return slug.replace("-", " ").title()


def _matches(job: JobListing, q: str, loc: str, remote_only: bool) -> bool:
    if remote_only and not job.remote_ok and "remote" not in job.location.lower():
        return False
    if loc and not remote_only and loc not in job.location.lower():
        return False
    if not q:
        return True
    haystack = f"{job.title} {job.description[:500]}".lower()
    tokens = [t for t in q.split() if t]
    return all(tok in haystack for tok in tokens)
