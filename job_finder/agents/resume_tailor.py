"""
Resume Tailor agent.

Generates role-specific, tokenized resume content using persona, listing,
fit analysis, and optional master bullet inventory.
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

logger = logging.getLogger("job_finder.agents.resume_tailor")

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "resume_tailor.md"


def _load_prompt() -> str:
    raw = PROMPT_PATH.read_text(encoding="utf-8")
    if "## System Prompt" not in raw:
        return raw.strip()
    start = raw.index("## System Prompt") + len("## System Prompt")
    next_header = raw.find("\n## ", start)
    if next_header == -1:
        return raw[start:].strip()
    return raw[start:next_header].strip()


def _read_master_bullets(master_bullets_path: str | Path | None) -> str:
    if not master_bullets_path:
        return ""
    path = Path(master_bullets_path)
    if not path.exists() or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("Could not read master bullets from %s: %s", path, exc)
        return ""


def _normalize_list(items: Any) -> list[str]:
    result: list[str] = []
    for item in items or []:
        text = str(item).strip()
        if text:
            result.append(text)
    return result


def _normalize_evidence_map(items: Any) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        requirement = str(item.get("requirement", "")).strip()
        evidence = str(item.get("evidence", "")).strip()
        if requirement and evidence:
            result.append({"requirement": requirement, "evidence": evidence})
    return result


def _extract_bullets(text: str, limit: int = 5) -> list[str]:
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


def _extract_resume_text(text: str) -> str:
    content = text.strip()
    for marker in (
        "## Tailored Resume Content",
        "## Tailored Resume Summary",
        "### Tailored Resume Content",
    ):
        if marker in content:
            content = content.split(marker, 1)[1].strip()
            break

    lines = content.splitlines()
    start_index = 0
    for idx, line in enumerate(lines):
        if re.match(r"^\s*(#|\*\*)?\s*\{\{[A-Z0-9_]+\}\}", line):
            start_index = idx
            break
    content = "\n".join(lines[start_index:]).strip()
    return content


def _parse_resume_payload_from_text(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text:
        return None

    resume_text = _extract_resume_text(text)
    if not resume_text:
        return None

    return {
        "resume_text": resume_text,
        "top_requirements": _extract_bullets(text, limit=5),
        "evidence_map": [],
        "notes": "Recovered from non-JSON model response.",
    }


def tailor_resume(
    persona: dict[str, Any],
    listing: dict[str, Any],
    fit_score: dict[str, Any] | None = None,
    router: LLMRouter | None = None,
    master_bullets_path: str | Path | None = "data/raw/master_bullets.md",
) -> dict[str, Any]:
    """
    Generate tokenized tailored resume content.

    Returns structured output ready for downstream document rendering.
    """
    if router is None:
        router = LLMRouter()

    master_bullets = _read_master_bullets(master_bullets_path)
    system_prompt = _load_prompt()
    payload = {
        "persona": persona,
        "listing": listing,
        "fit_score": fit_score or {},
        "master_bullets": master_bullets,
    }

    try:
        response = router.route_json(
            task_type="resume_tailoring",
            system_prompt=system_prompt,
            user_prompt=json.dumps(payload, indent=2),
        )
    except LLMParseError as exc:
        recovered = _parse_resume_payload_from_text(exc.raw_response or "")
        if recovered is None:
            raise
        logger.warning(
            "Recovered tailored resume from non-JSON model output for listing %s",
            listing.get("listing_id", "<unknown>"),
        )
        response = recovered

    resume_text = str(response.get("resume_text", "")).strip()

    # Handle alternative response formats from the LLM
    if not resume_text:
        # Try common alternative key names
        for alt_key in ("tailored_resume", "resume", "content", "text", "resume_content"):
            alt_val = response.get(alt_key)
            if isinstance(alt_val, str) and alt_val.strip():
                resume_text = alt_val.strip()
                logger.info("Found resume content under '%s' key (string)", alt_key)
                break
            elif isinstance(alt_val, dict):
                # Reconstruct resume text from nested dict structure
                parts = []
                for section_key in (
                    "professional_summary", "summary", "objective",
                    "skills", "technical_skills",
                    "experience", "work_experience", "employment",
                    "education",
                    "projects", "certifications", "awards",
                ):
                    section_val = alt_val.get(section_key)
                    if not section_val:
                        continue
                    header = section_key.replace("_", " ").title()
                    if isinstance(section_val, str):
                        parts.append(f"## {header}\n\n{section_val}")
                    elif isinstance(section_val, list):
                        items = []
                        for item in section_val:
                            if isinstance(item, str):
                                items.append(f"- {item}")
                            elif isinstance(item, dict):
                                # Format structured entries (e.g. experience entries)
                                title = item.get("title") or item.get("role") or item.get("position", "")
                                company = item.get("company") or item.get("organization") or item.get("employer", "")
                                dates = item.get("dates") or item.get("duration") or item.get("period", "")
                                desc = item.get("description") or item.get("summary", "")
                                bullets = item.get("bullets") or item.get("achievements") or item.get("responsibilities") or []

                                entry_header = f"**{title}**" if title else ""
                                if company:
                                    entry_header += f" | {company}"
                                if dates:
                                    entry_header += f" | {dates}"
                                if entry_header:
                                    items.append(entry_header)
                                if desc:
                                    items.append(desc)
                                for bullet in (bullets if isinstance(bullets, list) else []):
                                    items.append(f"- {bullet}")
                        if items:
                            parts.append(f"## {header}\n\n" + "\n".join(items))

                if parts:
                    # Add name placeholder at top
                    name_val = alt_val.get("name") or alt_val.get("full_name") or "{{FULL_NAME}}"
                    resume_text = f"# {name_val}\n\n" + "\n\n".join(parts)
                    logger.info(
                        "Reconstructed resume_text from nested '%s' dict (%d chars, %d sections)",
                        alt_key, len(resume_text), len(parts),
                    )
                else:
                    # Last resort: just serialize the nested dict as formatted text
                    resume_text = json.dumps(alt_val, indent=2)
                    logger.warning("Could not parse nested '%s' — using JSON dump (%d chars)", alt_key, len(resume_text))
                break

    logger.info(
        "tailor_resume result: resume_text_len=%d, response_keys=%s",
        len(resume_text),
        list(response.keys()) if isinstance(response, dict) else type(response).__name__,
    )
    if not resume_text:
        logger.warning(
            "resume_text is empty! Full response preview: %s",
            str(response)[:500],
        )
    return {
        "resume_id": str(uuid4()),
        "listing_id": listing.get("listing_id"),
        "persona_id": persona.get("persona_id"),
        "resume_text": resume_text,
        "top_requirements": _normalize_list(response.get("top_requirements")),
        "evidence_map": _normalize_evidence_map(response.get("evidence_map")),
        "notes": str(response.get("notes", "")).strip(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
