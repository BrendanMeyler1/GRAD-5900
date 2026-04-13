"""
WebSocket routes for real-time updates.

Endpoints:
    WS /ws/application/{app_id}
    WS /ws/queue
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api.routes import applications as applications_routes
from api.routes import jobs as jobs_routes

router = APIRouter()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _application_snapshot(app: dict[str, Any]) -> dict[str, Any]:
    state = app.get("state", {})
    return {
        "type": "application_snapshot",
        "timestamp": _utc_now(),
        "application_id": app.get("application_id"),
        "listing_id": app.get("listing_id"),
        "status": app.get("status"),
        "status_history": app.get("status_history", []),
        "human_escalations": state.get("human_escalations", []),
        "post_upload_corrections": state.get("post_upload_corrections", []),
        "failure_record": state.get("failure_record"),
        # Include generated doc content so the dashboard updates in real-time
        "tailored_resume_final": state.get("tailored_resume_final"),
        "tailored_resume_tokenized": state.get("tailored_resume_tokenized"),
        "cover_letter_final": state.get("cover_letter_final"),
        "cover_letter_tokenized": state.get("cover_letter_tokenized"),
        "fields_filled": state.get("fields_filled", []),
        "screenshot_path": state.get("screenshot_path"),
    }


def _queue_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "queue_snapshot",
        "timestamp": _utc_now(),
        "last_scan_at": payload.get("last_scan_at"),
        "count": payload.get("count", 0),
        "queue": payload.get("queue", []),
    }


@router.websocket("/ws/application/{app_id}")
async def ws_application_updates(websocket: WebSocket, app_id: str):
    await websocket.accept()
    try:
        app = applications_routes.get_application_record(app_id)
        if app is None:
            await websocket.send_json(
                {
                    "type": "error",
                    "timestamp": _utc_now(),
                    "message": f"Application not found: {app_id}",
                }
            )
            await websocket.close(code=1008)
            return

        last_snapshot_key = None
        while True:
            app = applications_routes.get_application_record(app_id)
            if app is None:
                await websocket.send_json(
                    {
                        "type": "error",
                        "timestamp": _utc_now(),
                        "message": f"Application removed: {app_id}",
                    }
                )
                await websocket.close(code=1008)
                return

            # Detect any state mutation: status changes, docs generated, etc.
            status_len = len(app.get("status_history", []))
            updated_at = app.get("updated_at", "")
            state = app.get("state", {})
            has_docs = bool(state.get("tailored_resume_final") or state.get("tailored_resume_tokenized"))
            snapshot_key = (status_len, updated_at, app.get("status"), has_docs)

            if snapshot_key != last_snapshot_key:
                await websocket.send_json(_application_snapshot(app))
                last_snapshot_key = snapshot_key

            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        return


@router.websocket("/ws/queue")
async def ws_queue_updates(websocket: WebSocket):
    await websocket.accept()
    try:
        last_key = None
        while True:
            payload = await jobs_routes.get_jobs_queue()
            current_key = (payload.get("last_scan_at"), payload.get("count"))
            if current_key != last_key:
                await websocket.send_json(_queue_snapshot(payload))
                last_key = current_key
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        return
