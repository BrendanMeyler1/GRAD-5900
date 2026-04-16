"""Unit tests for memory.conversation.ConversationMemory."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from memory.conversation import RECENT_WINDOW, SUMMARIZE_EVERY, ConversationMemory


def test_add_and_get_recent(store) -> None:
    mem = ConversationMemory(store, llm=MagicMock())
    mem.add("user", "hello")
    mem.add("assistant", "hi")
    recent = mem.get_recent()
    assert [m.content for m in recent] == ["hello", "hi"]


def test_context_window_respects_recent_limit(store) -> None:
    mem = ConversationMemory(store, llm=MagicMock())
    for i in range(RECENT_WINDOW + 5):
        mem.add("user", f"message {i}")
    window = mem.get_context_window()
    # No summary yet, so just recent window
    assert len(window) == RECENT_WINDOW


def test_context_window_includes_summary(store) -> None:
    store.update_summary("User wants Python jobs.", message_count=0)
    mem = ConversationMemory(store, llm=MagicMock())
    mem.add("user", "latest message")
    window = mem.get_context_window()
    # First entry is the summary as assistant message
    assert "User wants Python jobs" in window[0]["content"]
    assert window[-1]["content"] == "latest message"


async def test_maybe_summarize_triggers_after_threshold(store) -> None:
    llm = MagicMock()
    llm.chat = AsyncMock(return_value="Summary: user asked about jobs.")
    mem = ConversationMemory(store, llm=llm)

    # Add just under the threshold — no summary
    for i in range(SUMMARIZE_EVERY - 1):
        mem.add("user", f"m{i}")
    did = await mem.maybe_summarize()
    assert did is False
    assert store.get_summary() is None

    # Add one more — crosses the threshold
    mem.add("user", "trigger")
    did = await mem.maybe_summarize()
    assert did is True
    assert "Summary" in store.get_summary()


async def test_maybe_summarize_handles_llm_failure(store) -> None:
    """If the LLM raises, we log and return False — never crash the chat loop."""
    llm = MagicMock()
    llm.chat = AsyncMock(side_effect=RuntimeError("rate limit"))
    mem = ConversationMemory(store, llm=llm)

    for i in range(SUMMARIZE_EVERY + 1):
        mem.add("user", f"m{i}")
    did = await mem.maybe_summarize()
    assert did is False
    assert store.get_summary() is None


def test_context_window_filters_unknown_roles(store) -> None:
    """If some odd role value leaks in, get_context_window should skip it."""
    mem = ConversationMemory(store, llm=MagicMock())
    mem.add("user", "question")
    mem.add("assistant", "answer")
    # Directly inject a non-standard role via the store
    store.add_message("system", "weird")
    window = mem.get_context_window()
    roles = [m["role"] for m in window]
    assert "system" not in roles
