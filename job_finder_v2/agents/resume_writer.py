"""
agents/resume_writer.py — Worker: tailor resume + cover letter per job.

Two Claude calls per application:
    1. Tailor resume — input: base resume + job description + profile.
       Output: markdown resume that emphasizes relevant experience without
       fabricating anything.
    2. Cover letter — input: job description + tailored resume.
       Output: 3-paragraph cover letter with specific, non-generic content.

Both outputs are written to data/generated/{app_id}/ as .md and .pdf files.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from config import settings
from db.store import FullProfile
from llm.client import LLMClient, load_prompt
from utils.pdf import export_cover_letter_pdf, export_resume_pdf

log = logging.getLogger(__name__)


@dataclass
class ResumeResult:
    """Output of a resume tailoring call."""

    text: str
    file_path: str       # PDF path, ready for form upload
    markdown_path: str   # Raw .md path, for chat display + diff view


@dataclass
class CoverLetterResult:
    """Output of a cover letter generation call."""

    text: str
    file_path: str       # PDF path
    markdown_path: str


class ResumeWriter:
    """
    Tailors a resume and generates a cover letter for a specific job.

    Usage:
        writer = ResumeWriter(llm)
        resume = await writer.tailor(app_id, job_title, job_description, company, profile)
        cover  = await writer.cover_letter(app_id, job_title, company, job_description, profile, resume)
    """

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or LLMClient()
        self._resume_prompt = load_prompt("resume_writer")
        self._cover_prompt = load_prompt("cover_letter")

    async def tailor(
        self,
        app_id: str,
        job_title: str,
        job_description: str,
        company: str,
        profile: FullProfile,
    ) -> ResumeResult:
        """
        Produce a tailored resume for a specific job.

        The LLM is instructed to NEVER fabricate experience. If the base
        resume has gaps for a requirement, the tailored resume will honestly
        lack that content too.
        """
        base_resume = profile.profile.resume_raw_text or profile.to_context_string()
        p = profile.profile

        # Build contact line from actual profile fields so Claude can't invent them
        contact_parts = [x for x in [p.email, p.phone] if x]
        if p.city or p.state:
            contact_parts.append(", ".join(filter(None, [p.city, p.state])))
        if p.linkedin_url:
            contact_parts.append(p.linkedin_url)
        if p.github_url:
            contact_parts.append(p.github_url)
        contact_line = " · ".join(contact_parts) if contact_parts else ""

        user_content = f"""TARGET JOB
Company: {company}
Title: {job_title}

Job description:
{job_description[:6000]}

CANDIDATE PROFILE
{profile.to_context_string()}

BASE RESUME (source of truth — never contradict or invent new experience):
---
{base_resume[:8000]}
---

CRITICAL IDENTITY RULE: The candidate's name is **{p.full_name}**. You MUST use this exact name in the resume header. Never change, generalize, or replace it. The contact line is already filled in below — copy it verbatim.

Write the tailored resume in clean markdown using this EXACT header (do not alter the name or contact line):

# {p.full_name}
{f'*{contact_line}*' if contact_line else ''}

## Summary
<2-3 sentences tailored to this specific role — written in first person, referencing the company>

## Experience
### [Exact Title from original] — [Company] ([start date] – [end date or Present])
- Strongest, most relevant bullet first
- Additional bullets as needed

## Education
[Keep exactly as in the base resume]

## Skills
[Reordered so the most relevant skills for this role appear first]

Output ONLY the markdown resume. No explanation, no code fences, no preamble.
"""
        log.info(
            "resume_writer.tailor_start",
            extra={"app_id": app_id, "company": company, "job_title": job_title},
        )
        resp = await self.llm.chat(
            messages=[{"role": "user", "content": user_content}],
            system=self._resume_prompt,
            max_tokens=4000,
        )
        text = resp if isinstance(resp, str) else str(resp)
        text = _strip_code_fences(text)

        # Write markdown + PDF
        md_path = Path(settings.generated_dir) / app_id / "resume.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(text, encoding="utf-8")

        try:
            pdf_path = export_resume_pdf(
                app_id=app_id, resume_text=text, generated_dir=settings.generated_dir
            )
        except Exception as exc:  # noqa: BLE001 — PDF is not critical path
            log.warning(
                "resume_writer.pdf_failed",
                extra={"app_id": app_id, "error": str(exc)},
            )
            pdf_path = str(md_path)  # fall back to .md path so pipeline continues

        log.info(
            "resume_writer.tailor_complete",
            extra={"app_id": app_id, "chars": len(text), "pdf": pdf_path},
        )
        return ResumeResult(text=text, file_path=pdf_path, markdown_path=str(md_path))

    async def cover_letter(
        self,
        app_id: str,
        job_title: str,
        company: str,
        job_description: str,
        profile: FullProfile,
        tailored_resume: ResumeResult | None = None,
    ) -> CoverLetterResult:
        """
        Generate a cover letter. Uses the tailored resume (if given) so
        the letter's themes align with the resume's emphasis.
        """
        resume_ref = (
            f"TAILORED RESUME (reference only — mirror its tone):\n{tailored_resume.text[:4000]}"
            if tailored_resume
            else ""
        )
        user_content = f"""TARGET JOB
Company: {company}
Title: {job_title}

Job description:
{job_description[:4000]}

CANDIDATE PROFILE:
{profile.to_context_string()}

{resume_ref}

Write the cover letter in the voice of the candidate. Output ONLY the letter body — no headers, no date, no "Dear Hiring Manager" unless that sounds natural. Three paragraphs. 200-280 words. No banned phrases.
"""
        log.info(
            "resume_writer.cover_start",
            extra={"app_id": app_id, "company": company},
        )
        resp = await self.llm.chat(
            messages=[{"role": "user", "content": user_content}],
            system=self._cover_prompt,
            max_tokens=1500,
        )
        text = resp if isinstance(resp, str) else str(resp)
        text = _strip_code_fences(text)

        md_path = Path(settings.generated_dir) / app_id / "cover_letter.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(text, encoding="utf-8")

        try:
            pdf_path = export_cover_letter_pdf(
                app_id=app_id,
                cover_letter_text=text,
                generated_dir=settings.generated_dir,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "resume_writer.cover_pdf_failed",
                extra={"app_id": app_id, "error": str(exc)},
            )
            pdf_path = str(md_path)

        log.info(
            "resume_writer.cover_complete",
            extra={"app_id": app_id, "chars": len(text)},
        )
        return CoverLetterResult(text=text, file_path=pdf_path, markdown_path=str(md_path))


def _strip_code_fences(text: str) -> str:
    """Remove ``` markdown fences if the LLM wrapped its output."""
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        # Drop first (```lang) and last (```) lines if they're fences
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t
