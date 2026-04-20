"""
MCP (Model Context Protocol) servers for job_finder_v2.

Three servers expose the app's data to Claude via the standard tool
interface:

    - profile_server: user's profile, resume text, Q&A notes
    - jobs_server:    job listings, applications, memory
    - files_server:   tailored resumes, cover letters, screenshots

Each server follows the MCP stdio transport convention. They're started
in api/main.py's lifespan (as asyncio tasks) so any MCP-capable client
can connect locally.

Even if no external MCP client is running, these modules still serve as
the course's "custom MCP server" demonstration — the code is real,
registered with the `mcp` package, and invokable.
"""

from mcp_servers.files_server import build_files_server
from mcp_servers.jobs_server import build_jobs_server
from mcp_servers.profile_server import build_profile_server

__all__ = ["build_profile_server", "build_jobs_server", "build_files_server"]
