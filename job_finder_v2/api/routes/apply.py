"""
api/routes/apply.py — Shadow + live application pipeline.

The apply routes are the heart of the human-in-the-loop story.

Flow:
    1. User clicks "Shadow Apply" → POST /api/apply/{job_id}/shadow
       Returns task_id immediately. The pipeline runs in background:
       tailor resume + cover letter → fill form → take screenshots → status=shadow_review.
    2. User reviews in UI → POST /api/apply/{app_id}/approve
       Pipeline re-runs in live mode; submits for real.
    3. User can abort at any time → POST /api/apply/{app_id}/abort.

Endpoints:
    POST /api/apply/{job_id}/shadow        → start shadow run (task_id)
    POST /api/apply/{app_id}/approve       → submit live
    POST /api/apply/{app_id}/abort         → mark skipped
    GET  /api/apply/{app_id}               → full application record (includes job)
    GET  /api/apply/{app_id}/screenshots   → list screenshot paths
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from api.dependencies import get_pipeline, get_store
from api.tasks import registry
from db.store import Application, Store

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/{job_id}/shadow", summary="Start a shadow application (no submit)")
async def start_shadow(
    job_id: str,
    store: Store = Depends(get_store),
    pipeline=Depends(get_pipeline),
) -> dict[str, str]:
    """
    Begin the shadow pipeline for a job. Returns a task_id immediately —
    the UI polls /api/tasks/{task_id} for progress.

    Pre-flight checks:
      - Job must exist
      - Profile must have minimum required fields (name, email, phone)
    """
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail={"error": "job_not_found", "job_id": job_id})

    profile = store.get_full_profile()
    if not profile.is_complete_enough:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "profile_incomplete",
                "message": "Profile missing required fields (name, email, phone).",
                "completion_pct": profile.completion_pct,
            },
        )

    # Guard: don't start a duplicate shadow run if one is already in progress or ready for review
    all_apps = store.list_applications()
    for existing in all_apps:
        if existing.job_id == job_id and existing.status in {
            "shadow_running",
            "shadow_review",
            "submitting",
        }:
            return {
                "task_id": "",
                "job_id": job_id,
                "message": f"Shadow application already {existing.status} (app_id={existing.id}). Review it in the Apply tab.",
                "app_id": existing.id,
                "already_running": True,
            }

    task_id = await registry.create(f"Shadow apply: {job.company} — {job.title}")

    async def _run() -> None:
        try:
            await registry.update(task_id, progress="tailoring resume + filling form")
            app = await pipeline.run_application(job_id=job_id, mode="shadow")
            await registry.complete(
                task_id,
                result={
                    "app_id": app.id,
                    "status": app.status,
                    "screenshots": len(app.shadow_screenshots),
                    "company": job.company,
                    "title": job.title,
                },
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("apply.shadow.failed", extra={"job_id": job_id, "error": str(exc)})
            await registry.fail(task_id, error=str(exc))

    asyncio.create_task(_run())
    log.info("apply.shadow.started", extra={"job_id": job_id, "task_id": task_id})
    return {"task_id": task_id, "job_id": job_id, "message": "Shadow application started"}


@router.post("/{app_id}/approve", summary="Approve and submit a shadow application live")
async def approve_application(
    app_id: str,
    store: Store = Depends(get_store),
    pipeline=Depends(get_pipeline),
) -> dict[str, str]:
    """
    Take an application in shadow_review state and run it live. The pipeline
    re-fills the form (some ATSes invalidate the session between shadow and
    live) and clicks submit.
    """
    app = store.get_application(app_id)
    if not app:
        raise HTTPException(status_code=404, detail={"error": "application_not_found"})
    if app.status not in {"shadow_review", "awaiting_approval"}:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_state",
                "message": f"Cannot approve application in status '{app.status}'",
                "current_status": app.status,
            },
        )

    task_id = await registry.create(f"Live submit: {app.job.company if app.job else app_id}")

    async def _run() -> None:
        try:
            await registry.update(task_id, progress="submitting live")
            updated = await pipeline.run_application(
                job_id=app.job_id, mode="live", existing_app_id=app_id
            )
            await registry.complete(
                task_id,
                result={
                    "app_id": updated.id,
                    "status": updated.status,
                    "submitted_at": updated.submitted_at,
                },
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("apply.approve.failed", extra={"app_id": app_id, "error": str(exc)})
            await registry.fail(task_id, error=str(exc))

    asyncio.create_task(_run())
    log.info("apply.approve.started", extra={"app_id": app_id, "task_id": task_id})
    return {"task_id": task_id, "app_id": app_id, "message": "Live submission started"}


@router.post("/{app_id}/abort", summary="Abort an application")
async def abort_application(
    app_id: str, store: Store = Depends(get_store)
) -> dict[str, str]:
    """Mark an application as aborted/skipped. Does not delete — history preserved."""
    app = store.get_application(app_id)
    if not app:
        raise HTTPException(status_code=404, detail={"error": "application_not_found"})
    store.update_application(app_id, status="skipped")
    log.info("apply.aborted", extra={"app_id": app_id})
    return {"status": "skipped", "app_id": app_id}


@router.get("/{app_id}", summary="Get full application state", response_model=Application)
async def get_application(
    app_id: str, store: Store = Depends(get_store)
) -> Application:
    """Return the application including hydrated job, tailored resume, and screenshots."""
    app = store.get_application(app_id)
    if not app:
        raise HTTPException(status_code=404, detail={"error": "application_not_found"})
    return app


@router.get("/{app_id}/screenshots", summary="List screenshot URLs for an application")
async def list_screenshots(
    app_id: str, store: Store = Depends(get_store)
) -> dict[str, list[str]]:
    """
    Return browser-relative URLs for screenshots served via /static/screenshots.

    File paths on disk → HTTP URLs the UI can embed directly in <img>.
    """
    app = store.get_application(app_id)
    if not app:
        raise HTTPException(status_code=404, detail={"error": "application_not_found"})

    urls: list[str] = []
    for path_str in app.shadow_screenshots:
        p = Path(path_str)
        # Screenshots are stored in {screenshots_dir}/{app_id}/step_XX.png
        # Static mount serves {screenshots_dir}, so URL needs {app_id}/{filename}
        urls.append(f"/static/screenshots/{app_id}/{p.name}")
    return {"screenshots": urls, "count": len(urls)}


@router.get("/{app_id}/screenshot/{name}", summary="Serve a single screenshot image")
async def get_screenshot(
    app_id: str, name: str, store: Store = Depends(get_store)
) -> FileResponse:
    """
    Serve a specific screenshot. Verifies the file belongs to the application
    (no path traversal, no leaking other apps' screenshots).
    """
    app = store.get_application(app_id)
    if not app:
        raise HTTPException(status_code=404, detail={"error": "application_not_found"})

    target = None
    for path_str in app.shadow_screenshots:
        p = Path(path_str)
        # Match by app_id/filename (e.g. "abc123/step_01.png") or just filename
        rel = f"{app_id}/{p.name}"
        if p.name == name or rel == name:
            target = p
            break
    if target is None or not target.exists():
        raise HTTPException(status_code=404, detail={"error": "screenshot_not_found"})
    return FileResponse(str(target), media_type="image/png")
