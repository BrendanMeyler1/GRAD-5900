"""
api/tasks.py — In-process background task registry.

When the UI triggers a long-running task (shadow apply, job search,
email sync), the route returns a `task_id` immediately. The client
polls `/api/tasks/{id}` until status != "running".

This is deliberately in-memory: the app is single-user and single-process.
If the server restarts, in-flight tasks are lost — routes that matter
write intermediate DB state so nothing is silently dropped (e.g. the
pipeline creates the Application row before starting the browser).

Thread safety: fine with asyncio.Lock because we only mutate in async
code. The registry itself is a plain dict protected by the lock.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

log = logging.getLogger(__name__)

TaskState = Literal["running", "completed", "failed", "cancelled"]


@dataclass
class TaskStatus:
    """Snapshot of one background task."""

    task_id: str
    description: str
    status: TaskState
    progress: str = ""  # free-form human-readable message
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str = field(default_factory=lambda: _now())
    updated_at: str = field(default_factory=lambda: _now())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TaskRegistry:
    """
    Tracks every background task created by the API.

    Usage:
        task_id = registry.create("Shadow apply for Stripe")
        # ... in background ...
        registry.update(task_id, progress="tailoring resume")
        # later
        registry.complete(task_id, result={"app_id": "..."})
        # or
        registry.fail(task_id, error="browser crash")
    """

    def __init__(self) -> None:
        self._tasks: dict[str, TaskStatus] = {}
        self._lock = asyncio.Lock()

    async def create(self, description: str) -> str:
        task_id = str(uuid.uuid4())
        async with self._lock:
            self._tasks[task_id] = TaskStatus(
                task_id=task_id,
                description=description,
                status="running",
            )
        log.info("task.created", extra={"task_id": task_id, "description": description})
        return task_id

    async def update(self, task_id: str, progress: str) -> None:
        async with self._lock:
            t = self._tasks.get(task_id)
            if not t:
                return
            t.progress = progress
            t.updated_at = _now()
        log.debug("task.progress", extra={"task_id": task_id, "progress": progress})

    async def complete(self, task_id: str, result: dict[str, Any] | None = None) -> None:
        async with self._lock:
            t = self._tasks.get(task_id)
            if not t:
                return
            t.status = "completed"
            t.result = result
            t.updated_at = _now()
        log.info("task.completed", extra={"task_id": task_id})

    async def fail(self, task_id: str, error: str) -> None:
        async with self._lock:
            t = self._tasks.get(task_id)
            if not t:
                return
            t.status = "failed"
            t.error = error
            t.updated_at = _now()
        log.warning("task.failed", extra={"task_id": task_id, "error": error})

    async def get(self, task_id: str) -> TaskStatus | None:
        async with self._lock:
            return self._tasks.get(task_id)

    async def list_all(self, limit: int = 50) -> list[TaskStatus]:
        async with self._lock:
            tasks = list(self._tasks.values())
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks[:limit]

    async def cleanup_old(self, max_age_hours: int = 24) -> int:
        """Drop finished tasks older than `max_age_hours`. Returns count removed."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        removed = 0
        async with self._lock:
            for tid in list(self._tasks.keys()):
                t = self._tasks[tid]
                if t.status == "running":
                    continue
                try:
                    ts = datetime.fromisoformat(t.updated_at)
                except ValueError:
                    continue
                if ts < cutoff:
                    del self._tasks[tid]
                    removed += 1
        if removed:
            log.info("task.cleanup", extra={"removed": removed})
        return removed


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Module-level singleton — imported from routes
registry = TaskRegistry()
