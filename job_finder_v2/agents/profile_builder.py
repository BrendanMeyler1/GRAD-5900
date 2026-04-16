"""
agents/profile_builder.py — Worker: extract + refine user profile.

Two responsibilities:

1. **Extract from resume** — After the user uploads a PDF/DOCX, we parse
   text via utils.text and then ask Claude to structure it into profile
   fields (name, contact, education, experience, skills). Everything is
   persisted via the Store.

2. **Conversational profiling** — In chat, when the profile has gaps,
   this agent produces the next most useful question. Answers are
   saved as Q&A notes and, where structured, applied to the profile.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from db.store import FullProfile, Store
from llm.client import LLMClient, load_prompt

log = logging.getLogger(__name__)


@dataclass
class ExtractedProfile:
    """Structured profile data extracted from a resume."""

    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    city: str | None = None
    state: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None
    education: list[dict[str, Any]] | None = None
    experience: list[dict[str, Any]] | None = None
    skills: list[dict[str, Any]] | None = None


class ProfileBuilder:
    """
    Extract structured profile data from resume text, and drive
    conversational profile completion in chat.
    """

    def __init__(self, store: Store, llm: LLMClient | None = None) -> None:
        self.store = store
        self.llm = llm or LLMClient()
        self._prompt = load_prompt("profile_builder")

    # --- resume extraction ------------------------------------------------

    async def extract_from_resume(self, resume_text: str) -> ExtractedProfile:
        """
        Parse a resume into structured fields via Claude and persist them.

        The raw resume text is saved to user_profile.resume_raw_text so
        it's available for future tailoring calls. Structured pieces are
        upserted into the education / work_experience / skills tables.
        """
        if not resume_text or len(resume_text.strip()) < 50:
            log.warning("profile_builder.empty_resume")
            return ExtractedProfile()

        user_content = f"""Extract structured profile data from this resume. Return JSON with this exact shape:

{{
  "first_name": string | null,
  "last_name": string | null,
  "email": string | null,
  "phone": string | null,
  "city": string | null,
  "state": string | null,
  "linkedin_url": string | null,
  "github_url": string | null,
  "portfolio_url": string | null,
  "education": [
    {{"institution": str, "degree": str, "field": str, "graduation_year": int | null, "gpa": float | null}}
  ],
  "experience": [
    {{"company": str, "title": str, "start_date": str, "end_date": str | null, "is_current": bool, "description": str}}
  ],
  "skills": [
    {{"name": str, "category": "technical" | "soft" | "language" | "certification"}}
  ]
}}

RESUME TEXT:
---
{resume_text[:10000]}
---

Return ONLY the JSON object.
"""
        resp = await self.llm.chat(
            messages=[{"role": "user", "content": user_content}],
            system=self._prompt,
            max_tokens=4000,
        )
        text = resp if isinstance(resp, str) else str(resp)
        data = _parse_json(text)
        if not data:
            log.warning("profile_builder.extract_parse_failed")
            return ExtractedProfile()

        extracted = ExtractedProfile(
            first_name=data.get("first_name"),
            last_name=data.get("last_name"),
            email=data.get("email"),
            phone=data.get("phone"),
            city=data.get("city"),
            state=data.get("state"),
            linkedin_url=data.get("linkedin_url"),
            github_url=data.get("github_url"),
            portfolio_url=data.get("portfolio_url"),
            education=data.get("education") or [],
            experience=data.get("experience") or [],
            skills=data.get("skills") or [],
        )

        # Persist: top-level profile
        profile_update = {
            k: v
            for k, v in {
                "first_name": extracted.first_name,
                "last_name": extracted.last_name,
                "email": extracted.email,
                "phone": extracted.phone,
                "city": extracted.city,
                "state": extracted.state,
                "linkedin_url": extracted.linkedin_url,
                "github_url": extracted.github_url,
                "portfolio_url": extracted.portfolio_url,
                "resume_raw_text": resume_text,
            }.items()
            if v
        }
        self.store.upsert_profile(profile_update)

        # Persist: education
        for edu in extracted.education or []:
            try:
                self.store.add_education(edu)
            except Exception as exc:  # noqa: BLE001
                log.warning("profile_builder.edu_persist_failed", extra={"error": str(exc)})

        # Persist: experience
        for exp in extracted.experience or []:
            try:
                self.store.add_experience(exp)
            except Exception as exc:  # noqa: BLE001
                log.warning("profile_builder.exp_persist_failed", extra={"error": str(exc)})

        # Persist: skills
        if extracted.skills:
            try:
                self.store.upsert_skills(extracted.skills)
            except Exception as exc:  # noqa: BLE001
                log.warning("profile_builder.skills_persist_failed", extra={"error": str(exc)})

        log.info(
            "profile_builder.extract_complete",
            extra={
                "candidate_name": (extracted.first_name or "") + " " + (extracted.last_name or ""),
                "education_count": len(extracted.education or []),
                "experience_count": len(extracted.experience or []),
                "skills_count": len(extracted.skills or []),
            },
        )
        return extracted

    # --- conversational profiling ----------------------------------------

    async def ask_next_question(self, profile: FullProfile) -> str | None:
        """
        Determine the single most useful question to ask to close profile
        gaps. Returns None if the profile is already complete enough.
        """
        missing = self._identify_gaps(profile)
        if not missing:
            return None

        user_content = f"""The user's profile is missing: {', '.join(missing)}.

