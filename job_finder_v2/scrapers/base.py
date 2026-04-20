"""
scrapers/base.py — Abstract base class and shared types for job scrapers.

All scrapers return a list of `JobListing` objects with a consistent schema
regardless of source (JSearch, Greenhouse, Lever, etc.). Downstream code
(fit scoring, DB upsert, pipeline) operates on `JobListing` only — it never
cares about the upstream source format.

`detect_ats_type(url)` inspects an apply URL's domain and returns a canonical
ATS identifier. The form filler uses this to load per-ATS memory/notes, but
the filler itself is universal — it works even when ats_type is "unknown".

Usage:
    from scrapers.base import BaseScraper, JobListing, detect_ats_type

    class MyScraper(BaseScraper):
        source = "mysource"
        async def search(self, query, location, limit):
            ...
            return [JobListing(...), ...]
"""

from __future__ import annotations

import hashlib
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse

log = logging.getLogger(__name__)


# --- ATS detection ---------------------------------------------------------

_ATS_DOMAIN_MAP: dict[str, str] = {
    "boards.greenhouse.io": "greenhouse",
    "boards-api.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever",
    "hire.lever.co": "lever",
    "linkedin.com": "linkedin",
    "www.linkedin.com": "linkedin",
    "myworkdayjobs.com": "workday",
    "smartrecruiters.com": "smartrecruiters",
    "jobs.smartrecruiters.com": "smartrecruiters",
    "ashbyhq.com": "ashby",
    "jobs.ashbyhq.com": "ashby",
    "workable.com": "workable",
    "apply.workable.com": "workable",
    "icims.com": "icims",
    "taleo.net": "taleo",
    "bamboohr.com": "bamboohr",
    "jobvite.com": "jobvite",
    "indeed.com": "indeed",
    "ziprecruiter.com": "ziprecruiter",
    "glassdoor.com": "glassdoor",
    "handshake.com": "handshake",
    "joinhandshake.com": "handshake",
}


def detect_ats_type(url: str | None) -> str:
    """
    Detect which ATS (Applicant Tracking System) an apply URL belongs to.

    Returns a canonical lowercase identifier — 'greenhouse', 'lever',
    'linkedin', 'workday', etc. — or 'universal' if the domain is unknown.

    The universal filler works for any ATS, so this is advisory only:
    it's used for per-company form memory and for display badges in the UI.
    """
    if not url:
        return "universal"

    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return "universal"

    if not host:
        return "universal"

    # Exact match first
    if host in _ATS_DOMAIN_MAP:
        return _ATS_DOMAIN_MAP[host]

    # Suffix match — handles subdomains like `company.myworkdayjobs.com`
    for domain, ats in _ATS_DOMAIN_MAP.items():
        if host == domain or host.endswith("." + domain):
            return ats

    return "universal"


# --- JobListing dataclass --------------------------------------------------


@dataclass
class JobListing:
    """
    Canonical representation of a job listing across all sources.

    Required: id, source, title, company, apply_url.
    All other fields have sensible defaults so partial results from
    a scraper never blow up downstream code.

    `id` should be stable across runs for the same posting — scrapers
    compute it by hashing (source + apply_url) when no source-native ID
    is available.
    """

    id: str
    source: str
    title: str
    company: str
    apply_url: str
    location: str = ""
    remote_ok: bool = False
    description: str = ""
    posted_at: str | None = None
    ats_type: str = "universal"
    salary_min: int | None = None
    salary_max: int | None = None
    employment_type: str | None = None  # full-time, contract, intern, etc.
    raw: dict[str, Any] = field(default_factory=dict)  # original payload

    def __post_init__(self) -> None:
        # Auto-detect ATS if caller left it at the default
        if self.ats_type == "universal" and self.apply_url:
            self.ats_type = detect_ats_type(self.apply_url)

        # Normalize location whitespace
        self.location = _normalize(self.location)
        self.title = _normalize(self.title)
        self.company = _normalize(self.company)

        # If description contains remote cues, mark remote_ok
        if not self.remote_ok and self.description:
            self.remote_ok = bool(_REMOTE_RE.search(self.description[:500]))
        if not self.remote_ok and self.location:
            self.remote_ok = bool(_REMOTE_RE.search(self.location))

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict (for DB upsert, JSON responses)."""
        d = asdict(self)
        # raw payload is not stored in DB — drop it
        d.pop("raw", None)
        return d

    def dedup_key(self) -> str:
        """
        Stable key for cross-source deduplication.

        Same company + same title + same base apply URL → same posting,
        even if one source reports it via JSearch and another directly.
        """
        base_url = self.apply_url.split("?")[0].rstrip("/").lower()
        parts = [self.company.lower().strip(), self.title.lower().strip(), base_url]
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


_REMOTE_RE = re.compile(
    r"\b(remote|work from home|wfh|fully remote|anywhere|distributed)\b",
    re.IGNORECASE,
)


def _normalize(value: str) -> str:
    """Collapse internal whitespace, strip edges. Safe on empty strings."""
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def make_id(source: str, apply_url: str, native_id: str | None = None) -> str:
    """
    Generate a stable JobListing ID.

    If the source provides a native ID (e.g. Greenhouse job.id), use that:
    `{source}:{native_id}`. Otherwise hash source+apply_url so re-scraping
    the same listing produces the same ID.
    """
    if native_id:
        return f"{source}:{native_id}"
    digest = hashlib.sha256(f"{source}|{apply_url}".encode("utf-8")).hexdigest()[:16]
    return f"{source}:{digest}"


# --- BaseScraper -----------------------------------------------------------


class BaseScraper(ABC):
    """
    Abstract base for all job scrapers.

    Subclasses must set the `source` class attribute and implement `search`.
    Errors should be caught and logged — a scraper that raises will abort
    the whole discovery run. Prefer returning `[]` on failure.
    """

    source: str = "base"

    @abstractmethod
    async def search(
        self,
        query: str,
        location: str = "",
        limit: int = 20,
    ) -> list[JobListing]:
        """
        Search the source for matching jobs.

        Args:
            query: Free-text search query (job title, keywords).
            location: Optional location filter (city, state, "remote").
            limit: Maximum number of results to return.

        Returns:
            List of `JobListing` objects, possibly empty. Never raises.
        """
        ...

    async def close(self) -> None:
        """
        Release any resources (HTTP clients, browser sessions) held by
        this scraper. Called at the end of a scrape run. Override if needed.
        """
        return None

    # --- shared helpers for subclasses -----------------------------------

    @staticmethod
    def strip_html(html: str) -> str:
        """Remove HTML tags and collapse whitespace. Cheap, not a parser."""
        if not html:
            return ""
        text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"</(p|div|li|h[1-6])>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        # Decode a handful of common entities
        text = (
            text.replace("&nbsp;", " ")
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .replace("&#39;", "'")
            .replace("&rsquo;", "'")
            .replace("&lsquo;", "'")
            .replace("&ldquo;", '"')
            .replace("&rdquo;", '"')
        )
        return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()
