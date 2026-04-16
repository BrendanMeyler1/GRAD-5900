"""
mcp_servers/profile_server.py — MCP server exposing user profile tools.

Tools:
    - get_profile()          → returns FullProfile as JSON
    - get_resume_text()      → returns raw resume text
    - update_profile(fields) → updates top-level user_profile fields
    - add_qa_note(question, answer, category) → stores a Q&A note
    - list_qa_notes(category?) → returns existing Q&A notes
    - get_profile_completeness() → returns {pct, missing_fields}

Built with the official `mcp` Python SDK. The server uses stdio transport
by default (spawned as a subprocess by an MCP client). For development,
we simply expose a builder function; the FastAPI lifespan decides how
to run it.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from db.store import Store

log = logging.getLogger(__name__)


def build_profile_server(store: Store) -> Any:
    """
    Construct an MCP Server instance wired to the given Store.

    Returns the Server object (not yet running). Callers attach it to a
    transport — typically `mcp.server.stdio.stdio_server()`.

    If the `mcp` package is unavailable (e.g. in tests), returns a stub
    object whose tools can still be called programmatically.
    """
    try:
        from mcp.server import Server
        from mcp.types import Tool, TextContent
    except ImportError:
        log.warning("mcp package not installed — returning stub profile server")
        return _StubServer("profile", _profile_tool_registry(store))

    server = Server("job-finder-profile")
    tools = _profile_tool_registry(store)

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
            log.exception("profile_server.tool_error", extra={"tool": name})
            return [TextContent(type="text", text=json.dumps({"error": str(exc)}))]
        return [TextContent(type="text", text=json.dumps(result, default=str))]

    return server


def _profile_tool_registry(store: Store) -> dict[str, dict[str, Any]]:
    """Build a dict of tool_name → {description, input_schema, handler}."""

    async def get_profile() -> dict[str, Any]:
        profile = store.get_full_profile()
        return json.loads(profile.model_dump_json())

    async def get_resume_text() -> dict[str, Any]:
        profile = store.get_profile()
        if profile is None:
            return {"text": None, "present": False}
        return {"text": profile.resume_raw_text, "present": bool(profile.resume_raw_text)}

    async def update_profile(fields: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(fields, dict) or not fields:
            return {"updated": False, "reason": "no fields provided"}
        updated = store.upsert_profile(fields)
        return {"updated": True, "profile": json.loads(updated.model_dump_json())}

    async def add_qa_note(
        question: str, answer: str, category: str | None = None
    ) -> dict[str, Any]:
        qa = store.add_qa(question=question, answer=answer, category=category)
        return {"saved": True, "id": qa.id}

    async def list_qa_notes(category: str | None = None) -> dict[str, Any]:
        notes = store.get_qa(category=category)
        return {
            "notes": [
                {"id": n.id, "question": n.question, "answer": n.answer, "category": n.category}
                for n in notes
            ]
        }

    async def get_profile_completeness() -> dict[str, Any]:
        profile = store.get_full_profile()
        p = profile.profile
        missing: list[str] = []
        if not p.first_name or not p.last_name:
            missing.append("name")
        if not p.email:
            missing.append("email")
        if not p.phone:
            missing.append("phone")
        if p.target_salary_min is None:
            missing.append("target_salary_min")
        if not p.remote_preference:
            missing.append("remote_preference")
        if not profile.experience:
            missing.append("experience")
        return {
            "completion_pct": profile.completion_pct,
            "missing_fields": missing,
            "can_apply": profile.is_complete_enough,
        }

    return {
        "get_profile": {
            "description": "Return the complete user profile (personal info, education, experience, skills, preferences, Q&A).",
            "input_schema": {"type": "object", "properties": {}},
            "handler": get_profile,
        },
        "get_resume_text": {
            "description": "Return the raw text of the user's uploaded resume.",
            "input_schema": {"type": "object", "properties": {}},
            "handler": get_resume_text,
        },
        "update_profile": {
            "description": "Update top-level profile fields. Accepts a partial fields dict.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "fields": {
                        "type": "object",
                        "description": "Map of profile field name to new value",
                    }
                },
                "required": ["fields"],
            },
            "handler": update_profile,
        },
        "add_qa_note": {
            "description": "Save a question-answer pair to the user's profile notes.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "answer": {"type": "string"},
                    "category": {"type": "string", "enum": ["preference", "experience", "background", "other"]},
                },
                "required": ["question", "answer"],
            },
            "handler": add_qa_note,
        },
        "list_qa_notes": {
            "description": "List saved Q&A notes, optionally filtered by category.",
            "input_schema": {
                "type": "object",
                "properties": {"category": {"type": "string"}},
            },
            "handler": list_qa_notes,
        },
        "get_profile_completeness": {
            "description": "Return profile completion percentage and list of missing required fields.",
            "input_schema": {"type": "object", "properties": {}},
            "handler": get_profile_completeness,
        },
    }


class _StubServer:
    """Fallback server object used when `mcp` is not installed."""

    def __init__(self, name: str, tools: dict[str, dict[str, Any]]) -> None:
        self.name = name
        self.tools = tools

    async def call(self, tool: str, **kwargs: Any) -> Any:
        spec = self.tools.get(tool)
        if spec is None:
            raise ValueError(f"Unknown tool: {tool}")
        return await spec["handler"](**kwargs)

    def list_tools(self) -> list[str]:
        return list(self.tools.keys())
