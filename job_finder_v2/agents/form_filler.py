"""
agents/form_filler.py — Worker: orchestrate the universal form filler.

Thin agent wrapper that:
    1. Loads per-company memory (if any) to refine the instruction.
    2. Calls UniversalFiller.fill() with the tailored resume + cover letter.
    3. Persists the resulting Application record and form notes.
    4. Returns the saved Application.

Pipeline.py uses this agent as the final step of every shadow/live run.
"""

from __future__ import annotations

import logging

from db.store import Application, FullProfile, Store
from filler.universal import FillResult, UniversalFiller

log = logging.getLogger(__name__)


class FormFillerAgent:
    """
    Wraps UniversalFiller with DB persistence and memory lookups.

    Usage:
        agent = FormFillerAgent(store, filler)
        result = await agent.run(
            app_id=app_id,
            apply_url=job.apply_url,
            profile=profile,
            resume_path="/path/resume.pdf",
            cover_letter="Dear hiring manager...",
            job_description="...",
            company="Stripe",
            submit=False,
        )
    """

    def __init__(
        self,
        store: Store,
        filler: UniversalFiller | None = None,
    ) -> None:
        self.store = store
        self.filler = filler or UniversalFiller()

    async def run(
        self,
        app_id: str,
        apply_url: str,
        profile: FullProfile,
        resume_path: str,
        cover_letter: str,
        job_description: str,
        company: str,
        submit: bool = False,
    ) -> FillResult:
        """
        Execute one form fill, persist what was learned, return the result.
        """
        # Inject per-company memory into the LLM-visible job description.
        memory = self.store.get_app_memory(company)
        augmented_desc = job_description
        if memory and memory.form_notes:
            augmented_desc = (
                f"{job_description}\n\n"
                f"PRIOR NOTES FOR {company.upper()}:\n{memory.form_notes}"
            )

        log.info(
            "form_filler_agent.start",
            extra={
                "app_id": app_id,
                "company": company,
                "has_memory": memory is not None,
                "submit": submit,
            },
        )

        result = await self.filler.fill(
            apply_url=apply_url,
            profile=profile,
            resume_path=resume_path,
            cover_letter=cover_letter,
            app_id=app_id,
            job_description=augmented_desc,
            submit=submit,
        )

        # Update long-term memory for this company
        notes_bits = []
        if result.custom_qa:
            notes_bits.append(f"Custom questions: {list(result.custom_qa.keys())}")
        if result.error:
            notes_bits.append(f"Last error: {result.error}")
        notes_bits.append(f"Status: {result.status}")
        form_notes = " | ".join(notes_bits)

        try:
            self.store.upsert_app_memory(
                company=company,
                ats_type=None,
                what_worked=("submit succeeded" if result.submitted else None),
                what_failed=(result.error if result.error else None),
                form_notes=form_notes,
            )
        except Exception as exc:  # noqa: BLE001 — memory is best-effort
            log.warning(
                "form_filler_agent.memory_write_failed",
                extra={"company": company, "error": str(exc)},
            )

        log.info(
            "form_filler_agent.complete",
            extra={
                "app_id": app_id,
                "status": result.status,
                "screenshots": len(result.screenshots),
                "duration_ms": result.duration_ms,
            },
        )
        return result
