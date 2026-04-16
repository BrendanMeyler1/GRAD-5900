"""
api/main.py — FastAPI application entrypoint.

Responsibilities:
    - Configure logging before anything else.
    - Validate required env vars via config.settings.
    - Initialise the DB (idempotent CREATE TABLE IF NOT EXISTS).
    - Create required data directories.
    - Warm singleton instances (Store, LLMClient, Pipeline, Orchestrator,
      workers, ConversationMemory, task registry).
    - Mount routes + middleware + exception handler.
    - Schedule periodic email sync if OUTLOOK_* is configured.

Run:
    uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from agents.email_tracker import EmailTracker
from agents.job_scout import JobScout
from agents.orchestrator import Orchestrator
from agents.profile_builder import ProfileBuilder
from agents.resume_writer import ResumeWriter
from api.middleware import RequestLoggingMiddleware, global_exception_handler
from api.routes import applications, apply, chat, email, jobs, profile, tasks as tasks_routes
from config import settings
from db.encryption import get_encryptor
from db.schema import init_db
from db.store import Store
from llm.client import LLMClient
from logging_config import setup_logging
from memory.conversation import ConversationMemory
from pipeline import Pipeline

# Logging MUST be configured before any logger is created elsewhere
setup_logging(level=settings.log_level, log_dir=settings.logs_dir)

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup: validate, init, warm singletons. Shutdown: clean up."""
    log.info(
        "app.startup",
        extra={
            "port": settings.port,
            "dev_mode": settings.dev_mode,
            "headless": settings.headless,
            "data_dir": settings.data_dir,
        },
    )

    # Ensure data directories exist
    for p in (
        settings.data_dir,
        settings.resumes_dir,
        settings.generated_dir,
        settings.screenshots_dir,
        settings.logs_dir,
    ):
        Path(p).mkdir(parents=True, exist_ok=True)

    # Initialise DB
    init_db(settings.db_path)
    log.info("app.db_ready", extra={"path": settings.db_path})

    # Warm singletons
    store = Store(settings.db_path, get_encryptor())
    app.state.store = store

    try:
        llm = LLMClient()
    except RuntimeError as exc:
        log.error("app.llm_init_failed", extra={"error": str(exc)})
        raise
    app.state.llm = llm

    resume_writer = ResumeWriter(llm)
    job_scout = JobScout(store, llm)
    profile_builder = ProfileBuilder(store, llm)
    email_tracker = EmailTracker(store, llm)
    conversation_memory = ConversationMemory(store, llm)

    pipeline = Pipeline(store=store, llm=llm, resume_writer=resume_writer)
    workers = {
        "job_scout": job_scout,
        "resume_writer": resume_writer,
        "email_tracker": email_tracker,
        "profile_builder": profile_builder,
        "pipeline": pipeline,
    }
    orchestrator = Orchestrator(store=store, llm=llm, workers=workers)

    app.state.pipeline = pipeline
    app.state.orchestrator = orchestrator
    app.state.job_scout = job_scout
    app.state.profile_builder = profile_builder
    app.state.email_tracker = email_tracker
    app.state.conversation_memory = conversation_memory
    app.state.resume_writer = resume_writer

    # Startup summary
    profile = store.get_full_profile()
    jobs_count = len(store.get_jobs())
    apps_count = len(store.list_applications())
    log.info(
        "app.ready",
        extra={
            "profile_complete": profile.is_complete_enough,
            "completion_pct": profile.completion_pct,
            "jobs": jobs_count,
            "applications": apps_count,
            "email_configured": settings.email_configured,
        },
    )

    # Kick off periodic email sync if configured
    email_task: asyncio.Task | None = None
    if settings.email_configured:
        email_task = asyncio.create_task(_email_sync_loop(email_tracker))

    try:
        yield
    finally:
        log.info("app.shutdown.start")
        if email_task is not None:
            email_task.cancel()
            try:
                await email_task
            except asyncio.CancelledError:
                pass
        try:
            await pipeline.close()
        except Exception as exc:  # noqa: BLE001 — cleanup
            log.warning("app.shutdown.pipeline_close_error", extra={"error": str(exc)})
        try:
            store.close()
        except Exception as exc:  # noqa: BLE001 — cleanup
            log.warning("app.shutdown.store_close_error", extra={"error": str(exc)})
        log.info("app.shutdown.complete")


async def _email_sync_loop(tracker: EmailTracker) -> None:
    """Run email sync every 30 minutes while the server is running."""
    log.info("email_sync_loop.start")
    while True:
        try:
            await asyncio.sleep(30 * 60)
            await tracker.sync(since_days=1)
        except asyncio.CancelledError:
            log.info("email_sync_loop.cancelled")
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("email_sync_loop.error", extra={"error": str(exc)})


app = FastAPI(
    title="Job Finder v2 API",
    description=(
        "Multi-agent job application assistant with MCP, persistent memory, "
        "and human-in-the-loop shadow application review."
    ),
    version="2.0.0",
    lifespan=lifespan,
)

# --- Middleware -------------------------------------------------------------

app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)
app.add_exception_handler(Exception, global_exception_handler)

# --- Routes -----------------------------------------------------------------

app.include_router(profile.router, prefix="/api/profile", tags=["profile"])
app.include_router(jobs.router, prefix="/api/jobs", tags=["jobs"])
app.include_router(apply.router, prefix="/api/apply", tags=["apply"])
app.include_router(applications.router, prefix="/api/applications", tags=["applications"])
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(email.router, prefix="/api/email", tags=["email"])
app.include_router(tasks_routes.router, prefix="/api/tasks", tags=["tasks"])


@app.get("/api/health", summary="Liveness probe", tags=["meta"])
async def health(request: Request) -> dict:
    """Simple liveness check. Returns 200 if the app is up."""
    store: Store = request.app.state.store
    return {
        "status": "ok",
        "dev_mode": settings.dev_mode,
        "profile_complete": store.get_full_profile().is_complete_enough,
    }


@app.get("/", include_in_schema=False)
async def root() -> JSONResponse:
    return JSONResponse({"app": "job-finder-v2", "docs": "/docs"})


# Serve screenshots statically so the dashboard can display them
_screenshots_dir = Path(settings.screenshots_dir)
_screenshots_dir.mkdir(parents=True, exist_ok=True)
app.mount(
    "/static/screenshots",
    StaticFiles(directory=str(_screenshots_dir)),
    name="screenshots",
)
