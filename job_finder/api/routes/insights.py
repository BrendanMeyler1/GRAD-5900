"""
Insights routes for funnel metrics and failure patterns.

Endpoints:
    GET /api/insights/overview
    GET /api/insights/failures
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from api.routes import applications as applications_routes
from api.routes import jobs as jobs_routes
from feedback.failures_store import FailureStore

router = APIRouter()


def _safe_rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100.0, 2)


@router.get("/insights/overview", summary="Funnel metrics and success rates")
async def get_insights_overview():
    applications_payload = await applications_routes.list_applications()
    apps = applications_payload.get("applications", [])

    queue_payload = await jobs_routes.get_jobs_queue()
    queue_count = int(queue_payload.get("count") or 0)

    status_counts: dict[str, int] = {}
    for app in apps:
        status = str(app.get("status") or "UNKNOWN").upper()
        status_counts[status] = status_counts.get(status, 0) + 1

    total = len(apps)
    submitted_like = (
        status_counts.get("SUBMITTED", 0)
        + status_counts.get("RECEIVED", 0)
        + status_counts.get("INTERVIEW_SCHEDULED", 0)
        + status_counts.get("OFFER", 0)
        + status_counts.get("REJECTED", 0)
    )
    interviews = status_counts.get("INTERVIEW_SCHEDULED", 0)
    offers = status_counts.get("OFFER", 0)
    rejected = status_counts.get("REJECTED", 0)
    failed = status_counts.get("FAILED", 0)

    return {
        "status": "success",
        "overview": {
            "queue_count": queue_count,
            "application_count": total,
            "submitted_like_count": submitted_like,
            "interview_count": interviews,
            "offer_count": offers,
            "rejected_count": rejected,
            "failed_count": failed,
            "submission_rate_pct": _safe_rate(submitted_like, total),
            "interview_rate_pct": _safe_rate(interviews, submitted_like),
            "offer_rate_pct": _safe_rate(offers, submitted_like),
        },
        "status_breakdown": status_counts,
    }


@router.get("/insights/failures", summary="Top failure patterns from failures.db")
async def get_insights_failures(limit: int = 10, include_recent: bool = False):
    limit = max(1, min(100, int(limit)))
    store = FailureStore()
    patterns = store.top_failure_patterns(limit=limit)
    payload: dict[str, Any] = {
        "status": "success",
        "limit": limit,
        "patterns": patterns,
    }
    if include_recent:
        payload["recent"] = store.list_recent(limit=limit)
    return payload
