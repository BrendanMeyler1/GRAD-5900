"""Tests for mcp_servers.filesystem_server."""

from pathlib import Path
from uuid import uuid4

import pytest

from mcp_servers.filesystem_server import LocalFileMCPServer


def _server() -> tuple[LocalFileMCPServer, Path]:
    root = Path(".tmp") / f"mcp_files_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    return LocalFileMCPServer(root_dir=str(root)), root


def test_list_and_read_files():
    server, root = _server()
    (root / "master_bullets.md").write_text("- Bullet A\n- Bullet B\n", encoding="utf-8")
    (root / "notes.txt").write_text("hello", encoding="utf-8")

    files = server.list_files()
    assert "master_bullets.md" in files
    assert "notes.txt" in files

    content = server.read_master_bullets()
    assert "Bullet A" in content


def test_path_traversal_is_blocked():
    server, _ = _server()
    with pytest.raises(ValueError):
        server.read_file("../secrets.txt")


def test_max_bytes_limit_enforced():
    server, root = _server()
    (root / "big.txt").write_text("x" * 1024, encoding="utf-8")
    with pytest.raises(ValueError):
        server.read_file("big.txt", max_bytes=10)
