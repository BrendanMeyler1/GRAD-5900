"""Tests for feedback.company_memory_store."""

from pathlib import Path
from uuid import uuid4

from feedback.company_memory_store import CompanyMemoryStore


def _store() -> CompanyMemoryStore:
    tmp_dir = Path(".tmp")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_dir / f"company_memory_{uuid4().hex}.db"
    return CompanyMemoryStore(db_path=str(db_path))


def test_cache_answer_and_get_cached_answer():
    store = _store()

    store.cache_answer(
        company_name="Acme Test Corp",
        ats_type="greenhouse",
        question_key="why_work_here",
        question_text="Why do you want to work here?",
        answer_text="I align with your distributed systems mission.",
    )
    cached = store.get_cached_answer(
        company_name="Acme Test Corp",
        question_key="why_work_here",
    )

    assert cached is not None
    assert cached["question_key"] == "why_work_here"
    assert "distributed systems" in cached["answer_text"]
    assert cached["used_count"] >= 2


def test_cache_answer_updates_existing_record():
    store = _store()

    first_id = store.cache_answer(
        company_name="Acme Test Corp",
        question_key="salary_expectation",
        question_text="What are your salary expectations?",
        answer_text="180000",
    )
    second_id = store.cache_answer(
        company_name="Acme Test Corp",
        question_key="salary_expectation",
        question_text="What are your salary expectations?",
        answer_text="185000",
    )

    assert first_id == second_id
    cached = store.get_cached_answer(
        company_name="Acme Test Corp",
        question_key="salary_expectation",
    )
    assert cached["answer_text"] == "185000"


def test_replay_refs_roundtrip():
    store = _store()
    store.add_replay_ref(company_name="Acme Test Corp", trace_id="trace-1")
    store.add_replay_ref(company_name="Acme Test Corp", trace_id="trace-2")

    refs = store.get_replay_refs(company_name="Acme Test Corp")
    assert refs == ["trace-1", "trace-2"]
