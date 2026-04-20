"""
run.py — Start the Job Finder v2 API server.

Use this instead of calling uvicorn directly:
    python run.py

Why not `uvicorn api.main:app --reload` directly?
-----------------------------------------------
Uvicorn 0.40+ passes ``use_subprocess=True`` to its loop factory when
launching the reload worker subprocess, which makes the built-in factory
return ``SelectorEventLoop`` on Windows.  Playwright needs ProactorEventLoop
to call ``asyncio.create_subprocess_exec()`` (browser launch).

This script passes our custom ``browser.proactor_loop:factory`` which always
returns ProactorEventLoop on Windows, so both the reloader parent AND every
worker subprocess get the right loop type.

If you prefer the raw uvicorn CLI, use:
    uvicorn api.main:app --reload --port 8000 --loop browser.proactor_loop:factory
"""

from __future__ import annotations

import os

# ── Strip empty environment-variable overrides ────────────────────────────────
# If a variable is set in the OS environment as an empty string (e.g., a stale
# shell export), it silently overrides the .env file value in pydantic_settings.
# We clear those out here so the .env file is the authoritative source.
_REQUIRED_ENV_KEYS = ("ANTHROPIC_API_KEY", "JSEARCH_API_KEY")
for _key in _REQUIRED_ENV_KEYS:
    if _key in os.environ and not os.environ[_key].strip():
        del os.environ[_key]

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=["api", "agents", "db", "llm", "scrapers", "filler",
                     "memory", "mcp_servers", "utils", "browser", "pipeline.py"],
        # Custom loop factory: always ProactorEventLoop on Windows.
        # The built-in "asyncio" factory returns SelectorEventLoop in
        # the reload subprocess (use_subprocess=True), breaking Playwright.
        loop="browser.proactor_loop:factory",
        log_level="info",
    )
