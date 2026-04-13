"""
Cover Letter agent.

Generates role-specific, tokenized cover letter content grounded in persona
and listing context.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from errors import LLMParseError
from llm_router.router import LLMRouter

logger = logging.getLogger("job_finder.agents.cover_letter")

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "cover_letter.md"
_ALLOWED_TONES = {"professional", "enthusiastic", "direct"}


def _load_prompt() -> str:
    raw = PROMPT_PATH.read_text(encoding="utf-8")
    if "## System Prompt" not in raw:
        return raw.strip()
    start = raw.index("## System Prompt") + len("## System Prompt")
    next_header = raw.find("\n## ", start)
    if next_header == -1:
        return raw[start:].strip()
    return raw[start:next_header].strip()


def _normalize_list(items: Any) -> list[str]:
    result: list[str] = []
    for item in items or []:
        text = str(item).strip()
        if text:
            result.append(text)
    return result


def _extract_bullets(text: str, limit: int = 3) -> list[str]:
    bullets: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^\s*[-*•]\s+(.+)$", line.strip())
        if not match:
            continue
        value = match.group(1).strip()
        if value and value not in bullets:
            bullets.append(value)
        if len(bullets) >= limit:
            break
    return bullets


def _infer_tone(text: str) -> str:
    lowered = text.lower()
    if any(token in lowered for token in ("excited", "enthusiastic", "thrilled")):
        return "enthusiastic"
    if any(token in lowered for token in ("immediately", "directly", "in summary")):
        return "direct"
    return "professional"


def _extract_cover_letter_text(text: str) -> str:
    content = text.strip()
    for marker in (
        "## Cover Letter",
        "### Cover Letter",
        "Cover Letter:",
    ):
        if marker in content:
            content = content.split(marker, 1)[1].strip()
            break

    lines = content.splitlines()
    start_index = 0
    for idx, line in enumerate(lines):
        if re.match(r"^\s*(Dear|To\s+the\s+Hiring|Hello)", line, flags=re.IGNORECASE):
            start_index = idx
            break
    return "\n".join(lines[start_index:]).strip()


def _parse_cover_letter_payload_from_text(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text:
        return None
    cover_letter_text = _extract_cover_letter_text(text)
    if not cover_letter_text:
        return None
    return {
        "cover_letter_text": cover_letter_text,
        "highlights": _extract_bullets(text, limit=3),
        "tone": _infer_tone(text),
    }


def generate_cover_letter(
    persona: dict[str, Any],
    listing: dict[str, Any],
    fit_score: dict[str, Any] | None = None,
    router: LLMRouter | None = None,
) -> dict[str, Any]:
    """
    Generate tokenized cover letter text for a specific listing.
    """
    if router is None:
        router = LLMRouter()

    system_prompt = _load_prompt()
    payload = {
        "persona": persona,
        "listing": listing,
        "fit_score": fit_score or {},
    }
    try:
        response = router.route_json(
            task_type="cover_letter",
            system_prompt=system_prompt,
            user_prompt=json.dumps(payload, indent=2),
        )
    except LLMParseError as exc:
        recovered = _parse_cover_letter_payload_from_text(exc.raw_response or "")
        if recovered is None:
            raise
        logger.warning(
            "Recovered cover letter from non-JSON model output for listing %s",
            listing.get("listing_id", "<unknown>"),
        )
        response = recovered

    tone = str(response.get("tone", "professional")).strip().lower()
    if tone not in _ALLOWED_TONES:
        tone = "professional"

    cover_letter_text = str(response.get("cover_letter_text", "")).strip()

    # Handle alternative response formats from the LLM
    if not cover_letter_text:
        for alt_key in ("cover_letter", "letter", "content", "text", "letter_text", "body"):
            alt_val = response.get(alt_key)
            if isinstance(alt_val, str) and alt_val.strip():
                cover_letter_text = alt_val.strip()
                logger.info("Found cover letter content under '%s' key (string)", alt_key)
                break
            elif isinstance(alt_val, dict):
                # Reconstruct from nested dict (opening, body, closing, etc.)
                parts = []
                for section_key in ("opening", "introduction", "body", "paragraphs", "experience_highlights", "closing", "signature"):
                    section_val = alt_val.get(section_key)
                    if not section_val:
                        continue
                    if isinstance(section_val, str):
                        parts.append(section_val)
                    elif isinstance(section_val, list):
                        parts.extend(str(item) for item in section_val if item)
                if parts:
                    cover_letter_text = "\n\n".join(parts)
                    logger.info(
                        "Reconstructed cover_letter_text from nested '%s' dict (%d chars)",
                        alt_key, len(cover_letter_text),
                    )
                else:
                    # Last resort: serialize as text
                    cover_letter_text = json.dumps(alt_val, indent=2)
                    logger.warning("Could not parse nested '%s' — using JSON dump", alt_key)
                break

    logger.info(
        "generate_cover_letter result: cover_letter_text_len=%d, response_keys=%s",
        len(cover_letter_text),
        list(response.keys()) if isinstance(response, dict) else type(response).__name__,
    )

    return {
        "cover_letter_id": str(uuid4()),
        "listing_id": listing.get("listing_id"),
        "persona_id": persona.get("persona_id"),
        "cover_letter_text": cover_letter_text,
        "highlights": _normalize_list(response.get("highlights")),
        "tone": tone,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
