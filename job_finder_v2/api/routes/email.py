"""
api/routes/email.py — Outlook IMAP sync + event feed.

Endpoints:
    POST /api/email/sync                 → trigger IMAP scan (background)
    GET  /api/email/events               → all classified events (Apply view chips)
    GET  /api/email/events/{app_id}      → events for a specific application
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from api.dependencies import get_email_tracker, get_store
from api.tasks import registry
from config import settings
from db.store import EmailEvent, Store

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/sync", summary="Trigger IMAP sync")
async def sync_email(
    since_days: int = Query(7, ge=1, le=90),
    email_tracker=Depends(get_email_tracker),
) -> dict[str, str]:
    """
    Kick off an IMAP sync in the background. Returns a task_id to poll.
    """
    if not settings.email_configured:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "email_not_configured",
                "message": "Set OUTLOOK_EMAIL and OUTLOOK_APP_PASSWORD in .env to enable email tracking.",
            },
        )

    task_id = await registry.create(f"Email sync (last {since_days} days)")

    async def _run() -> None:
        try:
            await registry.update(task_id, progress="scanning inbox")
            events = await email_tracker.sync(since_days=since_days)
            await registry.complete(
                task_id,
                result={
                    "events": len(events),
                    "action_needed": sum(1 for e in events if e.action_needed),
                },
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("email.sync_failed", extra={"error": str(exc)})
            await registry.fail(task_id, error=str(exc))

    asyncio.create_task(_run())
    return {"task_id": task_id, "message": "Email sync started"}


@router.get("/events", summary="List classified email events", response_model=list[EmailEvent])
async def list_events(
    action_needed: bool | None = None,
    limit: int = Query(50, ge=1, le=500),
    store: Store = Depends(get_store),
) -> list[EmailEvent]:
    """Return email events, optionally filtered by action_needed=true."""
    return store.get_email_events(action_needed=action_needed, limit=limit)


@router.get(
    "/events/{app_id}",
    summary="Email thread for a specific application",
    response_model=list[EmailEvent],
)
async def events_for_app(
    app_id: str, store: Store = Depends(get_store)
) -> list[EmailEvent]:
    """All email events matched to a single application (newest first)."""
    if not store.get_application(app_id):
        raise HTTPException(status_code=404, detail={"error": "application_not_found"})
    return store.get_email_events(app_id=app_id, limit=200)
