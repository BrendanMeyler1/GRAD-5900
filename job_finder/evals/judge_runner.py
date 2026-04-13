"""
LLM-as-Judge runner for resume and cover-letter quality checks.

Phase 2 Step 9 implementation.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from llm_router.router import LLMRouter

PROMPTS_DIR = Path(__file__).parent.parent / "prompts" / "judges"
RESUME_PROMPT_PATH = PROMPTS_DIR / "resume_judge.md"
COVER_LETTER_PROMPT_PATH = PROMPTS_DIR / "cover_letter_judge.md"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_prompt(path: Path) -> str:
    raw = path.read_text(encoding="utf-8")
    if "## System Prompt" not in raw:
        return raw.strip()
    start = raw.index("## System Prompt") + len("## System Prompt")
    next_header = raw.find("\n## ", start)
    if next_header == -1:
        return raw[start:].strip()
    return raw[start:next_header].strip()


def _clamp_score(value: Any) -> int:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = 0.0
    return int(max(0, min(100, round(numeric))))


def _normalize_dimension_scores(
    payload: dict[str, Any],
    keys: list[str],
) -> dict[str, int]:
    source = payload.get("dimension_scores", {}) if isinstance(payload, dict) else {}
    return {key: _clamp_score(source.get(key, 0)) for key in keys}


def _normalize_issues(payload: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for item in payload.get("issues", []) or []:
        if not isinstance(item, dict):
            continue
        severity = str(item.get("severity", "moderate")).strip().lower()
        if severity not in {"minor", "moderate", "major"}:
            severity = "moderate"
        message = str(item.get("message", "")).strip()
        fix = str(item.get("fix", "")).strip()
        if message:
            issues.append({"severity": severity, "message": message, "fix": fix})
    return issues


def _normalize_strengths(payload: dict[str, Any]) -> list[str]:
    strengths: list[str] = []
    for item in payload.get("strengths", []) or []:
        text = str(item).strip()
        if text:
            strengths.append(text)
    return strengths


def _infer_pass(overall_score: int, issues: list[dict[str, str]], declared: Any) -> bool:
    if isinstance(declared, bool):
        base = declared
    else:
        base = overall_score >= 70

    has_major = any(issue.get("severity") == "major" for issue in issues)
    return bool(base and not has_major)


def evaluate_resume(
    persona: dict[str, Any],
    listing: dict[str, Any],
    resume_text: str,
    router: LLMRouter | None = None,
) -> dict[str, Any]:
    """Run resume quality judge and normalize output."""
    router = router or LLMRouter()
    response = router.route_json(
        task_type="resume_judge",
        system_prompt=_load_prompt(RESUME_PROMPT_PATH),
        user_prompt=json.dumps(
            {
                "persona": persona,
                "listing": listing,
                "resume_text": resume_text,
            },
            indent=2,
        ),
    )

    dimensions = _normalize_dimension_scores(
        response,
        keys=["relevance", "specificity", "truthfulness", "ats_readability"],
    )
    overall = _clamp_score(response.get("overall_score"))
    if overall == 0 and any(dimensions.values()):
        overall = int(round(sum(dimensions.values()) / len(dimensions)))

    issues = _normalize_issues(response)
    strengths = _normalize_strengths(response)
    passed = _infer_pass(overall, issues, response.get("pass"))

    return {
        "judge": "resume",
        "overall_score": overall,
        "dimension_scores": dimensions,
        "strengths": strengths,
        "issues": issues,
        "pass": passed,
        "summary": str(response.get("summary", "")).strip(),
        "evaluated_at": _utc_now(),
    }


def evaluate_cover_letter(
    persona: dict[str, Any],
    listing: dict[str, Any],
    cover_letter_text: str,
    router: LLMRouter | None = None,
) -> dict[str, Any]:
    """Run cover-letter quality judge and normalize output."""
    router = router or LLMRouter()
    response = router.route_json(
        task_type="cover_letter_judge",
        system_prompt=_load_prompt(COVER_LETTER_PROMPT_PATH),
        user_prompt=json.dumps(
            {
                "persona": persona,
                "listing": listing,
                "cover_letter_text": cover_letter_text,
            },
            indent=2,
        ),
    )

    dimensions = _normalize_dimension_scores(
        response,
        keys=["role_alignment", "specificity", "truthfulness", "writing_quality"],
    )
    overall = _clamp_score(response.get("overall_score"))
    if overall == 0 and any(dimensions.values()):
        overall = int(round(sum(dimensions.values()) / len(dimensions)))

    issues = _normalize_issues(response)
    strengths = _normalize_strengths(response)
    passed = _infer_pass(overall, issues, response.get("pass"))

    return {
        "judge": "cover_letter",
        "overall_score": overall,
        "dimension_scores": dimensions,
        "strengths": strengths,
        "issues": issues,
        "pass": passed,
        "summary": str(response.get("summary", "")).strip(),
        "evaluated_at": _utc_now(),
    }


def run_judges(
    persona: dict[str, Any],
    listing: dict[str, Any],
    tailored_resume_text: str,
    cover_letter_text: str,
    router: LLMRouter | None = None,
) -> dict[str, Any]:
    """
    Run both resume and cover-letter judges and return consolidated result.
    """
    router = router or LLMRouter()
    resume_eval = evaluate_resume(
        persona=persona,
        listing=listing,
        resume_text=tailored_resume_text,
        router=router,
    )
    cover_eval = evaluate_cover_letter(
        persona=persona,
        listing=listing,
        cover_letter_text=cover_letter_text,
        router=router,
    )

    overall = int(round((resume_eval["overall_score"] + cover_eval["overall_score"]) / 2))
    return {
        "overall_score": overall,
        "pass": bool(resume_eval["pass"] and cover_eval["pass"]),
        "resume": resume_eval,
        "cover_letter": cover_eval,
        "evaluated_at": _utc_now(),
    }
