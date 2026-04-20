"""
llm/client.py — Thin, typed wrapper around the Anthropic SDK.

Features:
- Standard chat (returns text or tool-use result)
- Streaming chat (async generator of text chunks for SSE)
- Vision chat (image + text, for Stagehand screenshot analysis)
- Prompt loading from prompts/{name}.md with fallback to inline defaults
- Prompt caching headers on repeated system prompts (reduces cost)
- Structured logging of every call (tokens, duration, model)

Usage:
    from llm.client import LLMClient
    client = LLMClient(api_key=settings.anthropic_api_key)
    text = await client.chat(messages=[{"role": "user", "content": "Hello"}])
    async for chunk in client.stream(messages=[...], system="You are..."):
        print(chunk, end="", flush=True)
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, AsyncIterator

import anthropic

from config import settings

log = logging.getLogger(__name__)

# Default model — override per-call if needed
DEFAULT_MODEL = "claude-opus-4-5"
FAST_MODEL = "claude-haiku-4-5"  # for quick classification tasks

# Prompt file directory (sibling of this file's parent)
_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

# Inline fallbacks — used when .md file is missing (prevents silent failure)
_INLINE_DEFAULTS: dict[str, str] = {
    "orchestrator": "You are an AI job search orchestrator. Break user goals into tasks and execute them using available tools.",
    "fit_scorer": "You are an expert recruiter scoring job-candidate fit. Return JSON with score (0-100), summary, strengths, gaps, and interview_likelihood.",
    "resume_writer": "You are an expert resume writer. Tailor the resume to match the job description without fabricating information.",
    "cover_letter": "Write a concise, specific cover letter (3 paragraphs, ~250 words). No generic phrases.",
    "profile_builder": "You are a helpful career assistant extracting and refining the user's professional profile.",
    "form_filler": "You are completing a job application form. Fill all fields accurately using the provided profile data.",
    "email_classifier": "Classify this recruiter email. Return JSON with category, summary, action_needed, urgency, key_details.",
    "chat_system": "You are an AI job search assistant with access to the user's profile, job queue, and applications.",
}


class ToolUseResult:
    """Wraps an Anthropic tool-use response for structured handling."""

    def __init__(self, tool_name: str, tool_input: dict, tool_use_id: str) -> None:
        self.tool_name = tool_name
        self.tool_input = tool_input
        self.tool_use_id = tool_use_id

    def __repr__(self) -> str:
        return f"ToolUseResult(tool={self.tool_name!r}, input_keys={list(self.tool_input.keys())})"


def load_prompt(name: str) -> str:
    """
    Load a system prompt from prompts/{name}.md.

    Falls back to an inline default if the file doesn't exist,
    so agents never break silently due to a missing prompt file.

    Args:
        name: Prompt name without extension (e.g., 'fit_scorer').

    Returns:
        Prompt text string.
    """
    path = _PROMPTS_DIR / f"{name}.md"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()

    fallback = _INLINE_DEFAULTS.get(name)
    if fallback:
        log.warning(
            "Prompt file missing — using inline default",
            extra={"prompt": name, "expected_path": str(path)},
        )
        return fallback

    raise ValueError(
        f"No prompt file found at {path} and no inline default for {name!r}. "
        f"Available defaults: {list(_INLINE_DEFAULTS.keys())}"
    )


class LLMClient:
    """
    Async wrapper around the Anthropic Claude API.

    Handles retries, logging, streaming, and vision calls.
    Intended to be created once per application lifetime and shared.

    Args:
        api_key: Anthropic API key.
        default_model: Model to use when not overridden per-call.
        max_retries: Number of retries on transient errors (429, 529).
    """

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = DEFAULT_MODEL,
        max_retries: int = 3,
    ) -> None:
        key = api_key or settings.anthropic_api_key
        if not key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is required. Set it in .env or pass api_key=..."
            )
        self._client = anthropic.AsyncAnthropic(
            api_key=key,
            max_retries=max_retries,
        )
        self._default_model = default_model

    async def chat(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
    ) -> str | ToolUseResult:
        """
        Send a standard (non-streaming) chat request.

        Args:
            messages: List of {"role": "user"|"assistant", "content": str} dicts.
            system: Optional system prompt. Supports prompt caching.
            tools: Optional Anthropic tool definitions for tool use.
            model: Override the default model.
            max_tokens: Maximum response tokens.

        Returns:
            Text string for text responses, or ToolUseResult for tool calls.
        """
        model = model or self._default_model
        start = time.monotonic()

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }

        if system:
            # Use prompt caching for system prompts (reduces cost on repeated calls)
            kwargs["system"] = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]

        if tools:
            kwargs["tools"] = tools

        log.debug("LLM chat request", extra={"model": model, "msg_count": len(messages)})

        response = await self._client.messages.create(**kwargs)

        duration_ms = int((time.monotonic() - start) * 1000)
        log.info(
            "LLM chat complete",
            extra={
                "model": model,
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "duration_ms": duration_ms,
                "stop_reason": response.stop_reason,
            },
        )

        # Handle tool use
        for block in response.content:
            if block.type == "tool_use":
                return ToolUseResult(
                    tool_name=block.name,
                    tool_input=block.input,  # type: ignore[arg-type]
                    tool_use_id=block.id,
                )

        # Return concatenated text
        text_parts = [b.text for b in response.content if hasattr(b, "text")]
        return "".join(text_parts)

    async def stream(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        """
        Stream a chat response as text chunks.

        Yields individual text delta strings suitable for Server-Sent Events.

        Args:
            messages: Chat history.
            system: Optional system prompt.
            model: Override model.
            max_tokens: Maximum response tokens.

        Yields:
            Text chunk strings as they arrive from the API.
        """
        model = model or self._default_model
        start = time.monotonic()

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }

        if system:
            kwargs["system"] = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]

        log.debug("LLM stream start", extra={"model": model})

        async with self._client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield text

        # Log usage after stream completes
        duration_ms = int((time.monotonic() - start) * 1000)
        log.info(
            "LLM stream complete",
            extra={"model": model, "duration_ms": duration_ms},
        )

    async def with_image(
        self,
        messages: list[dict[str, Any]],
        image_b64: str,
        media_type: str = "image/png",
        system: str | None = None,
        model: str | None = None,
        max_tokens: int = 2048,
    ) -> str:
        """
        Send a vision request with a base64-encoded image.

        Used by the form filler to analyze form screenshots when
        selector-based approaches need additional context.

        Args:
            messages: Chat history (text only).
            image_b64: Base64-encoded image bytes (without data URI prefix).
            media_type: MIME type of the image (e.g., 'image/png').
            system: Optional system prompt.
            model: Override model (vision requires claude-3+ family).
            max_tokens: Maximum response tokens.

        Returns:
            Text response describing or acting on the image.
        """
        model = model or self._default_model

        # Inject image as the last user message
        vision_messages = list(messages)
        if vision_messages and vision_messages[-1]["role"] == "user":
            # Append image to existing last user message
            last = vision_messages[-1]
            content = last["content"] if isinstance(last["content"], list) else [{"type": "text", "text": last["content"]}]
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": image_b64,
                },
            })
            vision_messages[-1] = {"role": "user", "content": content}
        else:
            vision_messages.append({
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                ],
            })

        result = await self.chat(
            messages=vision_messages,
            system=system,
            model=model,
            max_tokens=max_tokens,
        )
        return result if isinstance(result, str) else str(result)

    async def classify(
        self,
        prompt_name: str,
        user_content: str,
        model: str = FAST_MODEL,
        max_tokens: int = 512,
    ) -> str:
        """
        Quick classification call using a named prompt and the fast model.

        Useful for email classification, fit score hints, etc.

        Args:
            prompt_name: Name of prompt file in prompts/ directory.
            user_content: The content to classify.
            model: Model to use (defaults to Haiku for speed/cost).
            max_tokens: Maximum response tokens.

        Returns:
            Classification response text.
        """
        system = load_prompt(prompt_name)
        result = await self.chat(
            messages=[{"role": "user", "content": user_content}],
            system=system,
            model=model,
            max_tokens=max_tokens,
        )
        return result if isinstance(result, str) else str(result)
