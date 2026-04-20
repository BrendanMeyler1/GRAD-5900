"""
mcp_servers/files_server.py — MCP server for tailored documents + screenshots.

Tools:
    - read_tailored_resume(app_id)   → markdown
    - write_tailored_resume(app_id, content)
    - read_cover_letter(app_id)
    - write_cover_letter(app_id, content)
    - list_screenshots(app_id)       → list of absolute paths
    - get_fill_log(app_id)           → structured action log
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from config import settings
from db.store import Store

log = logging.getLogger(__name__)


def build_files_server(store: Store) -> Any:
    """Construct the files MCP server tied to the given Store."""
    try:
        from mcp.server import Server
        from mcp.types import Tool, TextContent
    except ImportError:
        log.warning("mcp package not installed — returning stub files server")
        from mcp_servers.profile_server import _StubServer

        return _StubServer("files", _files_tool_registry(store))

    server = Server("job-finder-files")
    tools = _files_tool_registry(store)

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
            log.exception("files_server.tool_error", extra={"tool": name})
            return [TextContent(type="text", text=json.dumps({"error": str(exc)}))]
        return [TextContent(type="text", text=json.dumps(result, default=str))]

    return server


def _files_tool_registry(store: Store) -> dict[str, dict[str, Any]]:
    gen_dir = Path(settings.generated_dir)

    async def read_tailored_resume(app_id: str) -> dict[str, Any]:
        app = store.get_application(app_id)
        if app is None:
            return {"error": f"application {app_id} not found"}
        md_path = gen_dir / app_id / "resume.md"
        if md_path.exists():
            return {"content": md_path.read_text(encoding="utf-8"), "path": str(md_path)}
        return {"content": app.resume_tailored_text or "", "path": str(md_path)}

    async def write_tailored_resume(app_id: str, content: str) -> dict[str, Any]:
        app = store.get_application(app_id)
        if app is None:
            return {"error": f"application {app_id} not found"}
        md_path = gen_dir / app_id / "resume.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(content, encoding="utf-8")
        store.update_application(app_id, resume_tailored_text=content)
        # Re-render PDF
        try:
            from utils.pdf import export_resume_pdf

            pdf_path = export_resume_pdf(app_id, content, generated_dir=settings.generated_dir)
            store.update_application(app_id, resume_tailored_path=pdf_path)
        except Exception as exc:  # noqa: BLE001
            log.warning("files_server.pdf_regen_failed", extra={"error": str(exc)})
            pdf_path = None
        return {"written": True, "md_path": str(md_path), "pdf_path": pdf_path}

    async def read_cover_letter(app_id: str) -> dict[str, Any]:
        app = store.get_application(app_id)
        if app is None:
            return {"error": f"application {app_id} not found"}
        md_path = gen_dir / app_id / "cover_letter.md"
        if md_path.exists():
            return {"content": md_path.read_text(encoding="utf-8"), "path": str(md_path)}
        return {"content": app.cover_letter_text or "", "path": str(md_path)}

    async def write_cover_letter(app_id: str, content: str) -> dict[str, Any]:
        app = store.get_application(app_id)
        if app is None:
            return {"error": f"application {app_id} not found"}
        md_path = gen_dir / app_id / "cover_letter.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(content, encoding="utf-8")
        store.update_application(app_id, cover_letter_text=content)
        return {"written": True, "md_path": str(md_path)}

    async def list_screenshots(app_id: str) -> dict[str, Any]:
        app = store.get_application(app_id)
        if app is None:
            return {"error": f"application {app_id} not found"}
        paths = app.shadow_screenshots or []
        return {"screenshots": paths, "count": len(paths)}

    async def get_fill_log(app_id: str) -> dict[str, Any]:
        app = store.get_application(app_id)
        if app is None:
            return {"error": f"application {app_id} not found"}
        return {"fill_log": app.fill_log or [], "custom_qa": app.custom_qa or {}}

    return {
        "read_tailored_resume": {
            "description": "Read the tailored resume markdown for an application.",
            "input_schema": {
                "type": "object",
                "properties": {"app_id": {"type": "string"}},
                "required": ["app_id"],
            },
            "handler": read_tailored_resume,
        },
        "write_tailored_resume": {
            "description": "Overwrite the tailored resume markdown for an application and regenerate the PDF.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "app_id": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["app_id", "content"],
            },
            "handler": write_tailored_resume,
        },
        "read_cover_letter": {
            "description": "Read the cover letter text for an application.",
            "input_schema": {
                "type": "object",
                "properties": {"app_id": {"type": "string"}},
                "required": ["app_id"],
            },
            "handler": read_cover_letter,
        },
        "write_cover_letter": {
            "description": "Overwrite the cover letter text for an application.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "app_id": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["app_id", "content"],
            },
            "handler": write_cover_letter,
        },
        "list_screenshots": {
            "description": "Return the list of screenshot file paths captured during a shadow/live run.",
            "input_schema": {
                "type": "object",
                "properties": {"app_id": {"type": "string"}},
                "required": ["app_id"],
            },
            "handler": list_screenshots,
        },
        "get_fill_log": {
            "description": "Return the structured action log + any custom Q&A generated during form fill.",
            "input_schema": {
                "type": "object",
                "properties": {"app_id": {"type": "string"}},
                "required": ["app_id"],
            },
            "handler": get_fill_log,
        },
    }
