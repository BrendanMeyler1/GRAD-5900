"""
Development and test routes.

Provides endpoints to rapidly seed test cases, trigger internal cron loops
manually, or reset system states for rapid iteration.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from api.routes.jobs import add_job_by_url, AddByUrlRequest

router = APIRouter()
logger = logging.getLogger("job_finder.api.development")


class SeedResponse(BaseModel):
    status: str
    message: str
    seeded_jobs: list[str]


@router.post("/seed-queue", response_model=SeedResponse, summary="Seed Job Queue with test ATS links")
async def seed_job_queue() -> Any:
    """
    Rapidly populates the Decision Queue with 3 verified test jobs (Greenhouse, Lever).
    Uses the exact format the scraper natively provides to bypass manual URL hunting.
    """
    test_jobs = [
        "https://boards.greenhouse.io/riskified/jobs/7473724002",
        "https://careers.appsflyer.com/jobs/position/8403527002/backend-engineer/?gh_jid=&rd=1",
        "https://jobs.lever.co/leverdemo/ec4e6f40-3ef0-4c3e-bc52-df08c35fd1d3/apply"
    ]
    
    seeded = []
    
    for url in test_jobs:
        try:
            await add_job_by_url(request=AddByUrlRequest(url=url, use_llm=False))
            seeded.append(url)
        except Exception as e:
            logger.error(f"Failed to seed {url}: {e}")

    return {
        "status": "success",
        "message": f"Successfully seeded {len(seeded)} test jobs into the queue.",
        "seeded_jobs": seeded
    }
