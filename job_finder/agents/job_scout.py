"""
Job Scout agent.

Evaluates discovered listings with ghost-job "alive" signals, computes
smart-skip recommendations, and returns a ranked decision queue.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from llm_router.router import LLMRouter
from retrieval.hybrid_search import HybridSearch

logger = logging.getLogger("job_finder.agents.job_scout")

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "job_scout.md"

_AGGREGATOR_DOMAINS = {
    "indeed.com",
    "ziprecruiter.com",
    "monster.com",
    "careerbuilder.com",
    "jooble.org",
}

_SIGNAL_WEIGHTS = {
    "posting_freshness": 0.20,
    "recruiter_activity": 0.20,
    "headcount_trend": 0.10,
    "financial_health": 0.10,
    "url_provenance": 0.10,
    "duplicate_check": 0.15,
    "portal_check": 0.15,
}


def _load_prompt() -> str:
    raw = PROMPT_PATH.read_text(encoding="utf-8")
    if "## System Prompt" not in raw:
        return raw.strip()

    start = raw.index("## System Prompt") + len("## System Prompt")
    next_header = raw.find("\n## ", start)
    if next_header == -1:
        return raw[start:].strip()
    return raw[start:next_header].strip()


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    cleaned = value.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y/%m/%d"):
        try:
            parsed = datetime.strptime(cleaned, fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        return None


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _extract_skills(persona: dict[str, Any]) -> set[str]:
    skills = persona.get("skills", {}) if isinstance(persona, dict) else {}
    values: set[str] = set()
    for group in ("languages", "frameworks", "infrastructure", "domains"):
        for item in skills.get(group, []) or []:
            values.add(str(item).strip().lower())
    return {v for v in values if v}


def _extract_requirements_text(listing: dict[str, Any]) -> str:
    role = listing.get("role", {}) if isinstance(listing, dict) else {}
    requirements = role.get("requirements", []) or []
    description = role.get("description_text", "") or ""
    return "\n".join([*map(str, requirements), str(description)])


def _fit_hint_score(persona: dict[str, Any] | None, listing: dict[str, Any]) -> float:
    if not persona:
        return 50.0

    persona_skills = _extract_skills(persona)
    if not persona_skills:
        return 50.0

    requirement_tokens = set(
        re.findall(r"[a-z0-9+#.]+", _extract_requirements_text(listing).lower())
    )
    if not requirement_tokens:
        return 50.0

    direct_matches = sum(1 for skill in persona_skills if skill in requirement_tokens)
    score = 50.0 + (50.0 * (direct_matches / max(len(persona_skills), 1)))
    return _clamp(score, 0.0, 100.0)


def _posting_freshness(posted_date: str | None) -> float:
    parsed = _parse_date(posted_date)
    if parsed is None:
        return 0.5

    now = datetime.now(timezone.utc)
    age_days = max(0, (now.date() - parsed.date()).days)
    if age_days <= 7:
        return 1.0
    if age_days <= 14:
        return 0.85
    if age_days <= 30:
        return 0.65
    if age_days <= 60:
        return 0.35
    return 0.15


def _url_provenance(url: str | None) -> float:
    if not url:
        return 0.0

    parsed = urlparse(url)
    domain = (parsed.netloc or "").lower()
    if not domain:
        return 0.2

    for aggregator in _AGGREGATOR_DOMAINS:
        if aggregator in domain:
            return 0.45

    if "redirect" in parsed.path.lower() or "url=" in parsed.query.lower():
        return 0.4
    return 1.0


def _portal_check(apply_url: str | None) -> float:
    if not apply_url:
        return 0.0
    parsed = urlparse(apply_url)
    if parsed.scheme not in {"http", "https"}:
        return 0.0
    if not parsed.netloc:
        return 0.0
    return 1.0


def _infer_ats_type(source_url: str | None, apply_url: str | None) -> str:
    joined = f"{source_url or ''} {apply_url or ''}".lower()
    if "greenhouse" in joined or "gh_jid=" in joined:
        return "greenhouse"
    if "lever.co" in joined or "jobs.lever" in joined:
        return "lever"
    if "workday" in joined:
        return "workday"
    return "unknown"


def _build_duplicate_scores(
    listings: list[dict[str, Any]],
    hybrid_search: HybridSearch | None = None,
) -> dict[int, float]:
    """Compute simple duplicate scores per listing index."""
    fingerprints: dict[str, list[int]] = {}
    for idx, listing in enumerate(listings):
        company = (
            listing.get("company", {}).get("name")
            if isinstance(listing.get("company"), dict)
            else ""
        )
        title = (
            listing.get("role", {}).get("title")
            if isinstance(listing.get("role"), dict)
            else ""
        )
        location = (
            listing.get("role", {}).get("location")
            if isinstance(listing.get("role"), dict)
            else ""
        )
        fingerprint = "|".join(
            [str(company).strip().lower(), str(title).strip().lower(), str(location).strip().lower()]
        )
        fingerprints.setdefault(fingerprint, []).append(idx)

    scores = {idx: 1.0 for idx in range(len(listings))}
    for indexes in fingerprints.values():
        if len(indexes) > 1:
            score = max(0.2, 1.0 - (0.3 * (len(indexes) - 1)))
            for idx in indexes:
                scores[idx] = score

    # Optional semantic duplicate adjustment with HybridSearch
    if hybrid_search is not None and listings:
        docs = []
        for i, listing in enumerate(listings):
            text = _extract_requirements_text(listing)
            docs.append({"id": str(i), "text": text, "metadata": {}})
        hybrid_search.index_documents(docs)

        for i, listing in enumerate(listings):
            text = _extract_requirements_text(listing)
            neighbors = hybrid_search.search(text, k=2)
            if len(neighbors) > 1:
                neighbor = neighbors[1]
                if neighbor["score"] >= 0.9:
                    scores[i] = min(scores[i], 0.5)

    return scores


def _llm_soft_signals(
    listing: dict[str, Any],
    persona: dict[str, Any] | None,
    router: LLMRouter,
) -> tuple[dict[str, float], list[str], str]:
    """Estimate softer signals with the LLM (optional)."""
    system_prompt = _load_prompt()
    payload = {
        "persona": persona or {},
        "listing": listing,
    }
    response = router.route_json(
        task_type="job_scouting",
        system_prompt=system_prompt,
        user_prompt=json.dumps(payload, indent=2),
    )

    signals = response.get("signals", {}) if isinstance(response, dict) else {}
    recruiter = _clamp(float(signals.get("recruiter_activity", 0.5)))
    headcount = _clamp(float(signals.get("headcount_trend", 0.5)))
    financial = _clamp(float(signals.get("financial_health", 0.5)))
    flags = [str(flag) for flag in response.get("risk_flags", []) or []]
    notes = str(response.get("notes", "")).strip()
    return {
        "recruiter_activity": recruiter,
        "headcount_trend": headcount,
        "financial_health": financial,
    }, flags, notes


def _smart_skip_reasons(
    listing: dict[str, Any],
    alive_score: dict[str, Any],
    fit_hint: float,
) -> list[str]:
    reasons: list[str] = []
    signals = alive_score.get("signals", {})
    portal_ok = float(signals.get("portal_check", 0.0))
    if portal_ok < 0.5:
        reasons.append("broken_apply_link")

    if float(alive_score.get("composite", 0.0)) < 0.4 and fit_hint < 60:
        reasons.append("low_alive_low_fit")

    high_sensitive = int(listing.get("high_sensitivity_fields_required", 0) or 0)
    if high_sensitive >= 3:
        reasons.append("excessive_high_sensitivity_fields")

    return reasons


def _compute_alive_score(
    listing: dict[str, Any],
    duplicate_score: float,
    soft_signals: dict[str, float],
    llm_flags: list[str] | None = None,
) -> dict[str, Any]:
    role = listing.get("role", {}) if isinstance(listing.get("role"), dict) else {}
    posted_date = role.get("posted_date")
    source_url = listing.get("source_url")
    apply_url = listing.get("apply_url")

    signals = {
        "posting_freshness": _posting_freshness(posted_date),
        "recruiter_activity": _clamp(float(soft_signals.get("recruiter_activity", 0.5))),
        "headcount_trend": _clamp(float(soft_signals.get("headcount_trend", 0.5))),
        "financial_health": _clamp(float(soft_signals.get("financial_health", 0.5))),
        "url_provenance": _url_provenance(source_url),
        "duplicate_check": _clamp(duplicate_score),
        "portal_check": _portal_check(apply_url),
    }

    composite = sum(signals[name] * _SIGNAL_WEIGHTS[name] for name in _SIGNAL_WEIGHTS)
    flags: list[str] = []
    if signals["posting_freshness"] < 0.4:
        flags.append("stale_posting")
    if signals["url_provenance"] < 0.5:
        flags.append("aggregator_or_redirect_link")
    if signals["duplicate_check"] <= 0.7:
        flags.append("possible_duplicate_posting")
    if signals["portal_check"] < 0.5:
        flags.append("apply_link_invalid")
    if llm_flags:
        flags.extend(llm_flags)

    # Keep order stable while deduplicating
    flags = list(dict.fromkeys(flags))

    return {
        "composite": round(_clamp(composite), 3),
        "signals": {key: round(value, 3) for key, value in signals.items()},
        "flags": flags,
    }


def scout_jobs(
    listings: list[dict[str, Any]],
    persona: dict[str, Any] | None = None,
    router: LLMRouter | None = None,
    hybrid_search: HybridSearch | None = None,
    use_llm: bool = False,
    max_results: int = 25,
) -> list[dict[str, Any]]:
    """
    Evaluate + rank discovered job listings.

    Returns listings enriched with:
    - alive_score
    - fit_hint_score
    - smart_skip_recommended
    - smart_skip_reasons
    - ats_type
    """
    if not listings:
        return []

    duplicate_scores = _build_duplicate_scores(listings, hybrid_search=hybrid_search)
    ranked: list[dict[str, Any]] = []

    for idx, original in enumerate(listings):
        listing = dict(original)
        listing.setdefault("listing_id", str(uuid4()))

        role = listing.get("role", {}) if isinstance(listing.get("role"), dict) else {}
        listing["role"] = role
        role.setdefault("requirements", [])
        role.setdefault("description_text", "")

        if "scraped_at" not in listing:
            listing["scraped_at"] = datetime.now(timezone.utc).isoformat()
        if not listing.get("apply_url"):
            listing["apply_url"] = listing.get("source_url")

        soft_signals = {
            "recruiter_activity": 0.5,
            "headcount_trend": 0.5,
            "financial_health": 0.5,
        }
        llm_flags: list[str] = []
        scout_notes = ""

        if use_llm and router is not None:
            try:
                soft_signals, llm_flags, scout_notes = _llm_soft_signals(
                    listing=listing,
                    persona=persona,
                    router=router,
                )
            except Exception as exc:
                logger.warning("Job Scout LLM signal estimation failed: %s", exc)

        alive_score = _compute_alive_score(
            listing=listing,
            duplicate_score=duplicate_scores[idx],
            soft_signals=soft_signals,
            llm_flags=llm_flags,
        )
        fit_hint = float(_fit_hint_score(persona, listing))

        skip_reasons = _smart_skip_reasons(
            listing=listing, alive_score=alive_score, fit_hint=fit_hint
        )
        smart_skip = bool(skip_reasons)

        listing["alive_score"] = alive_score
        listing["fit_hint_score"] = round(fit_hint, 1)
        listing["smart_skip_recommended"] = smart_skip
        listing["smart_skip_reasons"] = skip_reasons
        listing["ats_type"] = _infer_ats_type(
            listing.get("source_url"), listing.get("apply_url")
        )
        if scout_notes:
            listing["scout_notes"] = scout_notes

        priority_score = (alive_score["composite"] * 100.0 * 0.7) + (fit_hint * 0.3)
        listing["priority_score"] = round(priority_score, 2)
        ranked.append(listing)

    ranked.sort(
        key=lambda item: (
            item.get("smart_skip_recommended", False),
            -float(item.get("priority_score", 0.0)),
        )
    )
    return ranked[: max(1, max_results)]


def build_decision_queue(
    listings: list[dict[str, Any]],
    persona: dict[str, Any] | None = None,
    router: LLMRouter | None = None,
    hybrid_search: HybridSearch | None = None,
    use_llm: bool = False,
    max_results: int = 25,
) -> dict[str, list[dict[str, Any]]]:
    """Return queue split into primary candidates and deprioritized candidates."""
    ranked = scout_jobs(
        listings=listings,
        persona=persona,
        router=router,
        hybrid_search=hybrid_search,
        use_llm=use_llm,
        max_results=max_results,
    )
    queue = [item for item in ranked if not item.get("smart_skip_recommended", False)]
    deprioritized = [item for item in ranked if item.get("smart_skip_recommended", False)]
    return {"queue": queue, "deprioritized": deprioritized}
