"""
api/routes/tasks.py — Background task status endpoints.

The dashboard polls /api/tasks/{id} after starting a long-running action
(shadow apply, job search). Returns the current TaskStatus dict.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.tasks import registry

router = APIRouter()


@router.get("/{task_id}", summary="Get status for a background task")
async def get_task(task_id: str) -> dict:
    """Return current status, progress, and result (if complete) for a task."""
    t = await registry.get(task_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"error": "task_not_found", "task_id": task_id})
    return t.to_dict()


@router.get("", summary="List all tracked tasks (most recent first)")
async def list_tasks(limit: int = 50) -> dict:
    tasks = await registry.list_all(limit=limit)
    return {"tasks": [t.to_dict() for t in tasks], "count": len(tasks)}
