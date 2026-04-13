"""
Fit Scorer agent.

Scores persona-job match quality and returns a structured breakdown
used by downstream resume/cover-letter generation and queue decisions.
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

logger = logging.getLogger("job_finder.agents.fit_scorer")

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "fit_scorer.md"
_BREAKDOWN_KEYS = [
    "skills_match",
    "experience_level",
    "domain_relevance",
    "culture_signals",
    "location_match",
]


def _load_prompt() -> str:
    raw = PROMPT_PATH.read_text(encoding="utf-8")
    if "## System Prompt" not in raw:
        return raw.strip()

    start = raw.index("## System Prompt") + len("## System Prompt")
    next_header = raw.find("\n## ", start)
    if next_header == -1:
        return raw[start:].strip()
    return raw[start:next_header].strip()


def _clamp_score(value: Any) -> int:
    """Clamp score-like values to int in [0, 100]."""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = 0.0
    return int(max(0, min(100, round(numeric))))


def _normalize_breakdown(raw: dict[str, Any] | None) -> dict[str, int]:
    raw = raw or {}
    return {key: _clamp_score(raw.get(key, 0)) for key in _BREAKDOWN_KEYS}


def _normalize_gaps(raw: Any) -> list[dict[str, str]]:
    gaps = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        requirement = str(item.get("requirement", "")).strip()
        mitigation = str(item.get("mitigation", "")).strip()
        severity = str(item.get("severity", "moderate")).strip().lower()
        if severity not in {"minor", "moderate", "major"}:
            severity = "moderate"
        if requirement and mitigation:
            gaps.append(
                {
                    "requirement": requirement,
                    "severity": severity,
                    "mitigation": mitigation,
                }
            )
    return gaps


def _normalize_strengths(raw: Any) -> list[dict[str, str]]:
    strengths = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        requirement = str(item.get("requirement", "")).strip()
        evidence = str(item.get("evidence", "")).strip()
        if requirement and evidence:
            strengths.append({"requirement": requirement, "evidence": evidence})
    return strengths


def _normalize_talking_points(raw: Any) -> list[str]:
    points = []
    for item in raw or []:
        point = str(item).strip()
        if point:
            points.append(point)
    return points


def _derive_recommendation(score: int) -> str:
    if score >= 75:
        return "APPLY"
    if score >= 50:
        return "MAYBE"
    return "SKIP"


def _extract_overall_score_from_text(text: str) -> int | None:
    patterns = [
        r"overall\s+fit\s+score\s*[:\-]?\s*(\d{1,3})(?:\s*/\s*100)?",
        r"fit\s+score\s*[:\-]?\s*(\d{1,3})(?:\s*/\s*100)?",
        r"\bscore\s*[:\-]?\s*(\d{1,3})(?:\s*/\s*100)\b",
        r"\b(\d{1,3})\s*/\s*100\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _clamp_score(match.group(1))
    return None


def _extract_explicit_recommendation_from_text(text: str) -> str | None:
    labeled = re.search(
        r"recommendation\s*[:\-]?\s*(APPLY|MAYBE|SKIP)",
        text,
        flags=re.IGNORECASE,
    )
    if labeled:
        return labeled.group(1).upper()

    direct = re.search(r"\b(APPLY|MAYBE|SKIP)\b", text, flags=re.IGNORECASE)
    if direct:
        return direct.group(1).upper()

    return None


def _extract_recommendation_from_text(text: str, score: int) -> str:
    explicit = _extract_explicit_recommendation_from_text(text)
    if explicit:
        return explicit
    return _derive_recommendation(score)


def _extract_breakdown_from_text(text: str, default_score: int) -> dict[str, int]:
    patterns = {
        "skills_match": r"skills?(?:\s+match|\s+alignment)?\s*[:\-]?\s*(\d{1,3})",
        "experience_level": r"experience(?:\s+level)?\s*[:\-]?\s*(\d{1,3})",
        "domain_relevance": r"domain(?:\s+relevance)?\s*[:\-]?\s*(\d{1,3})",
        "culture_signals": r"culture(?:\s+signals?)?\s*[:\-]?\s*(\d{1,3})",
        "location_match": r"location(?:\s+match)?\s*[:\-]?\s*(\d{1,3})",
    }
    breakdown = {key: default_score for key in _BREAKDOWN_KEYS}
    for key, pattern in patterns.items():
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            breakdown[key] = _clamp_score(match.group(1))
    return breakdown


def _extract_talking_points_from_text(text: str, max_items: int = 3) -> list[str]:
    points: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^\s*[-*•]\s+(.+)$", line.strip())
        if not match:
            continue
        value = match.group(1).strip()
        if value and value not in points:
            points.append(value)
        if len(points) >= max_items:
            break
    return points


def _parse_fit_payload_from_text(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text:
        return None

    score = _extract_overall_score_from_text(text)
    if score is None:
        explicit = _extract_explicit_recommendation_from_text(text)
        if explicit == "APPLY":
            score = 80
        elif explicit == "MAYBE":
            score = 60
        elif explicit == "SKIP":
            score = 35

    if score is None:
        return None

    return {
        "overall_score": score,
        "breakdown": _extract_breakdown_from_text(text, default_score=score),
        "gaps": [],
        "strengths": [],
        "talking_points": _extract_talking_points_from_text(text),
        "recommendation": _extract_recommendation_from_text(text, score=score),
    }


def _compose_user_prompt(persona: dict[str, Any], listing: dict[str, Any]) -> str:
    return (
        "persona:\n"
        f"{json.dumps(persona, indent=2)}\n\n"
        "listing:\n"
        f"{json.dumps(listing, indent=2)}"
    )


def score_fit(
    persona: dict[str, Any],
    listing: dict[str, Any],
    router: LLMRouter | None = None,
) -> dict[str, Any]:
    """
    Score candidate fit for a single listing.

    Returns B.3-like structure enriched with fit_id, listing_id, persona_id, scored_at.
    """
    if router is None:
        router = LLMRouter()

    system_prompt = _load_prompt()
    user_prompt = _compose_user_prompt(persona, listing)



    logger.info("Scoring fit for listing %s", listing.get("listing_id", "<unknown>"))
    try:
        raw = router.route_json(
            task_type="fit_scoring",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
    except LLMParseError as exc:
        recovered = _parse_fit_payload_from_text(exc.raw_response or "")
        if recovered is None:
            raise
        logger.warning(
            "Recovered fit score from non-JSON model output for listing %s",
            listing.get("listing_id", "<unknown>"),
        )
        raw = recovered

    breakdown = _normalize_breakdown(raw.get("breakdown"))
    overall_score = _clamp_score(raw.get("overall_score"))
    if overall_score == 0 and any(breakdown.values()):
        overall_score = int(round(sum(breakdown.values()) / len(breakdown)))

    recommendation = str(raw.get("recommendation", "")).strip().upper()
    if recommendation not in {"APPLY", "MAYBE", "SKIP"}:
        recommendation = _derive_recommendation(overall_score)

    return {
        "fit_id": str(uuid4()),
        "listing_id": listing.get("listing_id"),
        "persona_id": persona.get("persona_id"),
        "overall_score": overall_score,
        "breakdown": breakdown,
        "gaps": _normalize_gaps(raw.get("gaps")),
        "strengths": _normalize_strengths(raw.get("strengths")),
        "talking_points": _normalize_talking_points(raw.get("talking_points")),
        "recommendation": recommendation,
        "scored_at": datetime.now(timezone.utc).isoformat(),
    }


def score_and_attach_listing(
    persona: dict[str, Any],
    listing: dict[str, Any],
    router: LLMRouter | None = None,
) -> dict[str, Any]:
    """Convenience helper to append fit_score onto a listing object."""
    enriched = dict(listing)
    enriched["fit_score"] = score_fit(persona=persona, listing=listing, router=router)
    return enriched
