"""
tests/conftest.py — Shared pytest fixtures.

Every test file can depend on these fixtures. The design principle:
no fixture makes a real network call or opens a real browser. We
mock the LLM and Stagehand/Playwright layers so the suite runs in
<5 seconds on a laptop.

Key fixtures:
    test_db            → fresh, empty SQLite DB file per test
    store              → Store pointed at test_db
    mock_llm           → LLMClient with deterministic canned responses
    sample_profile     → fully-populated FullProfile
    sample_job         → realistic JobListing
    seeded_store       → store pre-filled with sample data
    env_setup          → autouse: guarantees env vars exist before imports
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


# ─── autouse env setup (before any other import) ─────────────────────────────

@pytest.fixture(autouse=True, scope="session")
def env_setup() -> None:
    """Guarantee required env vars exist before config.settings loads."""
    os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-dummy")
    os.environ.setdefault("JSEARCH_API_KEY", "test-jsearch-key")
    os.environ.setdefault("DEV_MODE", "true")
    os.environ.setdefault("LOG_LEVEL", "WARNING")


# ─── DB fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def test_db(tmp_path: Path) -> Path:
    """Fresh, empty SQLite DB file scoped to each test."""
    from db.schema import init_db

    db_path = tmp_path / "test.db"
    init_db(db_path)
    return db_path


@pytest.fixture
def store(test_db: Path):
    """Store pointed at a clean test DB."""
    from cryptography.fernet import Fernet

    from db.encryption import FieldEncryptor
    from db.store import Store

    encryptor = FieldEncryptor(key=Fernet.generate_key())
    s = Store(test_db, encryptor)
    yield s
    s.close()


# ─── Sample data ──────────────────────────────────────────────────────────────

@pytest.fixture
def sample_profile():
    """A complete FullProfile for tests that need one."""
    from db.store import Education, FullProfile, Skill, UserProfile, WorkExperience

    return FullProfile(
        profile=UserProfile(
            first_name="Jane",
            last_name="Doe",
            email="jane@example.com",
            phone="(555) 123-4567",
            city="Boston",
            state="MA",
            target_salary_min=90_000,
            target_salary_max=120_000,
            remote_preference="hybrid",
            resume_raw_text="Jane Doe\nSoftware Engineer\nPython, FastAPI, PostgreSQL",
        ),
        education=[
            Education(
                institution="MIT",
                degree="BS",
                field="Computer Science",
                graduation_year=2023,
                gpa=3.9,
            )
        ],
        experience=[
            WorkExperience(
                company="Acme Corp",
                title="Software Engineer",
                start_date="2023-06-01",
                is_current=True,
                description="Built REST APIs.",
            )
        ],
        skills=[
            Skill(name="Python", category="technical", proficiency="expert"),
            Skill(name="FastAPI", category="technical", proficiency="expert"),
        ],
    )


@pytest.fixture
def sample_job():
    """A realistic JobListing for tests."""
    from db.store import JobListing

    return JobListing(
        id=str(uuid.uuid4()),
        source="greenhouse",
        ats_type="greenhouse",
        title="Backend Engineer",
        company="Stripe",
        location="New York, NY",
        remote_ok=True,
        description="Build payment APIs. Python + PostgreSQL.",
        apply_url="https://boards.greenhouse.io/stripe/jobs/123",
    )


@pytest.fixture
def seeded_store(store, sample_profile, sample_job):
    """Store pre-loaded with profile + one job + education + experience + skills."""
    store.upsert_profile(sample_profile.profile.model_dump(exclude={"id"}))
    for edu in sample_profile.education:
        store.add_education(edu.model_dump(exclude={"id", "created_at"}))
    for exp in sample_profile.experience:
        store.add_experience(exp.model_dump(exclude={"id", "created_at"}))
    store.upsert_skills([s.model_dump(exclude={"id"}) for s in sample_profile.skills])
    store.upsert_job(sample_job.model_dump())
    return store


# ─── LLM mock ─────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_llm():
    """
    LLMClient with canned responses.

    Returns a plain text JSON score by default. Override `.chat` per test to
    simulate different LLM behaviors.
    """
    m = MagicMock()
    m.chat = AsyncMock(
        return_value=(
            '{"score": 78, "summary": "Strong match.", '
            '"strengths": ["Python", "FastAPI"], "gaps": ["Kubernetes"], '
            '"interview_likelihood": "medium-high"}'
        )
    )
    m.classify = AsyncMock(
        return_value='{"category": "interview_request", "summary": "Interview scheduled.", '
        '"action_needed": true, "urgency": "high", "key_details": "2pm Thursday"}'
    )

    async def _fake_stream(*a: Any, **kw: Any) -> AsyncIterator[str]:
        for chunk in ("This ", "is ", "a ", "test ", "response."):
            yield chunk

    m.stream = _fake_stream
    m.with_image = AsyncMock(return_value="Looks like a form. Fill all required fields.")
    return m


# ─── Stagehand / browser mock ─────────────────────────────────────────────────

@pytest.fixture
def mock_stagehand(tmp_path: Path):
    """Mock UniversalFiller that returns a successful shadow fill."""
    from filler.universal import FillResult

    screenshot = tmp_path / "screenshot1.png"
    screenshot.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    result = FillResult(
        status="shadow_complete",
        screenshots=[str(screenshot)],
        fill_log=[
            {"action": "fill", "selector": "input[name=first_name]", "value": "Jane"},
            {"action": "upload", "selector": "input[type=file]", "value": "resume.pdf"},
        ],
        custom_qa={"Why do you want this job?": "Great company, aligned skills."},
        error=None,
    )
    m = MagicMock()
    m.fill = AsyncMock(return_value=result)
    m.close = AsyncMock()
    return m
