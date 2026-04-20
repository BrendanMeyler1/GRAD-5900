"""
api/dependencies.py — FastAPI dependency injection helpers.

Every route that needs DB or LLM access uses `Depends(get_store)` /
`Depends(get_llm)` so the code stays testable (fixtures override these).

Per-request Store instances ensure each request owns its SQLite
connection — SQLite connections are not thread-safe across threads
in Python, but each FastAPI worker handles requests sequentially in
its event loop, so a single connection per request is perfect.
"""

from __future__ import annotations

from typing import Iterator

from fastapi import Request

from config import settings
from db.encryption import get_encryptor
from db.store import Store
from llm.client import LLMClient


def get_store(request: Request) -> Store:
    """
    Return the shared Store instance from app state.

    Single-user app + SQLite WAL mode = one long-lived connection is fine.
    Initialised once in api/main.py's lifespan.
    """
    store: Store | None = getattr(request.app.state, "store", None)
    if store is None:
        store = Store(settings.db_path, get_encryptor())
        request.app.state.store = store
    return store


def get_llm(request: Request) -> LLMClient:
    """
    Return the shared LLMClient from app state.

    Initialised once in api/main.py's lifespan to avoid spinning up an
    Anthropic client per request.
    """
    client: LLMClient | None = getattr(request.app.state, "llm", None)
    if client is None:
        # Fallback: construct on demand (should not happen in production)
        client = LLMClient()
        request.app.state.llm = client
    return client


def get_pipeline(request: Request):
    """Return the shared Pipeline instance from app state."""
    return request.app.state.pipeline


def get_orchestrator(request: Request):
    """Return the shared Orchestrator instance from app state."""
    return request.app.state.orchestrator


def get_email_tracker(request: Request):
    """Return the shared EmailTracker instance from app state."""
    return request.app.state.email_tracker


def get_profile_builder(request: Request):
    """Return the shared ProfileBuilder instance from app state."""
    return request.app.state.profile_builder


def get_job_scout(request: Request):
    """Return the shared JobScout instance from app state."""
    return request.app.state.job_scout


def get_conversation_memory(request: Request):
    """Return the shared ConversationMemory instance from app state."""
    return request.app.state.conversation_memory
