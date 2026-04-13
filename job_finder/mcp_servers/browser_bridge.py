"""
Browser MCP bridge (Phase 2.5).

Exposes structured browser operations through an MCP-friendly façade.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from browser.humanizer import Humanizer
from browser.playwright_driver import PlaywrightDriver


class BrowserBridgeMCPServer:
    """Bridge Playwright browser actions into MCP-style tool methods."""

    def __init__(
        self,
        driver_factory: Callable[..., PlaywrightDriver] | None = None,
    ) -> None:
        self._driver_factory = driver_factory or (
            lambda **kwargs: PlaywrightDriver(**kwargs)
        )
        self._sessions: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _run(coro):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        raise RuntimeError(
            "BrowserBridgeMCPServer sync methods cannot run inside an active event loop."
        )

    def _get_session(self, session_id: str) -> dict[str, Any]:
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError(f"Unknown browser session: {session_id}")
        return session

    def start_session(
        self,
        headless: bool = True,
        use_humanizer: bool = True,
    ) -> dict[str, Any]:
        """Start a browser session and return metadata."""
        session_id = str(uuid4())
        driver = self._driver_factory(
            headless=headless,
            humanizer=Humanizer() if use_humanizer else None,
        )
        self._run(driver.start())
        self._sessions[session_id] = {
            "driver": driver,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "headless": headless,
        }
        return {
            "session_id": session_id,
            "headless": headless,
            "status": "started",
        }

    def stop_session(self, session_id: str) -> dict[str, Any]:
        """Stop a browser session."""
        session = self._get_session(session_id)
        self._run(session["driver"].stop())
        self._sessions.pop(session_id, None)
        return {"session_id": session_id, "status": "stopped"}

    def list_sessions(self) -> list[dict[str, Any]]:
        """List currently active sessions."""
        rows = []
        for sid, info in self._sessions.items():
            rows.append(
                {
                    "session_id": sid,
                    "created_at": info.get("created_at"),
                    "headless": info.get("headless", True),
                }
            )
        rows.sort(key=lambda row: str(row.get("created_at", "")))
        return rows

    def navigate(self, session_id: str, url: str) -> dict[str, Any]:
        """Navigate current page."""
        session = self._get_session(session_id)
        result = self._run(session["driver"].goto(url))
        result["session_id"] = session_id
        result["status"] = "navigated"
        return result

    def get_dom_tree(
        self,
        session_id: str,
        form_selector: str = "form",
    ) -> dict[str, Any]:
        """Fetch structured DOM snapshot for form interpretation."""
        session = self._get_session(session_id)
        result = self._run(session["driver"].get_dom_snapshot(form_selector=form_selector))
        result["session_id"] = session_id
        return result

    def fill_field(
        self,
        session_id: str,
        selector: str,
        value: Any,
        use_humanizer: bool = True,
    ) -> dict[str, Any]:
        """Fill a field in the active page."""
        session = self._get_session(session_id)
        result = self._run(
            session["driver"].fill_field(
                selector=selector,
                value=value,
                use_humanizer=use_humanizer,
            )
        )
        result["session_id"] = session_id
        return result

    def upload_file(self, session_id: str, selector: str, file_path: str) -> dict[str, Any]:
        """Upload a file through the active page."""
        session = self._get_session(session_id)
        result = self._run(session["driver"].upload_file(selector=selector, file_path=file_path))
        result["session_id"] = session_id
        return result

    def click(self, session_id: str, selector: str) -> dict[str, Any]:
        """Click an element."""
        session = self._get_session(session_id)
        result = self._run(session["driver"].click(selector=selector))
        result["session_id"] = session_id
        return result

    def screenshot(
        self,
        session_id: str,
        path: str,
        full_page: bool = True,
    ) -> dict[str, Any]:
        """Save a screenshot to disk."""
        session = self._get_session(session_id)
        result = self._run(
            session["driver"].screenshot(path=path, full_page=full_page)
        )
        result["session_id"] = session_id
        return result

    def describe(self) -> dict[str, Any]:
        """Return metadata."""
        return {
            "server": "browser_bridge_mcp",
            "capabilities": [
                "start_session",
                "stop_session",
                "list_sessions",
                "navigate",
                "get_dom_tree",
                "fill_field",
                "upload_file",
                "click",
                "screenshot",
            ],
            "active_sessions": len(self._sessions),
        }


def build_fastmcp_app():
    """Optional FastMCP app factory."""
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception as exc:  # pragma: no cover - depends on runtime package API
        raise RuntimeError(
            "FastMCP runtime not available. Install/upgrade `mcp` package."
        ) from exc

    service = BrowserBridgeMCPServer()
    app = FastMCP("job_finder_browser_bridge")

    @app.tool()
    def start_session(headless: bool = True, use_humanizer: bool = True) -> dict[str, Any]:
        return service.start_session(headless=headless, use_humanizer=use_humanizer)

    @app.tool()
    def stop_session(session_id: str) -> dict[str, Any]:
        return service.stop_session(session_id=session_id)

    @app.tool()
    def list_sessions() -> list[dict[str, Any]]:
        return service.list_sessions()

    @app.tool()
    def navigate(session_id: str, url: str) -> dict[str, Any]:
        return service.navigate(session_id=session_id, url=url)

    @app.tool()
    def get_dom_tree(session_id: str, form_selector: str = "form") -> dict[str, Any]:
        return service.get_dom_tree(session_id=session_id, form_selector=form_selector)

    @app.tool()
    def fill_field(
        session_id: str,
        selector: str,
        value: Any,
        use_humanizer: bool = True,
    ) -> dict[str, Any]:
        return service.fill_field(
            session_id=session_id,
            selector=selector,
            value=value,
            use_humanizer=use_humanizer,
        )

    @app.tool()
    def upload_file(session_id: str, selector: str, file_path: str) -> dict[str, Any]:
        return service.upload_file(
            session_id=session_id,
            selector=selector,
            file_path=file_path,
        )

    @app.tool()
    def click(session_id: str, selector: str) -> dict[str, Any]:
        return service.click(session_id=session_id, selector=selector)

    @app.tool()
    def screenshot(session_id: str, path: str, full_page: bool = True) -> dict[str, Any]:
        return service.screenshot(
            session_id=session_id,
            path=path,
            full_page=full_page,
        )

    return app


if __name__ == "__main__":  # pragma: no cover
    app = build_fastmcp_app()
    app.run()

