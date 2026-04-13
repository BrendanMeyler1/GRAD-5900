"""
Batch routes for grouped approvals/submissions.

Endpoints:
    GET  /api/batch/candidates
    POST /api/batch/approve
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from api.routes import applications as applications_routes
from api.routes import jobs as jobs_routes

router = APIRouter()


def _company_name(listing: dict[str, Any]) -> str:
    company = listing.get("company")
    if isinstance(company, dict):
        return str(company.get("name") or "Unknown company")
    return str(company or "Unknown company")


def _role_title(listing: dict[str, Any]) -> str:
    role = listing.get("role")
    if isinstance(role, dict):
        return str(role.get("title") or "Untitled role")
    return str(listing.get("role_title") or "Untitled role")


class BatchApproveRequest(BaseModel):
    listing_ids: list[str] = Field(default_factory=list)
    submission_mode: Literal["dry_run", "shadow", "live"] = "shadow"
    run_now: bool = True
    use_browser_automation: bool = False
    headless: bool = True


@router.get("/batch/candidates", summary="Get batch-eligible listing groups")
async def get_batch_candidates(min_group_size: int = 2):
    queue_payload = await jobs_routes.get_jobs_queue()
    queue_items = queue_payload.get("queue", [])
    min_group_size = max(2, int(min_group_size))

    grouped: dict[str, dict[str, Any]] = {}
    for item in queue_items:
        listing_id = str(item.get("listing_id", "")).strip()
        if not listing_id:
            continue
        company = _company_name(item)
        role_title = _role_title(item)
        ats_type = str(item.get("ats_type") or "unknown")
        key = f"{company}::{role_title}::{ats_type}"
        if key not in grouped:
            grouped[key] = {
                "group_key": key,
                "company": company,
                "role_title": role_title,
                "ats_type": ats_type,
                "listing_ids": [],
                "count": 0,
            }
        grouped[key]["listing_ids"].append(listing_id)
        grouped[key]["count"] += 1

    candidates = [group for group in grouped.values() if group["count"] >= min_group_size]
    candidates.sort(key=lambda item: int(item["count"]), reverse=True)
    return {
        "status": "success",
        "count": len(candidates),
        "groups": candidates,
    }


@router.post("/batch/approve", summary="Batch approve multiple listings")
async def batch_approve(request: BatchApproveRequest):
    unique_listing_ids = list(dict.fromkeys([str(item).strip() for item in request.listing_ids if item]))
    results: list[dict[str, Any]] = []
    started = 0
    failed = 0

    for listing_id in unique_listing_ids:
        try:
            payload = await applications_routes.start_application(
                listing_id=listing_id,
                request=applications_routes.StartApplicationRequest(
                    submission_mode=request.submission_mode,
                    run_now=request.run_now,
                    use_browser_automation=request.use_browser_automation,
                    headless=request.headless,
                ),
            )
            results.append(
                {
                    "listing_id": listing_id,
                    "status": "started",
                    "application_id": payload.get("application_id"),
                    "workflow_status": payload.get("workflow_status"),
                }
            )
            started += 1
        except Exception as exc:
            results.append(
                {
                    "listing_id": listing_id,
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                }
            )
            failed += 1

    return {
        "status": "success",
        "requested": len(unique_listing_ids),
        "started": started,
        "failed": failed,
        "results": results,
    }
