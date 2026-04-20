"""
mcp_servers/jobs_server.py — MCP server exposing jobs + applications tools.

Tools:
    - list_jobs(status?, min_fit_score?, remote_only?, limit?)
    - get_job(job_id)
    - update_job_status(job_id, status)
    - list_applications(status?)
    - get_application(app_id)
    - get_application_memory(company)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from db.store import JobFilters, Store

log = logging.getLogger(__name__)


def build_jobs_server(store: Store) -> Any:
    """Construct the jobs MCP server tied to the given Store."""
    try:
        from mcp.server import Server
        from mcp.types import Tool, TextContent
    except ImportError:
        log.warning("mcp package not installed — returning stub jobs server")
        from mcp_servers.profile_server import _StubServer

        return _StubServer("jobs", _jobs_tool_registry(store))

    server = Server("job-finder-jobs")
    tools = _jobs_tool_registry(store)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name=name,
                description=spec["description"],
                inputSchema=spec["input_schema"],
            )
            for name, spec in tools.items()
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        spec = tools.get(name)
        if spec is None:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
        try:
            result = await spec["handler"](**(arguments or {}))
        except Exception as exc:  # noqa: BLE001
            log.exception("jobs_server.tool_error", extra={"tool": name})
            return [TextContent(type="text", text=json.dumps({"error": str(exc)}))]
        return [TextContent(type="text", text=json.dumps(result, default=str))]

    return server


def _jobs_tool_registry(store: Store) -> dict[str, dict[str, Any]]:
    async def list_jobs(
        status: str | None = None,
        min_fit_score: float | None = None,
        remote_only: bool = False,
        source: str | None = None,
        limit: int = 25,
    ) -> dict[str, Any]:
        filters = JobFilters(
            status=status,
            source=source,
            min_fit_score=min_fit_score,
            remote_only=remote_only,
            limit=min(limit, 100),
            sort_by="fit_score",
        )
        jobs = store.get_jobs(filters)
        return {
            "jobs": [
                {
                    "id": j.id,
                    "title": j.title,
                    "company": j.company,
                    "location": j.location,
                    "remote_ok": j.remote_ok,
                    "fit_score": j.fit_score,
                    "fit_summary": j.fit_summary,
                    "status": j.status,
                    "apply_url": j.apply_url,
                    "source": j.source,
                }
                for j in jobs
            ],
            "count": len(jobs),
        }

    async def get_job(job_id: str) -> dict[str, Any]:
        job = store.get_job(job_id)
        if job is None:
            return {"error": f"job {job_id} not found"}
        return json.loads(job.model_dump_json())

    async def update_job_status(job_id: str, status: str) -> dict[str, Any]:
        store.update_job_status(job_id, status)
        return {"updated": True, "job_id": job_id, "status": status}

    async def list_applications(status: str | None = None) -> dict[str, Any]:
        apps = store.list_applications(status=status)
        return {
            "applications": [
                {
                    "id": a.id,
                    "job_id": a.job_id,
                    "status": a.status,
                    "company": a.job.company if a.job else None,
                    "title": a.job.title if a.job else None,
                    "created_at": a.created_at,
                    "submitted_at": a.submitted_at,
                    "screenshot_count": len(a.shadow_screenshots or []),
                }
                for a in apps
            ],
            "count": len(apps),
        }

    async def get_application(app_id: str) -> dict[str, Any]:
        app = store.get_application(app_id)
        if app is None:
            return {"error": f"application {app_id} not found"}
        return json.loads(app.model_dump_json())

    async def get_application_memory(company: str) -> dict[str, Any]:
        mem = store.get_app_memory(company)
        if mem is None:
            return {"found": False}
        return {"found": True, "memory": json.loads(mem.model_dump_json())}

    return {
        "list_jobs": {
            "description": "List job listings with optional filters (status, min fit score, remote only, source). Returns up to 100.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "min_fit_score": {"type": "number", "minimum": 0, "maximum": 100},
                    "remote_only": {"type": "boolean"},
                    "source": {"type": "string"},
                    "limit": {"type": "integer", "default": 25},
                },
            },
            "handler": list_jobs,
        },
        "get_job": {
            "description": "Return full detail for a single job listing by ID.",
            "input_schema": {
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
            "handler": get_job,
        },
        "update_job_status": {
            "description": "Update a job's workflow status (new|queued|reviewing|applied|skipped).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "status": {"type": "string"},
                },
                "required": ["job_id", "status"],
            },
            "handler": update_job_status,
        },
        "list_applications": {
            "description": "List applications, optionally filtered by status.",
            "input_schema": {
                "type": "object",
                "properties": {"status": {"type": "string"}},
            },
            "handler": list_applications,
        },
        "get_application": {
            "description": "Return full detail for a single application, including tailored documents and screenshots.",
            "input_schema": {
                "type": "object",
                "properties": {"app_id": {"type": "string"}},
                "required": ["app_id"],
            },
            "handler": get_application,
        },
        "get_application_memory": {
            "description": "Return stored notes about past applications to this company (what worked, what didn't, form quirks).",
            "input_schema": {
                "type": "object",
                "properties": {"company": {"type": "string"}},
                "required": ["company"],
            },
            "handler": get_application_memory,
        },
    }
