"""
api/routes/applications.py — Application history + review endpoints.

Powers the Apply view's kanban columns and the ReviewPanel.

Endpoints:
    GET    /api/applications              → all applications (with job hydrated)
    GET    /api/applications/pending      → only shadow_review + awaiting_approval
    GET    /api/applications/{app_id}     → full application detail
    PATCH  /api/applications/{app_id}     → update notes, tailored text, custom_qa
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.dependencies import get_store
from db.store import Application, Store

log = logging.getLogger(__name__)
router = APIRouter()


class ApplicationUpdate(BaseModel):
    """Fields the human reviewer can edit during review."""

    human_notes: str | None = None
    resume_tailored_text: str | None = None
    cover_letter_text: str | None = None
    custom_qa: dict[str, str] | None = None
    status: str | None = None


@router.get("", summary="List all applications", response_model=list[Application])
async def list_applications(
    status: str | None = None, store: Store = Depends(get_store)
) -> list[Application]:
    """Return applications, newest first. Optionally filter by status."""
    return store.list_applications(status=status)


@router.get("/pending", summary="List applications awaiting review", response_model=list[Application])
async def list_pending(store: Store = Depends(get_store)) -> list[Application]:
    """Applications in shadow_review or awaiting_approval state — what the human needs to act on."""
    pending = []
    for status in ("shadow_review", "awaiting_approval"):
        pending.extend(store.list_applications(status=status))
    return pending


@router.get("/{app_id}", summary="Get application detail", response_model=Application)
async def get_application(
    app_id: str, store: Store = Depends(get_store)
) -> Application:
    app = store.get_application(app_id)
    if not app:
        raise HTTPException(status_code=404, detail={"error": "application_not_found"})
    return app


@router.patch("/{app_id}", summary="Update application fields", response_model=Application)
async def update_application(
    app_id: str,
    update: ApplicationUpdate,
    store: Store = Depends(get_store),
) -> Application:
    """
    Save edits made in the ReviewPanel — cover letter tweaks, resume adjustments,
    custom Q&A answers, human notes.
    """
    existing = store.get_application(app_id)
    if not existing:
        raise HTTPException(status_code=404, detail={"error": "application_not_found"})

    fields = {k: v for k, v in update.model_dump(exclude_unset=True).items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail={"error": "no_fields"})

    if "status" in fields:
        allowed = {
            "pending", "shadow_review", "awaiting_approval", "submitted",
            "skipped", "failed", "interview_scheduled", "rejected", "offer_received",
        }
        if fields["status"] not in allowed:
            raise HTTPException(
                status_code=400,
                detail={"error": "invalid_status", "allowed": sorted(allowed)},
            )

    updated = store.update_application(app_id, **fields)
    log.info("applications.updated", extra={"app_id": app_id, "fields": list(fields.keys())})
    return updated


@router.delete(
    "",
    summary="Bulk-delete applications by status",
    description=(
        "Hard-delete all applications whose status matches any of the provided "
        "`status` query params. Repeat the param to clear multiple columns at once "
        "(e.g. `?status=shadow_review&status=failed`). "
        "Returns the number of records removed."
    ),
)
async def bulk_delete_applications(
    status: list[str] = Query(default=[]),
    store: Store = Depends(get_store),
) -> dict[str, int]:
    """Remove all applications with the given status(es) — used by the 'Clear all' button."""
    count = store.delete_applications_by_statuses(status)
    log.info("applications.bulk_deleted", extra={"statuses": status, "count": count})
    return {"deleted": count}


@router.delete(
    "/{app_id}",
    summary="Delete a single application",
    description="Hard-delete one application record. Used by the per-card × dismiss button.",
)
async def delete_application(
    app_id: str,
    store: Store = Depends(get_store),
) -> dict[str, str | bool]:
    """Remove a single application. Irreversible."""
    deleted = store.delete_application(app_id)
    if not deleted:
        raise HTTPException(status_code=404, detail={"error": "application_not_found"})
    log.info("applications.deleted", extra={"app_id": app_id})
    return {"deleted": True, "app_id": app_id}
