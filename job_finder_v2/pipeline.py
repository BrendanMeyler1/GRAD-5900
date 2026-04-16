"""
pipeline.py — Full application pipeline.

Orchestrates the end-to-end flow for a single job application:

    Step 1: Validate job + profile readiness
    Step 2: Tailor resume (resume_writer.tailor)
    Step 3: Generate cover letter (resume_writer.cover_letter)
    Step 4: Create Application row (status=shadow_running)
    Step 5: Fill form (form_filler.run)
    Step 6: Persist screenshots, fill log, custom Q&A
    Step 7: Set final status:
              - mode=shadow → status=shadow_review (human reviews next)
              - mode=live → status=submitted (or failed)

Called from /api/apply/{job_id}/shadow and /api/apply/{app_id}/approve
as a FastAPI BackgroundTask.

Also exposes a `tailor_only` fast path that the orchestrator can use to
preview tailored documents without running the filler.
"""

from __future__ import annotations

import logging
import time
from typing import Literal

from agents.form_filler import FormFillerAgent
from agents.resume_writer import ResumeWriter
from db.store import Application, Store
from filler.universal import UniversalFiller
from llm.client import LLMClient

log = logging.getLogger(__name__)

Mode = Literal["shadow", "live"]


class Pipeline:
    """
    The full tailor → fill → persist pipeline.

    Instances are cheap to construct; internal workers hold their own
    resources (LLM client, filler browser). Keep one Pipeline in the
    FastAPI app state and reuse it.
    """

    def __init__(
        self,
        store: Store,
        llm: LLMClient | None = None,
        resume_writer: ResumeWriter | None = None,
        form_filler: FormFillerAgent | None = None,
        filler: UniversalFiller | None = None,
    ) -> None:
        self.store = store
        self.llm = llm or LLMClient()
        self.resume_writer = resume_writer or ResumeWriter(self.llm)
        self._filler = filler or UniversalFiller(self.llm)
        self.form_filler = form_filler or FormFillerAgent(store, self._filler)

    async def close(self) -> None:
        """Release browser + HTTP resources."""
        try:
            await self._filler.close()
        except Exception as exc:  # noqa: BLE001
            log.warning("pipeline.close_error", extra={"error": str(exc)})

    # --- public entrypoints ---------------------------------------------

    async def tailor_only(self, job_id: str) -> Application:
        """
        Generate tailored docs for a job without running the browser.

        Creates an Application record with status='pending' that the
        user can inspect in chat before deciding to shadow-apply.
        """
        job = self._require_job(job_id)
        profile = self._require_profile()

        # Pre-create app record so we have an ID for file paths
        app = self.store.create_application(job_id=job_id, status="pending")

        resume = await self.resume_writer.tailor(
            app_id=app.id,
            job_title=job.title or "",
            job_description=job.description or "",
            company=job.company or "",
            profile=profile,
        )
        cover = await self.resume_writer.cover_letter(
            app_id=app.id,
            job_title=job.title or "",
            company=job.company or "",
            job_description=job.description or "",
            profile=profile,
            tailored_resume=resume,
        )

        updated = self.store.update_application(
            app.id,
            resume_tailored_text=resume.text,
            resume_tailored_path=resume.file_path,
            cover_letter_text=cover.text,
        )
        log.info(
            "pipeline.tailor_only_complete",
            extra={"app_id": app.id, "job_id": job_id, "company": job.company},
        )
        return updated

    async def run_application(
        self,
        job_id: str,
        mode: Mode = "shadow",
        existing_app_id: str | None = None,
    ) -> Application:
        """
        Full pipeline: tailor → fill → persist.

        If `existing_app_id` is provided, we reuse that application's
        tailored documents and jump straight to filling. This is the
        path used when the user clicks "Approve & Submit" on a shadow
        application they already reviewed.
        """
        start = time.monotonic()
        job = self._require_job(job_id)
        profile = self._require_profile()

        if existing_app_id:
            app = self.store.get_application(existing_app_id)
            if app is None:
                raise ValueError(f"Application {existing_app_id} not found")
            if app.job_id != job_id:
                raise ValueError(
                    f"Application {existing_app_id} belongs to job {app.job_id}, not {job_id}"
                )
            resume_text = app.resume_tailored_text or ""
            resume_path = app.resume_tailored_path or ""
            cover_text = app.cover_letter_text or ""
        else:
            app = self.store.create_application(job_id=job_id, status="shadow_running")

            resume = await self.resume_writer.tailor(
                app_id=app.id,
                job_title=job.title or "",
                job_description=job.description or "",
                company=job.company or "",
                profile=profile,
            )
            cover = await self.resume_writer.cover_letter(
                app_id=app.id,
                job_title=job.title or "",
                company=job.company or "",
                job_description=job.description or "",
                profile=profile,
                tailored_resume=resume,
            )
            resume_text = resume.text
            resume_path = resume.file_path
            cover_text = cover.text
            self.store.update_application(
                app.id,
                resume_tailored_text=resume_text,
                resume_tailored_path=resume_path,
                cover_letter_text=cover_text,
            )

        log.info(
            "pipeline.start_fill",
            extra={"app_id": app.id, "job_id": job_id, "mode": mode},
        )

        # Mark running (in case reused app was in review state)
        self.store.update_application(
            app.id, status="shadow_running" if mode == "shadow" else "submitting"
        )

        try:
            fill_result = await self.form_filler.run(
                app_id=app.id,
                apply_url=job.apply_url or "",
                profile=profile,
                resume_path=resume_path,
                cover_letter=cover_text,
                job_description=job.description or "",
                company=job.company or "",
                submit=(mode == "live"),
            )
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "pipeline.fill_unhandled_error",
                extra={"app_id": app.id, "error": str(exc)},
            )
            updated = self.store.update_application(
                app.id,
                status="failed",
                human_notes=f"Unhandled error: {exc}",
            )
            return updated

        # Determine final status
        if mode == "shadow":
            if fill_result.status == "shadow_complete":
                final_status = "shadow_review"
            elif fill_result.status == "skipped":
                final_status = "skipped"
            else:
                final_status = "failed"
        else:  # live
            if fill_result.status == "complete" and fill_result.submitted:
                final_status = "submitted"
            elif fill_result.status == "skipped":
                final_status = "skipped"
            else:
                final_status = "failed"

        updated = self.store.update_application(
            app.id,
            status=final_status,
            shadow_screenshots=fill_result.screenshots,
            fill_log=fill_result.fill_log,
            custom_qa=fill_result.custom_qa,
            submitted_at=(_now() if final_status == "submitted" else None),
            human_notes=(fill_result.error if fill_result.error else None),
        )

        # Update job row's status so Discover view reflects progress
        job_status_map = {
            "shadow_review": "reviewing",
            "submitted": "applied",
            "skipped": "skipped",
            "failed": "queued",  # let user retry
        }
        try:
            self.store.update_job_status(job_id, job_status_map.get(final_status, "reviewing"))
        except Exception as exc:  # noqa: BLE001
            log.warning("pipeline.job_status_update_failed", extra={"error": str(exc)})

        duration_ms = int((time.monotonic() - start) * 1000)
        log.info(
            "pipeline.complete",
            extra={
                "app_id": app.id,
                "status": final_status,
                "mode": mode,
                "duration_ms": duration_ms,
            },
        )
        return updated

    # --- helpers ---------------------------------------------------------

    def _require_job(self, job_id: str):
        job = self.store.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        if not job.apply_url:
            raise ValueError(f"Job {job_id} has no apply URL")
        return job

    def _require_profile(self):
        profile = self.store.get_full_profile()
        if not profile.is_complete_enough:
            raise ValueError(
                "Profile is missing required fields (name, email, phone). "
                "Complete the profile before applying."
            )
        return profile


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
