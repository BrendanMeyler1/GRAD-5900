"""Unit tests for agents.profile_builder.ProfileBuilder."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.profile_builder import ExtractedProfile, ProfileBuilder, _parse_json


_RESUME_TEXT = """
Jane Doe
jane.doe@example.com | (555) 123-4567 | Boston, MA
linkedin.com/in/janedoe | github.com/janedoe

EDUCATION
MIT — BS in Computer Science, 2023, GPA 3.9

EXPERIENCE
Acme Corp — Software Engineer (2023-06 – present)
Built REST APIs in Python + PostgreSQL.

SKILLS
Python, FastAPI, PostgreSQL, React
"""


def _mock_extract_response() -> str:
    return json.dumps(
        {
            "first_name": "Jane",
            "last_name": "Doe",
            "email": "jane.doe@example.com",
            "phone": "(555) 123-4567",
            "city": "Boston",
            "state": "MA",
            "linkedin_url": "https://linkedin.com/in/janedoe",
            "github_url": "https://github.com/janedoe",
            "portfolio_url": None,
            "education": [
                {
                    "institution": "MIT",
                    "degree": "BS",
                    "field": "Computer Science",
                    "graduation_year": 2023,
                    "gpa": 3.9,
                }
            ],
            "experience": [
                {
                    "company": "Acme Corp",
                    "title": "Software Engineer",
                    "start_date": "2023-06-01",
                    "end_date": None,
                    "is_current": True,
                    "description": "Built REST APIs.",
                }
            ],
            "skills": [
                {"name": "Python", "category": "technical"},
                {"name": "FastAPI", "category": "technical"},
                {"name": "PostgreSQL", "category": "technical"},
            ],
        }
    )


async def test_extract_from_resume_populates_all_fields(store) -> None:
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=_mock_extract_response())
    builder = ProfileBuilder(store, llm=llm)

    extracted = await builder.extract_from_resume(_RESUME_TEXT)

    assert extracted.first_name == "Jane"
    assert extracted.last_name == "Doe"
    assert extracted.email == "jane.doe@example.com"
    assert len(extracted.education or []) == 1
    assert len(extracted.experience or []) == 1
    assert len(extracted.skills or []) == 3


async def test_extract_persists_profile_to_db(store) -> None:
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=_mock_extract_response())
    builder = ProfileBuilder(store, llm=llm)

    await builder.extract_from_resume(_RESUME_TEXT)

    profile = store.get_profile()
    assert profile is not None
    assert profile.first_name == "Jane"
    assert profile.email == "jane.doe@example.com"
    assert profile.resume_raw_text == _RESUME_TEXT

    # Structured tables populated
    assert len(store.get_education()) == 1
    assert len(store.get_experience()) == 1
    assert len(store.get_skills()) >= 3


async def test_extract_empty_resume_returns_empty(store) -> None:
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=_mock_extract_response())
    builder = ProfileBuilder(store, llm=llm)

    result = await builder.extract_from_resume("")
    assert result == ExtractedProfile()
    # LLM should not be called for empty input
    llm.chat.assert_not_called()


async def test_extract_tolerates_fenced_json(store) -> None:
    llm = MagicMock()
    llm.chat = AsyncMock(
        return_value=f"```json\n{_mock_extract_response()}\n```"
    )
    builder = ProfileBuilder(store, llm=llm)

    result = await builder.extract_from_resume(_RESUME_TEXT)
    assert result.first_name == "Jane"


async def test_extract_handles_malformed_json(store) -> None:
    llm = MagicMock()
    llm.chat = AsyncMock(return_value="not json at all")
    builder = ProfileBuilder(store, llm=llm)

    result = await builder.extract_from_resume(_RESUME_TEXT)
    assert result == ExtractedProfile()


async def test_ask_next_question_returns_none_when_complete(store, sample_profile) -> None:
    """A fully-populated profile should yield no further questions."""
    llm = MagicMock()
    llm.chat = AsyncMock(return_value="Should not be called.")
    builder = ProfileBuilder(store, llm=llm)

    # sample_profile has name/email/phone/location/salary/remote/experience/education/skills
    question = await builder.ask_next_question(sample_profile)
    assert question is None


async def test_ask_next_question_returns_question_when_gaps(store) -> None:
    from db.store import FullProfile, UserProfile

    empty_profile = FullProfile(
        profile=UserProfile(first_name="Jane"),
        education=[],
        experience=[],
        skills=[],
        qa_notes=[],
    )
    llm = MagicMock()
    llm.chat = AsyncMock(return_value="What's your email address?")
    builder = ProfileBuilder(store, llm=llm)

    q = await builder.ask_next_question(empty_profile)
    assert q == "What's your email address?"
    llm.chat.assert_called_once()


async def test_answer_into_profile_applies_structured_update(store, sample_profile) -> None:
    llm = MagicMock()
    llm.chat = AsyncMock(
        return_value='{"profile_updates": {"target_salary_min": 100000, "target_salary_max": 140000}, "category": "preference"}'
    )
    builder = ProfileBuilder(store, llm=llm)
    # Seed the profile so store.upsert_profile has an existing row
    store.upsert_profile(sample_profile.profile.model_dump(exclude={"id"}))

    result = await builder.answer_into_profile(
        question="What salary are you targeting?",
        answer="$100k to $140k",
        profile=sample_profile,
    )
    assert result["category"] == "preference"
    assert result["updates"]["target_salary_min"] == 100000

    # Q&A recorded
    qa = store.get_qa()
    assert any("salary" in n.question.lower() for n in qa)

    # Profile updated
    updated = store.get_profile()
    assert updated.target_salary_min == 100000
    assert updated.target_salary_max == 140000


async def test_answer_into_profile_handles_unstructured_answer(store, sample_profile) -> None:
    llm = MagicMock()
    llm.chat = AsyncMock(
        return_value='{"profile_updates": {}, "category": "other"}'
    )
    builder = ProfileBuilder(store, llm=llm)
    store.upsert_profile(sample_profile.profile.model_dump(exclude={"id"}))

    result = await builder.answer_into_profile(
        question="Tell me about yourself.",
        answer="I love building things.",
        profile=sample_profile,
    )
    assert result["category"] == "other"
    assert result["updates"] == {}


def test_parse_json_handles_fenced_output() -> None:
    fenced = '```json\n{"a": 1}\n```'
    assert _parse_json(fenced) == {"a": 1}


def test_parse_json_extracts_embedded_object() -> None:
    noisy = 'Here you go: {"key": "value"} ...trailing text'
    assert _parse_json(noisy) == {"key": "value"}


def test_parse_json_returns_none_on_garbage() -> None:
    assert _parse_json("nothing json here") is None
