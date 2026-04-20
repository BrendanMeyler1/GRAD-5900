"""Unit tests for agents.resume_writer.ResumeWriter — prompt construction + output handling."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.resume_writer import ResumeWriter, _strip_code_fences


@pytest.fixture(autouse=True)
def _isolate_generated_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect settings.generated_dir to a tmp dir for each test."""
    from config import settings

    generated = tmp_path / "generated"
    generated.mkdir()
    monkeypatch.setattr(settings, "__dict__", dict(settings.__dict__), raising=False)
    # pydantic BaseSettings uses attribute access; patch the property getter
    monkeypatch.setattr(
        type(settings), "generated_dir", property(lambda self: generated)
    )
    return generated


async def test_tailor_writes_markdown_file(sample_profile, _isolate_generated_dir) -> None:
    llm = MagicMock()
    llm.chat = AsyncMock(return_value="# Jane Doe\n\n## Summary\nPython engineer.")
    writer = ResumeWriter(llm=llm)

    with patch("agents.resume_writer.export_resume_pdf", return_value="/tmp/resume.pdf"):
        result = await writer.tailor(
            app_id="app-1",
            job_title="Backend Engineer",
            job_description="Build APIs.",
            company="Stripe",
            profile=sample_profile,
        )

    assert result.text.startswith("# Jane Doe")
    md_path = _isolate_generated_dir / "app-1" / "resume.md"
    assert md_path.exists()
    assert "Jane Doe" in md_path.read_text()


async def test_tailor_strips_code_fences(sample_profile, _isolate_generated_dir) -> None:
    llm = MagicMock()
    llm.chat = AsyncMock(return_value="```markdown\n# Jane\n## Summary\nExpert.\n```")
    writer = ResumeWriter(llm=llm)

    with patch("agents.resume_writer.export_resume_pdf", return_value="/tmp/r.pdf"):
        result = await writer.tailor(
            app_id="app-2",
            job_title="T",
            job_description="D",
            company="C",
            profile=sample_profile,
        )

    assert "```" not in result.text
    assert result.text.startswith("# Jane")


async def test_tailor_prompt_includes_job_and_profile(sample_profile, _isolate_generated_dir) -> None:
    captured: dict = {}

    async def fake_chat(messages, system, max_tokens):
        captured["messages"] = messages
        captured["system"] = system
        return "# Output resume"

    llm = MagicMock()
    llm.chat = AsyncMock(side_effect=fake_chat)
    writer = ResumeWriter(llm=llm)

    with patch("agents.resume_writer.export_resume_pdf", return_value="/tmp/r.pdf"):
        await writer.tailor(
            app_id="app-3",
            job_title="Backend Engineer",
            job_description="Build APIs with Python and PostgreSQL.",
            company="Stripe",
            profile=sample_profile,
        )

    user_msg = captured["messages"][0]["content"]
    assert "Stripe" in user_msg
    assert "Backend Engineer" in user_msg
    assert "Python" in user_msg  # from job description
    assert "Jane" in user_msg  # from profile


async def test_cover_letter_references_resume(sample_profile, _isolate_generated_dir) -> None:
    from agents.resume_writer import ResumeResult

    captured: dict = {}

    async def fake_chat(messages, system, max_tokens):
        captured["messages"] = messages
        return "Dear team, I'd love to join..."

    llm = MagicMock()
    llm.chat = AsyncMock(side_effect=fake_chat)
    writer = ResumeWriter(llm=llm)

    tailored = ResumeResult(
        text="# Jane Doe\n## Summary\nSenior Python engineer.",
        file_path="/tmp/r.pdf",
        markdown_path="/tmp/r.md",
    )
    with patch("agents.resume_writer.export_cover_letter_pdf", return_value="/tmp/c.pdf"):
        result = await writer.cover_letter(
            app_id="app-4",
            job_title="Backend",
            company="Stripe",
            job_description="APIs",
            profile=sample_profile,
            tailored_resume=tailored,
        )

    assert "Dear team" in result.text
    user_msg = captured["messages"][0]["content"]
    assert "Senior Python engineer" in user_msg  # resume text included


def test_strip_code_fences_handles_no_fences() -> None:
    assert _strip_code_fences("# Header\ncontent") == "# Header\ncontent"


def test_strip_code_fences_removes_triple_backticks() -> None:
    input_text = "```markdown\n# Resume\nbody\n```"
    assert _strip_code_fences(input_text) == "# Resume\nbody"


def test_strip_code_fences_removes_bare_fences() -> None:
    assert _strip_code_fences("```\nx\n```") == "x"
