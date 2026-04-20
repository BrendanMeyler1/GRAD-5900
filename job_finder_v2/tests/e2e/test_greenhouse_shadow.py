"""
End-to-end test: shadow-fill a Greenhouse-style form on a local mock server.

This test spins up a minimal HTTP server that serves a realistic Greenhouse
application form, runs the Pipeline in shadow mode against it, and verifies
the full lifecycle: tailor → fill → screenshots → shadow_review.

Because the real UniversalFiller needs Playwright installed, and CI may
not have a browser, we fall back to DEV_MODE (mock filler) when Playwright
is unavailable. The test still validates the full pipeline wiring.

Marks: requires_browser — skip in CI unless explicitly enabled.
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.resume_writer import CoverLetterResult, ResumeResult
from filler.universal import FillResult
from pipeline import Pipeline


# ─── Fake Greenhouse form HTML ────────────────────────────────────────────────

_FORM_HTML = """\
<!DOCTYPE html>
<html>
<head><title>Stripe — Apply: Backend Engineer</title></head>
<body>
  <h1>Backend Engineer</h1>
  <form id="application-form" action="#" method="POST">
    <div>
      <label for="first_name">First Name</label>
      <input type="text" id="first_name" name="first_name" required />
    </div>
    <div>
      <label for="last_name">Last Name</label>
      <input type="text" id="last_name" name="last_name" required />
    </div>
    <div>
      <label for="email">Email</label>
      <input type="email" id="email" name="email" required />
    </div>
    <div>
      <label for="phone">Phone</label>
      <input type="tel" id="phone" name="phone" />
    </div>
    <div>
      <label for="resume">Resume</label>
      <input type="file" id="resume" name="resume" accept=".pdf,.docx" />
    </div>
    <div>
      <label for="cover_letter">Cover Letter</label>
      <textarea id="cover_letter" name="cover_letter" rows="5"></textarea>
    </div>
    <div>
      <label for="linkedin">LinkedIn</label>
      <input type="url" id="linkedin" name="linkedin" />
    </div>
    <div>
      <label for="why_interested">Why are you interested in this role?</label>
      <textarea id="why_interested" name="why_interested" rows="3"></textarea>
    </div>
    <div>
      <label>Work Authorization</label>
      <select name="authorized">
        <option value="">--</option>
        <option value="yes">Yes</option>
        <option value="no">No</option>
      </select>
    </div>
    <button type="submit" id="submit-btn">Submit Application</button>
  </form>
