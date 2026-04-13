"""
Jobs routes - discovery queue APIs.

Endpoints:
    POST /api/jobs/scan
    GET  /api/jobs/queue
    POST /api/jobs/{listing_id}/skip
    POST /api/jobs/{listing_id}/flag
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse

from agents.job_scout import build_decision_queue
from api.routes import persona as persona_routes

router = APIRouter()

# In-memory jobs state (Phase 2)
_listings_by_id: dict[str, dict[str, Any]] = {}
_listing_decisions: dict[str, str] = {}  # pending|skipped|flagged
_listing_flags: dict[str, str] = {}
_last_scan_at: str | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_listing_by_id(listing_id: str) -> dict[str, Any] | None:
    """Shared accessor for applications route."""
    return _listings_by_id.get(listing_id)


def list_all_listings() -> list[dict[str, Any]]:
    return list(_listings_by_id.values())


def reset_jobs_state() -> None:
    """Test helper: clear in-memory jobs state."""
    global _last_scan_at
    _listings_by_id.clear()
    _listing_decisions.clear()
    _listing_flags.clear()
    _last_scan_at = None


class JobsScanRequest(BaseModel):
    listings: list[dict[str, Any]] = Field(default_factory=list)
    persona: dict[str, Any] | None = None
    use_llm: bool = False
    max_results: int = 25


class FlagRequest(BaseModel):
    reason: str = "manual_review_requested"


class AddByUrlRequest(BaseModel):
    url: str
    use_llm: bool = False


@router.post("/jobs/scan", summary="Trigger Job Scout scan")
async def scan_jobs(request: JobsScanRequest):
    """
    Run Job Scout against provided listings and refresh queue state.
    """
    global _last_scan_at

    persona = request.persona or persona_routes._current_persona  # noqa: SLF001
    if persona is None:
        raise HTTPException(
            status_code=400,
            detail="No persona available. Upload a resume or pass persona in request.",
        )
    if not request.listings:
        raise HTTPException(status_code=400, detail="No listings provided for scan.")

    decision_queue = build_decision_queue(
        listings=request.listings,
        persona=persona,
        use_llm=request.use_llm,
        max_results=request.max_results,
    )

    # Preserve prior manual decisions when listing IDs are stable
    for item in [*decision_queue["queue"], *decision_queue["deprioritized"]]:
        listing_id = str(item.get("listing_id"))
        if not listing_id:
            continue
        _listings_by_id[listing_id] = item
        _listing_decisions.setdefault(
            listing_id,
            "skipped" if item.get("smart_skip_recommended") else "pending",
        )

    _last_scan_at = _utc_now()
    return {
        "status": "success",
        "scanned": len(request.listings),
        "queue_size": len(decision_queue["queue"]),
        "deprioritized_size": len(decision_queue["deprioritized"]),
        "scanned_at": _last_scan_at,
    }


@router.get("/jobs/queue", summary="Get job listings pending review")
async def get_jobs_queue():
    """
    Return current decision queue listings (excluding skipped).
    """
    queue_items: list[dict[str, Any]] = []
    for listing in _listings_by_id.values():
        listing_id = str(listing.get("listing_id"))
        decision = _listing_decisions.get(listing_id, "pending")
        if decision == "skipped":
            continue
        enriched = dict(listing)
        enriched["decision"] = decision
        if listing_id in _listing_flags:
            enriched["flag_reason"] = _listing_flags[listing_id]
        queue_items.append(enriched)

    queue_items.sort(key=lambda item: float(item.get("priority_score", 0.0)), reverse=True)
    return {
        "status": "success",
        "last_scan_at": _last_scan_at,
        "count": len(queue_items),
        "queue": queue_items,
    }


@router.post("/jobs/{listing_id}/skip", summary="Skip a listing")
async def skip_listing(listing_id: str):
    if listing_id not in _listings_by_id:
        raise HTTPException(status_code=404, detail=f"Listing not found: {listing_id}")
    _listing_decisions[listing_id] = "skipped"
    return {
        "status": "success",
        "listing_id": listing_id,
        "decision": "skipped",
    }


@router.post("/jobs/{listing_id}/flag", summary="Flag a listing")
async def flag_listing(listing_id: str, request: FlagRequest):
    if listing_id not in _listings_by_id:
        raise HTTPException(status_code=404, detail=f"Listing not found: {listing_id}")
    _listing_decisions[listing_id] = "flagged"
    _listing_flags[listing_id] = request.reason
    return {
        "status": "success",
        "listing_id": listing_id,
        "decision": "flagged",
        "reason": request.reason,
    }


@router.post("/jobs/add-by-url", summary="Scrape and add a job by URL")
async def add_job_by_url(request: AddByUrlRequest):
    """
    Scrape a Greenhouse (or Lever) URL for basic job details and push it to the queue.
    """
    try:
        from browser.playwright_driver import PlaywrightDriver
        
        driver = PlaywrightDriver(headless=False)
        await driver.start()
        try:
            await driver.goto(request.url)
            page = driver._require_page()
            html = await page.content()
            page_title = await page.title()
        finally:
            await driver.stop()
            
        soup = BeautifulSoup(html, "html.parser")
        
        # Super basic heuristic scraper (mainly for Greenhouse/Lever)
        title = ""
        company = ""
        location = ""
        description = ""
        
        # Title heuristics
        title_tag = soup.find("h1")
        if title_tag:
            title = title_tag.get_text(strip=True)
            
        # Company heuristics
        company_tag = soup.find("span", class_="company-name") or soup.find("a", class_="company-name")
        if company_tag:
            company = company_tag.get_text(strip=True).replace("at ", "").strip()
        else:
            parsed = urlparse(request.url)
            # Try to grab company from standard Greenhouse URL: boards.greenhouse.io/company
            parts = parsed.path.strip("/").split("/")
            if len(parts) > 0 and parts[0] != "jobs":
                company = parts[0]
            else:
                company = parsed.netloc.split('.')[0]
                
        # Location
        loc_tag = soup.find("div", class_="location")
        if loc_tag:
            location = loc_tag.get_text(strip=True)
            
        # Description
        desc_tag = soup.find("div", id="content") or soup.find("div", class_="description")
        if desc_tag:
            description = desc_tag.get_text(separator="\n", strip=True)
        
        if not title:
            title = soup.title.string.strip() if soup.title else "Unknown Role"
            
        ats_type = "unknown"
        if "greenhouse.io" in request.url or "gh_jid=" in request.url:
            ats_type = "greenhouse"
        elif "lever.co" in request.url:
            ats_type = "lever"
            
        listing_payload = {
            "source_url": request.url,
            "apply_url": request.url,
            "ats_type": ats_type,
            "company": {"name": company},
            "role": {
                "title": title,
                "location": location,
                "description_text": description,
                "posted_date": _utc_now(),
            }
        }
        
        # Hook directly into the scan_jobs logic
        scan_req = JobsScanRequest(
            listings=[listing_payload],
            use_llm=request.use_llm
        )
        return await scan_jobs(scan_req)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Scraping failed: {str(e)}")
