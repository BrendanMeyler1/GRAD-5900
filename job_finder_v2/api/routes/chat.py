"""
api/routes/chat.py — Streaming chat endpoint with live context injection.

The chat endpoint is the user's primary interface to the multi-agent system.
Every request gets a freshly-computed context block (profile snapshot,
pending reviews, recent top jobs, email alerts) prepended to the system
prompt, so Claude always knows the current state of the app.

Two execution paths:
    1. Conversational — plain streaming reply via LLMClient.stream()
    2. Action-taking — orchestrator.handle() runs the tool-use loop,
       then we stream the final synthesized text back.

The chat payload opts into orchestrator mode via `use_tools: true`.
If false (or omitted), we stream a pure conversational response.

Endpoints:
    POST   /api/chat          → SSE stream
    GET    /api/chat/history  → last 50 messages
    DELETE /api/chat          → clear chat history (keeps profile)
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.dependencies import (
    get_conversation_memory,
    get_llm,
    get_orchestrator,
    get_store,
)
from db.store import ChatMessage, JobFilters, Store
from llm.client import LLMClient, load_prompt

log = logging.getLogger(__name__)
router = APIRouter()


class ChatRequest(BaseModel):
    """Chat request payload."""

    message: str
    context_type: str | None = None  # 'general'|'job'|'apply'|'profile'
    context_id: str | None = None
    use_tools: bool = True  # run orchestrator w/ tools vs plain stream


# ───────────────────────────── context builder ─────────────────────────────


def _build_chat_context(
    store: Store, context_type: str | None = None, context_id: str | None = None
) -> str:
    """
    Compose a live context block to inject into the chat system prompt.

    Reads directly from the DB on every turn, so Claude always sees the
    current profile, pending reviews, recent top jobs, and email alerts.
    """
    profile = store.get_full_profile()
    p = profile.profile

    parts: list[str] = []
    parts.append("--- CURRENT APP STATE ---")
    parts.append(f"User: {p.full_name or '(not set)'} | Email: {p.email or '—'}")
    parts.append(
        f"Profile completeness: {profile.completion_pct}% "
        f"(is_complete_enough: {profile.is_complete_enough})"
    )
    if profile.skills:
        top = ", ".join(s.name for s in profile.skills[:20])
        parts.append(f"Skills: {top}")
    if profile.experience:
        recent = profile.experience[0]
        parts.append(
            f"Most recent role: {recent.title or '—'} at {recent.company or '—'}"
        )
    if p.target_salary_min and p.target_salary_max:
        parts.append(f"Salary target: ${p.target_salary_min:,}–${p.target_salary_max:,}")
    if p.remote_preference:
        parts.append(f"Remote preference: {p.remote_preference}")

    # Pending reviews — the human-in-the-loop queue
    pending = store.list_applications(status="shadow_review")
    if pending:
        parts.append(f"\nPENDING REVIEWS ({len(pending)} awaiting approval):")
        for a in pending[:5]:
            if a.job:
                parts.append(f"  - {a.job.company}: {a.job.title}  (app_id: {a.id})")

    # Recent top jobs
    recent_jobs = store.get_jobs(JobFilters(sort_by="fit_score", limit=5))
    if recent_jobs:
        parts.append("\nRECENT TOP JOBS:")
        for j in recent_jobs:
            score = f"{j.fit_score:.0f}" if j.fit_score is not None else "—"
            parts.append(f"  - {j.company}: {j.title} | fit={score} | status={j.status}")

    # Email alerts needing attention
    alerts = store.get_email_events(action_needed=True, limit=5)
    if alerts:
        parts.append(f"\nEMAIL ALERTS ({len(alerts)} need attention):")
        for e in alerts:
            parts.append(f"  - {e.company}: {e.summary}")

    # Narrow context if the user is looking at something specific
    if context_type == "job" and context_id:
        job = store.get_job(context_id)
        if job:
            parts.append(
                f"\n--- CURRENT JOB CONTEXT ---\n"
                f"{job.title} at {job.company} ({job.location or '—'})\n"
                f"Fit: {job.fit_score or '—'} | {job.fit_summary or ''}\n"
                f"{(job.description or '')[:2000]}"
            )
    elif context_type == "apply" and context_id:
        app = store.get_application(context_id)
        if app:
            company = app.job.company if app.job else "—"
            title = app.job.title if app.job else "—"
            parts.append(
                f"\n--- CURRENT APPLICATION ---\n"
                f"{title} at {company}\nStatus: {app.status}\n"
                f"Tailored resume (excerpt):\n{(app.resume_tailored_text or '')[:1500]}"
            )

    return "\n".join(parts)


# ───────────────────────────── SSE helpers ─────────────────────────────


def _sse(event: dict[str, Any]) -> str:
    """Format an event as a Server-Sent Event line."""
    return f"data: {json.dumps(event)}\n\n"


# ───────────────────────────── endpoints ─────────────────────────────


@router.post("", summary="Stream chat reply (SSE)")
async def chat(
    request: ChatRequest,
    store: Store = Depends(get_store),
    llm: LLMClient = Depends(get_llm),
    memory=Depends(get_conversation_memory),
    orchestrator=Depends(get_orchestrator),
) -> StreamingResponse:
    """
    Stream an assistant reply as Server-Sent Events.

    Event types:
        chunk   → text delta (append to current message)
        action  → side effect for UI (e.g. "jobs_updated")
        context → the context panel should switch (job/apply/profile)
        error   → something failed
        done    → stream finished; final assistant message saved
    """
    if not request.message or not request.message.strip():
        raise HTTPException(status_code=400, detail={"error": "empty_message"})

    # Persist user message immediately so it's visible if the browser refreshes
    memory.add(
        "user",
        request.message.strip(),
        context_type=request.context_type,
        context_id=request.context_id,
    )

    live_context = _build_chat_context(store, request.context_type, request.context_id)
    system = load_prompt("chat_system") + "\n\n" + live_context
    history = memory.get_context_window()

    async def generator() -> AsyncIterator[str]:
        collected: list[str] = []
        try:
            if request.use_tools:
                # Orchestrator path: run the tool-use loop, then stream the final text
                # Emit a heartbeat so the UI knows something is happening
                yield _sse({"type": "chunk", "text": ""})
                result = await orchestrator.handle(
                    user_message=request.message,
                    history=history,
                    live_context=live_context,
                )
                # Stream the final text in chunks for a progressive feel
                text = result.text or ""
                collected.append(text)
                for i in range(0, len(text), 80):
                    yield _sse({"type": "chunk", "text": text[i : i + 80]})
                    await asyncio.sleep(0.01)

                for effect in result.side_effects:
                    yield _sse({"type": "action", "action": effect})

                if request.context_type and request.context_id:
                    yield _sse(
                        {
                            "type": "context",
                            "context_type": request.context_type,
                            "context_id": request.context_id,
                        }
                    )
            else:
                # Plain conversational stream — no tools
                messages = history + [{"role": "user", "content": request.message}]
                async for delta in llm.stream(messages=messages, system=system):
                    collected.append(delta)
                    yield _sse({"type": "chunk", "text": delta})

            full_text = "".join(collected).strip()
            if full_text:
                memory.add(
                    "assistant",
                    full_text,
                    context_type=request.context_type,
                    context_id=request.context_id,
                )
            # Trigger rolling summarization if needed (non-blocking)
            asyncio.create_task(memory.maybe_summarize())

            yield _sse({"type": "done"})
        except Exception as exc:  # noqa: BLE001
            log.exception("chat.stream_failed", extra={"error": str(exc)})
            yield _sse({"type": "error", "message": str(exc)})
            yield _sse({"type": "done"})

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/history", summary="Last N chat messages", response_model=list[ChatMessage])
async def history(
    limit: int = 50, store: Store = Depends(get_store)
) -> list[ChatMessage]:
    return store.get_messages(limit=limit)


@router.delete("", summary="Clear chat history (profile + jobs + apps unchanged)")
async def clear_history(store: Store = Depends(get_store)) -> dict[str, str]:
    conn = store._get_conn()  # noqa: SLF001 — intentional direct access for admin op
    conn.execute("DELETE FROM chat_messages")
    conn.execute("DELETE FROM conversation_summary WHERE id = 1")
    conn.commit()
    log.info("chat.history_cleared")
    return {"status": "cleared"}
