"""
Integration test for api/routes/chat.py — SSE streaming + orchestrator.

Uses a real SQLite Store and mock LLM/Orchestrator so we test the full
request → dependency injection → SSE stream → persistence flow. Verifies:
    - SSE events contain expected types (chunk, done)
    - User message persisted before streaming
    - Assistant message persisted after streaming
    - Chat history endpoint works
    - Clear history wipes messages + summary
    - Empty message rejected
    - Context builder includes profile data
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from agents.orchestrator import OrchestratorResult
from api.routes.chat import _build_chat_context, router as chat_router
from memory.conversation import ConversationMemory


@pytest.fixture
def mock_orchestrator():
    m = MagicMock()
    m.handle = AsyncMock(
        return_value=OrchestratorResult(
            text="I found 3 Python backend jobs in Boston.",
            tool_calls=[{"tool": "search_jobs", "input": {"query": "python"}, "output_preview": "..."}],
            side_effects=["jobs_updated"],
        )
    )
    return m


@pytest.fixture
def mock_memory(seeded_store):
    llm = MagicMock()
    llm.chat = AsyncMock(return_value="Summary of chat.")
    mem = ConversationMemory(seeded_store, llm=llm)
    return mem


@pytest.fixture
def test_app(seeded_store, mock_orchestrator, mock_memory):
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(chat_router, prefix="/api/chat")

    from api.dependencies import (
        get_conversation_memory,
        get_llm,
        get_orchestrator,
        get_store,
    )

    mock_llm = MagicMock()

    async def _fake_stream(*a, **kw) -> AsyncIterator[str]:
        for chunk in ("Hello ", "there!"):
            yield chunk

    mock_llm.stream = _fake_stream

    app.dependency_overrides[get_store] = lambda: seeded_store
    app.dependency_overrides[get_llm] = lambda: mock_llm
    app.dependency_overrides[get_orchestrator] = lambda: mock_orchestrator
    app.dependency_overrides[get_conversation_memory] = lambda: mock_memory

    return app


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


def _parse_sse(raw: str) -> list[dict]:
    """Parse an SSE body into a list of JSON event dicts."""
    events = []
    for line in raw.strip().split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def test_chat_sse_stream_with_tools(client) -> None:
    resp = client.post(
        "/api/chat",
        json={"message": "Find Python jobs in Boston", "use_tools": True},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(resp.text)
    types = [e["type"] for e in events]
    assert "chunk" in types
    assert "done" in types

    # The orchestrator result text should appear in a chunk
    chunks = [e for e in events if e["type"] == "chunk"]
    text = "".join(e.get("text", "") for e in chunks)
    assert "Python backend" in text or "3" in text or "Boston" in text


def test_chat_sse_stream_without_tools(client) -> None:
    resp = client.post(
        "/api/chat",
        json={"message": "What is your name?", "use_tools": False},
    )
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    types = [e["type"] for e in events]
    assert "chunk" in types
    assert "done" in types

    chunks = [e for e in events if e["type"] == "chunk"]
    text = "".join(e.get("text", "") for e in chunks)
    assert "Hello there!" == text


def test_chat_persists_user_message(client, seeded_store) -> None:
    client.post("/api/chat", json={"message": "test message"})
    messages = seeded_store.get_messages(limit=50)
    user_msgs = [m for m in messages if m.role == "user"]
    assert any("test message" in m.content for m in user_msgs)


def test_chat_persists_assistant_message(client, seeded_store) -> None:
    client.post(
        "/api/chat",
        json={"message": "Find jobs", "use_tools": True},
    )
    messages = seeded_store.get_messages(limit=50)
    assistant_msgs = [m for m in messages if m.role == "assistant"]
    assert len(assistant_msgs) >= 1


def test_chat_history_endpoint(client, seeded_store) -> None:
    # Add some messages directly
    seeded_store.add_message("user", "hello")
    seeded_store.add_message("assistant", "hi there")

    resp = client.get("/api/chat/history")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 2


def test_chat_clear_wipes_history(client, seeded_store) -> None:
    seeded_store.add_message("user", "something")
    resp = client.delete("/api/chat")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cleared"

    remaining = seeded_store.get_messages(limit=50)
    assert len(remaining) == 0


def test_chat_rejects_empty_message(client) -> None:
    resp = client.post("/api/chat", json={"message": ""})
    assert resp.status_code == 400


def test_chat_rejects_whitespace_message(client) -> None:
    resp = client.post("/api/chat", json={"message": "   "})
    assert resp.status_code == 400


# ─── _build_chat_context unit integration ─────────────────────────────────────


def test_build_chat_context_includes_profile(seeded_store) -> None:
    ctx = _build_chat_context(seeded_store)
    assert "Jane" in ctx
    assert "hybrid" in ctx.lower() or "remote" in ctx.lower()


def test_build_chat_context_includes_job_context(seeded_store, sample_job) -> None:
    ctx = _build_chat_context(seeded_store, context_type="job", context_id=sample_job.id)
    assert "Stripe" in ctx
    assert "Backend Engineer" in ctx


def test_build_chat_context_includes_apply_context(seeded_store, sample_job) -> None:
    app = seeded_store.create_application(
        job_id=sample_job.id,
        status="shadow_review",
        resume_tailored_text="# Jane Doe\nPython expert.",
    )
    ctx = _build_chat_context(seeded_store, context_type="apply", context_id=app.id)
    assert "shadow_review" in ctx
    assert "Jane Doe" in ctx


def test_build_chat_context_includes_pending_apps(seeded_store, sample_job) -> None:
    seeded_store.create_application(job_id=sample_job.id, status="shadow_review")
    ctx = _build_chat_context(seeded_store)
    assert "PENDING REVIEWS" in ctx


def test_sse_action_events_emitted(client) -> None:
    """Orchestrator side_effects should appear as 'action' SSE events."""
    resp = client.post(
        "/api/chat",
        json={"message": "Search for jobs", "use_tools": True},
    )
    events = _parse_sse(resp.text)
    action_events = [e for e in events if e["type"] == "action"]
    assert len(action_events) >= 1
    assert action_events[0]["action"] == "jobs_updated"
