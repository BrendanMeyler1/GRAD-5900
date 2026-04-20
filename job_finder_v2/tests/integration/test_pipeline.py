"""
Integration test for Pipeline — the full tailor → fill → persist flow.

Uses real Store (SQLite) + mock LLM + mock UniversalFiller so we exercise
the entire pipeline without network or browser. Verifies:
    - Application created with correct status
    - Tailored resume + cover letter saved to DB
    - Screenshots and fill log persisted
    - Job status updated
    - Reuse path (existing_app_id) works
    - Errors handled gracefully (status=failed, never raises)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from filler.universal import FillResult
from pipeline import Pipeline


@pytest.fixture
def mock_resume_writer():
    """ResumeWriter that returns canned results."""
    from agents.resume_writer import CoverLetterResult, ResumeResult

    w = MagicMock()
    w.tailor = AsyncMock(
        return_value=ResumeResult(
            text="# Jane Doe\n\n## Summary\nPython engineer.",
            file_path="/tmp/resume.pdf",
            markdown_path="/tmp/resume.md",
        )
    )
    w.cover_letter = AsyncMock(
        return_value=CoverLetterResult(
            text="Dear Stripe team,\n\nExcited to apply.",
            file_path="/tmp/cover.pdf",
            markdown_path="/tmp/cover.md",
        )
    )
    return w


@pytest.fixture
def mock_filler_agent():
    """FormFillerAgent that returns a shadow_complete result."""
    agent = MagicMock()
    agent.run = AsyncMock(
        return_value=FillResult(
            status="shadow_complete",
            screenshots=["/tmp/screen1.png", "/tmp/screen2.png"],
            fill_log=[
                {"action": "fill", "selector": "input[name=email]", "value": "jane@x.com"},
                {"action": "upload", "selector": "input[type=file]", "value": "resume.pdf"},
            ],
            custom_qa={"Why this role?": "Great alignment with my Python skills."},
            error=None,
            submitted=False,
            duration_ms=12000,
        )
    )
    return agent


@pytest.fixture
def pipeline(seeded_store, mock_resume_writer, mock_filler_agent):
    """Pipeline wired to seeded store, mock writer, mock filler."""
    return Pipeline(
        store=seeded_store,
        llm=MagicMock(),
        resume_writer=mock_resume_writer,
        form_filler=mock_filler_agent,
    )


async def test_shadow_pipeline_creates_review_application(
    pipeline, seeded_store, sample_job
) -> None:
    app = await pipeline.run_application(sample_job.id, mode="shadow")

    assert app.status == "shadow_review"
    assert app.job_id == sample_job.id
    assert "Jane Doe" in app.resume_tailored_text
    assert "Stripe" in app.cover_letter_text
    assert len(app.shadow_screenshots) == 2
    assert len(app.fill_log) == 2
    assert "Why this role?" in app.custom_qa


async def test_shadow_pipeline_updates_job_status(
    pipeline, seeded_store, sample_job
) -> None:
    await pipeline.run_application(sample_job.id, mode="shadow")
    job = seeded_store.get_job(sample_job.id)
    assert job.status == "reviewing"


async def test_live_pipeline_sets_submitted(
    seeded_store, mock_resume_writer, sample_job
) -> None:
    agent = MagicMock()
    agent.run = AsyncMock(
        return_value=FillResult(
            status="complete",
            screenshots=["/tmp/s.png"],
            fill_log=[],
            custom_qa={},
            error=None,
            submitted=True,
            duration_ms=8000,
        )
    )
    p = Pipeline(
        store=seeded_store,
        llm=MagicMock(),
        resume_writer=mock_resume_writer,
        form_filler=agent,
    )
    app = await p.run_application(sample_job.id, mode="live")
    assert app.status == "submitted"
    assert app.submitted_at is not None


async def test_tailor_only_creates_pending_app(
    pipeline, seeded_store, sample_job
) -> None:
    app = await pipeline.tailor_only(sample_job.id)

    assert app.status == "pending"
    assert "Jane Doe" in app.resume_tailored_text
    assert app.cover_letter_text  # non-empty


async def test_reuse_existing_app_skips_tailoring(
    seeded_store, mock_resume_writer, mock_filler_agent, sample_job
) -> None:
    # Create an existing app in shadow_review
    existing = seeded_store.create_application(
        job_id=sample_job.id,
        status="shadow_review",
        resume_tailored_text="# Existing Resume",
        resume_tailored_path="/tmp/existing.pdf",
        cover_letter_text="Existing CL",
    )
    p = Pipeline(
        store=seeded_store,
        llm=MagicMock(),
        resume_writer=mock_resume_writer,
        form_filler=mock_filler_agent,
    )
    app = await p.run_application(
        sample_job.id, mode="live", existing_app_id=existing.id
    )

    # Resume writer should NOT be called — we reused existing docs
    mock_resume_writer.tailor.assert_not_called()
    mock_resume_writer.cover_letter.assert_not_called()
    # Filler was called
    mock_filler_agent.run.assert_called_once()


async def test_filler_exception_results_in_failed_status(
    seeded_store, mock_resume_writer, sample_job
) -> None:
    agent = MagicMock()
    agent.run = AsyncMock(side_effect=RuntimeError("Browser crashed"))
    p = Pipeline(
        store=seeded_store,
        llm=MagicMock(),
        resume_writer=mock_resume_writer,
        form_filler=agent,
    )
    # Should NOT raise — pipeline catches and returns failed app
    app = await p.run_application(sample_job.id, mode="shadow")
    assert app.status == "failed"
    assert "Browser crashed" in (app.human_notes or "")


async def test_missing_job_raises(seeded_store, mock_resume_writer) -> None:
    p = Pipeline(store=seeded_store, llm=MagicMock(), resume_writer=mock_resume_writer)
    with pytest.raises(ValueError, match="not found"):
        await p.run_application("nonexistent-job-id", mode="shadow")


async def test_incomplete_profile_raises(store, sample_job) -> None:
    """If profile is missing required fields, pipeline refuses to run."""
    # store has no profile data at all
    store.upsert_job(sample_job.model_dump())
    p = Pipeline(store=store, llm=MagicMock())
    with pytest.raises(ValueError, match="missing required fields"):
        await p.run_application(sample_job.id, mode="shadow")