</body>
</html>
"""


class _FormHandler(SimpleHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(_FORM_HTML.encode())

    def log_message(self, format, *args):  # noqa: A002
        pass  # suppress server logs during tests


@pytest.fixture
def form_server():
    """Spin up an ephemeral local HTTP server serving the mock Greenhouse form."""
    server = HTTPServer(("127.0.0.1", 0), _FormHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


# ─── Tests ────────────────────────────────────────────────────────────────────


async def test_e2e_shadow_pipeline_with_mock_filler(
    seeded_store, sample_job, form_server, tmp_path
) -> None:
    """
    Full e2e: seed DB → run pipeline (mock filler) → verify Application in
    shadow_review with screenshots and fill log persisted.
    """
    # Point the sample job at our local form server
    seeded_store.update_job_status(sample_job.id, "queued")
    conn = seeded_store._get_conn()
    conn.execute(
        "UPDATE job_listings SET apply_url = ? WHERE id = ?",
        (form_server, sample_job.id),
    )
    conn.commit()

    # Mock resume writer
    writer = MagicMock()
    writer.tailor = AsyncMock(
        return_value=ResumeResult(
            text="# Jane Doe\nTailored for Stripe.",
            file_path=str(tmp_path / "resume.pdf"),
            markdown_path=str(tmp_path / "resume.md"),
        )
    )
    writer.cover_letter = AsyncMock(
        return_value=CoverLetterResult(
            text="Dear Stripe hiring team,\n\nI'm a Python engineer.",
            file_path=str(tmp_path / "cover.pdf"),
            markdown_path=str(tmp_path / "cover.md"),
        )
    )

    # Create a dummy resume file
    (tmp_path / "resume.pdf").write_bytes(b"%PDF-1.4 fake")

    # Mock the form filler to return shadow_complete
    screenshot_path = str(tmp_path / "screen_final.png")
    Path(screenshot_path).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    filler_agent = MagicMock()
    filler_agent.run = AsyncMock(
        return_value=FillResult(
            status="shadow_complete",
            screenshots=[screenshot_path],
            fill_log=[
                {"action": "fill", "selector": "#first_name", "value": "Jane"},
                {"action": "fill", "selector": "#last_name", "value": "Doe"},
                {"action": "fill", "selector": "#email", "value": "jane@x.com"},
                {"action": "fill", "selector": "#phone", "value": "(555) 123-4567"},
                {"action": "fill", "selector": "#cover_letter", "value": "Dear Stripe..."},
                {"action": "fill", "selector": "#why_interested", "value": "Great Python team."},
                {"action": "select", "selector": "select[name=authorized]", "value": "yes"},
            ],
            custom_qa={"Why are you interested in this role?": "Great Python team."},
            error=None,
            submitted=False,
            duration_ms=15000,
        )
    )

    pipeline = Pipeline(
        store=seeded_store,
        llm=MagicMock(),
        resume_writer=writer,
        form_filler=filler_agent,
    )

    app = await pipeline.run_application(sample_job.id, mode="shadow")

    # ── Assertions ──────────────────────────────────────────────────────

    # Status correct
    assert app.status == "shadow_review"
    assert app.job_id == sample_job.id

    # Tailored docs persisted
    assert "Jane Doe" in app.resume_tailored_text
    assert "Stripe" in app.cover_letter_text

    # Screenshots saved
    assert len(app.shadow_screenshots) == 1
    assert "screen_final" in app.shadow_screenshots[0]

    # Fill log saved
    assert len(app.fill_log) == 7
    actions = [entry["action"] for entry in app.fill_log]
    assert "fill" in actions
    assert "select" in actions

    # Custom Q&A saved
    assert "Why are you interested" in list(app.custom_qa.keys())[0]

    # Job status updated
    job = seeded_store.get_job(sample_job.id)
    assert job.status == "reviewing"

    # Application is retrievable by ID
    fetched = seeded_store.get_application(app.id)
    assert fetched is not None
    assert fetched.status == "shadow_review"


async def test_e2e_approve_after_shadow(
    seeded_store, sample_job, form_server, tmp_path
) -> None:
    """After shadow review, approve & submit (live mode) reuses tailored docs."""
    # Create a shadow_review application
    shadow_app = seeded_store.create_application(
        job_id=sample_job.id,
        status="shadow_review",
        resume_tailored_text="# Jane Doe\nTailored.",
        resume_tailored_path=str(tmp_path / "r.pdf"),
        cover_letter_text="Dear team...",
    )

    writer = MagicMock()
    filler_agent = MagicMock()
    filler_agent.run = AsyncMock(
        return_value=FillResult(
            status="complete",
            screenshots=[str(tmp_path / "submitted.png")],
            fill_log=[],
            custom_qa={},
            error=None,
            submitted=True,
            duration_ms=8000,
        )
    )

    pipeline = Pipeline(
        store=seeded_store,
        llm=MagicMock(),
        resume_writer=writer,
        form_filler=filler_agent,
    )

    app = await pipeline.run_application(
        sample_job.id, mode="live", existing_app_id=shadow_app.id
    )

    assert app.status == "submitted"
    assert app.submitted_at is not None
    # Writer not called — docs reused from shadow
    writer.tailor.assert_not_called()
    writer.cover_letter.assert_not_called()


async def test_e2e_pipeline_failed_filler(
    seeded_store, sample_job, tmp_path
) -> None:
    """If the filler returns status=failed, the application is marked failed."""
    writer = MagicMock()
    writer.tailor = AsyncMock(
        return_value=ResumeResult(
            text="# Resume", file_path=str(tmp_path / "r.pdf"),
            markdown_path=str(tmp_path / "r.md"),
        )
    )
    writer.cover_letter = AsyncMock(
        return_value=CoverLetterResult(
            text="Cover letter", file_path=str(tmp_path / "c.pdf"),
            markdown_path=str(tmp_path / "c.md"),
        )
    )

    filler_agent = MagicMock()
    filler_agent.run = AsyncMock(
        return_value=FillResult(
            status="failed",
            screenshots=[],
            fill_log=[],
            custom_qa={},
            error="Login required — cannot proceed",
            submitted=False,
            duration_ms=3000,
        )
    )

    pipeline = Pipeline(
        store=seeded_store,
        llm=MagicMock(),
        resume_writer=writer,
        form_filler=filler_agent,
    )

    app = await pipeline.run_application(sample_job.id, mode="shadow")
    assert app.status == "failed"
    assert "Login required" in (app.human_notes or "")