Profile snapshot:
{profile.to_context_string()[:1500]}

Ask ONE focused, friendly question that will fill the MOST IMPORTANT missing field. Keep it to one sentence. Do not list multiple questions.
"""
        resp = await self.llm.chat(
            messages=[{"role": "user", "content": user_content}],
            system=self._prompt,
            max_tokens=200,
        )
        return resp.strip() if isinstance(resp, str) else str(resp).strip()

    async def answer_into_profile(
        self, question: str, answer: str, profile: FullProfile
    ) -> dict[str, Any]:
        """
        Interpret a free-text answer and apply structured updates to the
        profile where possible. Also records the Q&A as a user_qa note.
        """
        user_content = f"""The user was asked: "{question}"
They answered: "{answer}"

Based on this exchange, return JSON:
{{
  "profile_updates": {{ <zero or more top-level user_profile fields to set> }},
  "category": "preference" | "experience" | "background" | "other"
}}

Valid profile fields: target_salary_min (int), target_salary_max (int),
remote_preference ("remote"|"hybrid"|"onsite"|"flexible"),
willing_to_relocate (bool), availability_weeks (int), visa_status (str),
requires_sponsorship (bool), linkedin_url, github_url, portfolio_url,
city, state, zip, gender, race_ethnicity, veteran_status, disability_status.

If the answer doesn't map to a field, return {{"profile_updates": {{}}, "category": "other"}}.

Return ONLY the JSON.
"""
        resp = await self.llm.chat(
            messages=[{"role": "user", "content": user_content}],
            system=self._prompt,
            max_tokens=400,
        )
        text = resp if isinstance(resp, str) else str(resp)
        data = _parse_json(text) or {"profile_updates": {}, "category": "other"}

        updates = data.get("profile_updates") or {}
        category = data.get("category", "other")

        # Persist Q&A note
        try:
            self.store.add_qa(question=question, answer=answer, category=category)
        except Exception as exc:  # noqa: BLE001
            log.warning("profile_builder.qa_persist_failed", extra={"error": str(exc)})

        # Persist structured updates
        if updates and isinstance(updates, dict):
            try:
                self.store.upsert_profile(updates)
            except Exception as exc:  # noqa: BLE001
                log.warning("profile_builder.update_failed", extra={"error": str(exc)})

        return {"category": category, "updates": updates}

    # --- helpers ---------------------------------------------------------

    @staticmethod
    def _identify_gaps(profile: FullProfile) -> list[str]:
        """Return human-readable labels for missing profile fields."""
        p = profile.profile
        missing: list[str] = []
        if not p.first_name or not p.last_name:
            missing.append("full name")
        if not p.email:
            missing.append("email")
        if not p.phone:
            missing.append("phone number")
        if not p.city or not p.state:
            missing.append("location (city/state)")
        if p.target_salary_min is None or p.target_salary_max is None:
            missing.append("target salary range")
        if not p.remote_preference:
            missing.append("remote/hybrid/onsite preference")
        if not profile.experience:
            missing.append("work experience")
        if not profile.education:
            missing.append("education")
        if not profile.skills:
            missing.append("skills")
        return missing


def _parse_json(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from possibly-fenced LLM output."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n", "", t)
        t = re.sub(r"\n```$", "", t)
    m = re.search(r"\{.*\}", t, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
