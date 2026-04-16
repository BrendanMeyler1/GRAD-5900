"""
api/routes/jobs.py — Job discovery + queue management.

Endpoints:
    GET    /api/jobs                  → filtered list with fit scores
    POST   /api/jobs/search           → trigger scrape (background task)
    POST   /api/jobs/add-url          → scrape single job by URL
    GET    /api/jobs/{job_id}         → job detail
    POST   /api/jobs/{job_id}/queue   → set status='queued'
    POST   /api/jobs/{job_id}/skip    → set status='skipped'
    PATCH  /api/jobs/{job_id}/status  → set arbitrary workflow status
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.dependencies import get_job_scout, get_store
from api.tasks import registry
from db.store import JobFilters, JobListing, Store
from scrapers.base import detect_ats_type, make_id

log = logging.getLogger(__name__)
router = APIRouter()


class SearchRequest(BaseModel):
    """Job search parameters."""

    query: str
    location: str = ""
    limit: int = 30


class AddURLRequest(BaseModel):
    """Single-job URL ingest request."""

    url: str
    title: str | None = None
    company: str | None = None
    description: str | None = None


class StatusUpdate(BaseModel):
    status: str


@router.get("", summary="List jobs with filters", response_model=list[JobListing])
async def list_jobs(
    status: str | None = None,
    source: str | None = None,
    min_fit_score: float | None = Query(None, ge=0, le=100),
    remote_only: bool = False,
    sort_by: str = Query("created_at", pattern="^(created_at|fit_score|posted_at)$"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    store: Store = Depends(get_store),
) -> list[JobListing]:
    """Return a filtered + sorted list of job listings."""
    filters = JobFilters(
        status=status,
        source=source,
        min_fit_score=min_fit_score,
        remote_only=remote_only,
        sort_by=sort_by,
        limit=limit,
        offset=offset,
    )
    return store.get_jobs(filters)


@router.post("/search", summary="Search for jobs across all scrapers")
async def search_jobs(
    payload: SearchRequest,
    job_scout=Depends(get_job_scout),
) -> dict[str, Any]:
    """
    Kick off job discovery in the background. Returns a task_id the UI can poll.

    The JobScout fans out to JSearch + Greenhouse + Lever concurrently, dedups
    results, scores each, and persists to DB. UI refreshes job list on completion.
    """
    task_id = await registry.create(f"Search: '{payload.query}' in '{payload.location}'")

    async def _run() -> None:
        try:
            await registry.update(task_id, progress="searching…")
            scored = await job_scout.discover(
                query=payload.query, location=payload.location, limit=payload.limit
            )
            await registry.complete(
                task_id,
                result={
                    "count": len(scored),
                    "query": payload.query,
                    "location": payload.location,
                },
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("jobs.search.failed", extra={"error": str(exc)})
            await registry.fail(task_id, error=str(exc))

    import asyncio

    asyncio.create_task(_run())
    return {"task_id": task_id, "message": "Search started"}


@router.post("/add-url", summary="Add a single job by URL", response_model=JobListing)
async def add_job_url(
    payload: AddURLRequest,
    store: Store = Depends(get_store),
) -> JobListing:
    """
    Create a job listing from a raw URL. Minimal fields — user can fill in
    description via chat or a PATCH later. Fit scoring can be triggered
    separately (the orchestrator calls score_fit on demand).
    """
    ats_type = detect_ats_type(payload.url)
    # Infer source from ATS type if recognizable
    source = ats_type if ats_type != "universal" else "manual"
    job_id = make_id(source=source, apply_url=payload.url)

    data = {
        "id": job_id,
        "source": source,
        "ats_type": ats_type,
        "title": payload.title or "Untitled Role",
        "company": payload.company or "Unknown",
        "location": "",
        "remote_ok": False,
        "description": payload.description or "",
        "apply_url": payload.url,
        "status": "new",
    }
    job = store.upsert_job(data)
    log.info("jobs.added_by_url", extra={"job_id": job.id, "url": payload.url})
    return job


@router.get("/{job_id}", summary="Get full job detail", response_model=JobListing)
async def get_job(job_id: str, store: Store = Depends(get_store)) -> JobListing:
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail={"error": "job_not_found", "job_id": job_id})
    return job


@router.post("/{job_id}/queue", summary="Queue a job for application")
async def queue_job(job_id: str, store: Store = Depends(get_store)) -> dict[str, str]:
    if not store.get_job(job_id):
        raise HTTPException(status_code=404, detail={"error": "job_not_found"})
    store.update_job_status(job_id, "queued")
    return {"status": "queued", "job_id": job_id}


@router.post("/{job_id}/skip", summary="Skip a job")
async def skip_job(job_id: str, store: Store = Depends(get_store)) -> dict[str, str]:
    if not store.get_job(job_id):
        raise HTTPException(status_code=404, detail={"error": "job_not_found"})
    store.update_job_status(job_id, "skipped")
    return {"status": "skipped", "job_id": job_id}


@router.patch("/{job_id}/status", summary="Update job workflow status")
async def update_job_status(
    job_id: str,
    payload: StatusUpdate,
    store: Store = Depends(get_store),
) -> dict[str, str]:
    if not store.get_job(job_id):
        raise HTTPException(status_code=404, detail={"error": "job_not_found"})
    allowed = {"new", "queued", "skipped", "applying", "applied", "archived"}
    if payload.status not in allowed:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_status", "allowed": sorted(allowed)},
        )
    store.update_job_status(job_id, payload.status)
    return {"status": payload.status, "job_id": job_id}
