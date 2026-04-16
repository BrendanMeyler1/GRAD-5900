"""Unit tests for db.store.Store — CRUD for every table."""

from __future__ import annotations

from db.store import JobFilters


def test_upsert_and_get_profile(store) -> None:
    store.upsert_profile(
        {
            "first_name": "Alice",
            "last_name": "Nguyen",
            "email": "alice@example.com",
            "phone": "(555) 000-1111",
            "city": "Seattle",
            "state": "WA",
        }
    )
    p = store.get_profile()
    assert p is not None
    assert p.first_name == "Alice"
    assert p.email == "alice@example.com"  # decrypted transparently
    assert p.phone == "(555) 000-1111"


def test_profile_partial_update_preserves_unsent_fields(store) -> None:
    store.upsert_profile({"first_name": "Alice", "last_name": "Nguyen"})
    store.upsert_profile({"email": "alice@example.com"})
    p = store.get_profile()
    assert p.first_name == "Alice"  # still there
    assert p.email == "alice@example.com"


def test_add_and_list_education(store) -> None:
    store.add_education(
        {"institution": "MIT", "degree": "BS", "field": "CS", "graduation_year": 2023}
    )
    store.add_education(
        {"institution": "Stanford", "degree": "MS", "field": "CS", "graduation_year": 2025}
    )
    edus = store.get_education()
    assert len(edus) == 2
    # Ordered by graduation_year DESC
    assert edus[0].institution == "Stanford"


def test_add_and_list_experience(store) -> None:
    store.add_experience(
        {
            "company": "Acme",
            "title": "Engineer",
            "start_date": "2023-01-01",
            "is_current": True,
        }
    )
    xs = store.get_experience()
    assert len(xs) == 1
    assert xs[0].is_current is True


def test_upsert_skills_dedups_by_name(store) -> None:
    store.upsert_skills(
        [
            {"name": "Python", "proficiency": "intermediate"},
            {"name": "React"},
        ]
    )
    store.upsert_skills([{"name": "Python", "proficiency": "expert"}])
    skills = store.get_skills()
    names = [s.name for s in skills]
    assert names.count("Python") == 1
    py = next(s for s in skills if s.name == "Python")
    assert py.proficiency == "expert"


def test_qa_insert_and_filter_by_category(store) -> None:
    store.add_qa("A?", "Alpha", category="preference")
    store.add_qa("B?", "Beta", category="background")
    prefs = store.get_qa(category="preference")
    assert len(prefs) == 1
    assert prefs[0].answer == "Alpha"


def test_upsert_job_and_filter_by_fit_score(store) -> None:
    store.upsert_job(
        {
            "id": "j1",
            "source": "greenhouse",
            "title": "Backend Eng",
            "company": "Stripe",
            "apply_url": "https://boards.greenhouse.io/stripe/1",
            "fit_score": 82.0,
        }
    )
    store.upsert_job(
        {
            "id": "j2",
            "source": "lever",
            "title": "Full-stack",
            "company": "Ramp",
            "apply_url": "https://jobs.lever.co/ramp/1",
            "fit_score": 55.0,
        }
    )
    hot = store.get_jobs(JobFilters(min_fit_score=70))
    assert len(hot) == 1
    assert hot[0].company == "Stripe"


def test_update_job_status_and_fit(store) -> None:
    store.upsert_job(
        {
            "id": "j1",
            "source": "greenhouse",
            "title": "A",
            "company": "B",
            "apply_url": "https://x/1",
        }
    )
    store.update_job_status("j1", "queued")
    store.update_job_fit("j1", 90.0, "Great fit", ["python"], ["k8s"], "high")
    job = store.get_job("j1")
    assert job.status == "queued"
    assert job.fit_score == 90.0
    assert "python" in job.fit_strengths
    assert job.interview_likelihood == "high"


def test_create_and_update_application(store) -> None:
    store.upsert_job(
        {"id": "j1", "source": "x", "title": "T", "company": "C", "apply_url": "https://x/1"}
    )
    app = store.create_application(
        job_id="j1",
        status="pending",
        resume_tailored_text="markdown...",
        shadow_screenshots=["/tmp/a.png"],
    )
    assert app.status == "pending"
    assert app.shadow_screenshots == ["/tmp/a.png"]

    updated = store.update_application(
        app.id, status="shadow_review", human_notes="looks ok"
    )
    assert updated.status == "shadow_review"
    assert updated.human_notes == "looks ok"
    assert updated.job is not None  # hydrated on read
    assert updated.job.company == "C"


def test_chat_message_count_and_history(store) -> None:
    store.add_message("user", "hi")
    store.add_message("assistant", "hello")
    store.add_message("user", "find jobs")
    assert store.get_message_count() == 3
    msgs = store.get_messages(limit=2)
    # Most recent 2, chronologically
    assert [m.content for m in msgs] == ["hello", "find jobs"]


def test_conversation_summary_upsert(store) -> None:
    assert store.get_summary() is None
    store.update_summary("User wants Python jobs.", message_count=10)
    assert store.get_summary() == "User wants Python jobs."
    assert store.get_summary_message_count() == 10
    store.update_summary("Updated summary.", message_count=20)
    assert store.get_summary() == "Updated summary."
    assert store.get_summary_message_count() == 20


def test_app_memory_upsert(store) -> None:
    store.upsert_app_memory("Stripe", ats_type="greenhouse", what_worked="upload resume button")
    m = store.get_app_memory("Stripe")
    assert m is not None
    assert m.what_worked == "upload resume button"
    store.upsert_app_memory("Stripe", what_failed="custom Q timeout")
    m2 = store.get_app_memory("Stripe")
    assert m2.what_worked == "upload resume button"  # still there
    assert m2.what_failed == "custom Q timeout"


def test_email_event_persist_and_filter(store) -> None:
    store.add_email_event(
        {
            "company": "Stripe",
            "subject": "Interview invite",
            "category": "interview_request",
            "summary": "Schedule 30min call",
            "action_needed": True,
            "urgency": "high",
        }
    )
    store.add_email_event(
        {
            "company": "Acme",
            "subject": "Auto-reply",
            "category": "auto_reply",
            "action_needed": False,
        }
    )
    all_events = store.get_email_events()
    assert len(all_events) == 2
    urgent = store.get_email_events(action_needed=True)
    assert len(urgent) == 1
    assert urgent[0].company == "Stripe"
