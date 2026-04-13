"""
Local File MCP Server (Phase 2 Step 7).

Primary purpose in this phase:
- read-only access to local prompt/context files, especially master bullets.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class LocalFileMCPServer:
    """Read-only filesystem access wrapper for MCP tool exposure."""

    def __init__(self, root_dir: str = "data/raw") -> None:
        self.root_dir = Path(root_dir).resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _resolve(self, relative_path: str) -> Path:
        candidate = (self.root_dir / relative_path).resolve()
        if not str(candidate).startswith(str(self.root_dir)):
            raise ValueError(f"Path escapes allowed root: {relative_path}")
        return candidate

    def list_files(self, recursive: bool = False, suffix: str | None = None) -> list[str]:
        """
        List files under root_dir.
        """
        if recursive:
            items = [p for p in self.root_dir.rglob("*") if p.is_file()]
        else:
            items = [p for p in self.root_dir.iterdir() if p.is_file()]

        if suffix:
            items = [p for p in items if p.name.endswith(suffix)]

        return sorted(str(p.relative_to(self.root_dir)).replace("\\", "/") for p in items)

    def read_file(self, relative_path: str, max_bytes: int = 1_000_000) -> str:
        """
        Read a UTF-8 text file from root_dir with size guard.
        """
        path = self._resolve(relative_path)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"File not found: {relative_path}")

        data = path.read_bytes()
        if len(data) > max_bytes:
            raise ValueError(
                f"File too large ({len(data)} bytes). Max allowed: {max_bytes}."
            )
        return data.decode("utf-8")

    def read_master_bullets(self, filename: str = "master_bullets.md") -> str:
        """
        Dedicated helper used by Resume Tailor.
        """
        return self.read_file(filename)

    def describe(self) -> dict[str, Any]:
        """Return server metadata."""
        return {
            "server": "local_file_mcp",
            "root_dir": str(self.root_dir),
            "capabilities": ["list_files", "read_file", "read_master_bullets"],
            "mode": "read_only",
        }


def build_fastmcp_app(root_dir: str = "data/raw"):
    """
    Optional FastMCP app factory.
    Keeps import optional so unit tests and local tooling work without MCP runtime.
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception as exc:  # pragma: no cover - depends on runtime package API
        raise RuntimeError(
            "FastMCP runtime not available. Install/upgrade `mcp` package."
        ) from exc

    service = LocalFileMCPServer(root_dir=root_dir)
    app = FastMCP("job_finder_filesystem")

    @app.tool()
    def list_files(recursive: bool = False, suffix: str | None = None) -> list[str]:
        return service.list_files(recursive=recursive, suffix=suffix)

    @app.tool()
    def read_file(relative_path: str, max_bytes: int = 1_000_000) -> str:
        return service.read_file(relative_path=relative_path, max_bytes=max_bytes)

    @app.tool()
    def read_master_bullets(filename: str = "master_bullets.md") -> str:
        return service.read_master_bullets(filename=filename)

    return app


if __name__ == "__main__":  # pragma: no cover
    app = build_fastmcp_app()
    app.run()
