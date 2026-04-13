"""
FastAPI Application — Main entry point for the job_finder API.

Provides REST endpoints for the Decision Queue UI and handles
CORS, middleware, and route registration.

See Appendix E for the full route map.
"""

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.middleware.pii_guard import PIIGuardMiddleware

load_dotenv()


def _configure_asyncio_for_windows() -> None:
    """
    Ensure asyncio subprocess support on Windows.

    Playwright launches browser subprocesses; this requires the Proactor
    event loop policy on Windows.
    """
    if not sys.platform.startswith("win"):
        return
    policy_cls = getattr(asyncio, "WindowsProactorEventLoopPolicy", None)
    if policy_cls is None:
        return
    try:
        if not isinstance(asyncio.get_event_loop_policy(), policy_cls):
            asyncio.set_event_loop_policy(policy_cls())
    except Exception:
        # Safe best-effort; if policy cannot be changed, submission will
        # fail later with a clear Playwright error.
        logging.getLogger("job_finder.api").warning(
            "Could not set Windows Proactor event loop policy.",
            exc_info=True,
        )


_configure_asyncio_for_windows()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("job_finder.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — startup and shutdown events."""
    logger.info("job_finder API starting up...")
    # Phase 2+: initialize workflow, load persona, etc.
    yield
    logger.info("job_finder API shutting down...")


app = FastAPI(
    title="job_finder",
    description="AI-Powered Job Application Automation System",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — allow the Dashboard frontend
dashboard_url = os.getenv("DASHBOARD_URL", "http://localhost:5173")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[dashboard_url, "http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# PII Guard middleware — scans responses for PII leaks
app.add_middleware(PIIGuardMiddleware)

# Register route modules
from api.routes import (  # noqa: E402
    applications,
    batch,
    insights,
    jobs,
    persona,
    settings,
    websocket,
    development,
)

app.include_router(persona.router, prefix="/api", tags=["Persona"])
app.include_router(jobs.router, prefix="/api", tags=["Jobs"])
app.include_router(applications.router, prefix="/api", tags=["Applications"])
app.include_router(batch.router, prefix="/api", tags=["Batch"])
app.include_router(insights.router, prefix="/api", tags=["Insights"])
app.include_router(settings.router, prefix="/api", tags=["Settings"])
app.include_router(development.router, prefix="/api", tags=["Development"])
app.include_router(websocket.router, tags=["WebSocket"])


@app.get("/")
async def root():
    """Health check endpoint."""
    return {
        "service": "job_finder",
        "version": "0.1.0",
        "status": "running",
    }


@app.get("/api/health")
async def health_check():
    """Detailed health check."""
    return {
        "status": "healthy",
        "components": {
            "api": "up",
            "pii_vault": "not_checked",  # Phase 2: actual checks
            "llm_router": "not_checked",
            "workflow": "not_checked",
        },
    }
