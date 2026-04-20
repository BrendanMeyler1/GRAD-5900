"""
memory/conversation.py — Rolling chat memory with LLM summarization.

We persist every chat message to SQLite, but we don't send all of them
to Claude on each turn — that would balloon cost over time. Instead:

    - Keep the last N messages (default: 20) verbatim.
    - Maintain a running summary of everything older than that window.
    - When the number of messages since the last summary exceeds a
      threshold (default: 30), generate an updated summary via Claude
      and persist it.

The orchestrator/chat route composes the model's context as:

    system prompt
    + (if summary exists) "Previous conversation summary: <summary>"
    + last 20 messages
    + current user message

This keeps token cost bounded regardless of session length.
"""

from __future__ import annotations

import logging
from typing import Any

from db.store import ChatMessage, Store
from llm.client import LLMClient

log = logging.getLogger(__name__)

RECENT_WINDOW = 20
SUMMARIZE_EVERY = 30  # messages added since the last summary


class ConversationMemory:
    """
    Rolling chat memory.

    Usage:
        memory = ConversationMemory(store, llm)
        memory.add("user", "Find Python jobs in Boston")
        await memory.maybe_summarize()
        context = memory.get_context_window()  # → list[anthropic message dicts]
    """

    def __init__(self, store: Store, llm: LLMClient | None = None) -> None:
        self.store = store
        self.llm = llm or LLMClient()

    # --- read/write ------------------------------------------------------

    def add(
        self,
        role: str,
        content: str,
        context_type: str | None = None,
        context_id: str | None = None,
    ) -> ChatMessage:
        """Persist a single message and return it."""
        msg = self.store.add_message(role, content, context_type, context_id)
        log.debug(
            "conversation.add",
            extra={"role": role, "chars": len(content), "context_type": context_type},
        )
        return msg

    def get_recent(self, limit: int = RECENT_WINDOW) -> list[ChatMessage]:
        """Return the most recent `limit` messages in chronological order."""
        return self.store.get_messages(limit=limit)

    def get_summary(self) -> str | None:
        """Return the rolling summary text (None if never summarised)."""
        return self.store.get_summary()

    # --- context assembly ------------------------------------------------

    def get_context_window(self, recent_limit: int = RECENT_WINDOW) -> list[dict[str, Any]]:
        """
        Build a list of Anthropic-format messages to send to the model.

        Format: one assistant message with the summary (if any), followed by
        the recent window. The caller adds their own system prompt.
        """
        messages: list[dict[str, Any]] = []

        summary = self.get_summary()
        if summary:
            messages.append(
                {
                    "role": "assistant",
                    "content": f"[Earlier conversation summary]\n{summary}",
                }
            )

        for msg in self.get_recent(recent_limit):
            if msg.role not in {"user", "assistant"}:
                continue
            messages.append({"role": msg.role, "content": msg.content})

        return messages

    # --- summarisation ---------------------------------------------------

    async def maybe_summarize(self) -> bool:
        """
        If enough new messages have accumulated since the last summary,
        generate an updated rolling summary and persist it.

        Returns True if a new summary was written, False otherwise.
        """
        total = self.store.get_message_count()
        last_summary_count = self.store.get_summary_message_count()
        if total - last_summary_count < SUMMARIZE_EVERY:
            return False

        # Summarize everything EXCEPT the most recent window (that's kept verbatim)
        keep_recent = RECENT_WINDOW
        all_msgs = self.store.get_messages(limit=max(total, 10_000))
        to_summarize = all_msgs[:-keep_recent] if len(all_msgs) > keep_recent else all_msgs
        if not to_summarize:
            return False

        prior_summary = self.get_summary() or ""
        transcript = "\n".join(
            f"{m.role.upper()}: {m.content}" for m in to_summarize
        )[:40_000]  # hard cap

        user_content = f"""PRIOR SUMMARY (may be empty):
{prior_summary}

NEW TRANSCRIPT TO INCORPORATE:
{transcript}

Produce an updated summary of the conversation. Include:
- Key things the user has told us about themselves, their goals, and preferences.
- Decisions that have been made (jobs they're interested in, roles they've applied to, what got rejected).
- Any commitments or follow-ups (e.g. "user said they'd upload an updated resume").

Write it in 10-20 concise bullet points. Preserve specific names, numbers, and URLs. Do NOT invent facts. Return only the summary.
"""
        try:
            resp = await self.llm.chat(
                messages=[{"role": "user", "content": user_content}],
                system="You are an assistant maintaining a rolling summary of a chat session.",
                max_tokens=1500,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("conversation.summarize_failed", extra={"error": str(exc)})
            return False

        summary_text = resp if isinstance(resp, str) else str(resp)
        self.store.update_summary(summary_text.strip(), message_count=total)
        log.info(
            "conversation.summary_updated",
            extra={"total_messages": total, "summary_chars": len(summary_text)},
        )
        return True
