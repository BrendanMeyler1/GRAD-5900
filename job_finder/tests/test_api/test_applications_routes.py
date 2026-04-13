"""Tests for Phase 2 Step 10 application API routes."""

from fastapi.testclient import TestClient

from api.main import app
from api.routes import applications as applications_routes
from api.routes import jobs as jobs_routes
from api.routes import persona as persona_routes


client = TestClient(app)


def _sample_listing(listing_id: str) -> dict:
    return {
        "listing_id": listing_id,
        "source": "greenhouse",
        "source_url": f"https://boards.greenhouse.io/acme/jobs/{listing_id}",
        "apply_url": f"https://boards.greenhouse.io/acme/jobs/{listing_id}#app",
        "company": {"name": "Acme Test Corp"},
        "role": {
            "title": "Backend Engineer",
            "location": "Remote US",
            "posted_date": "2026-04-08",
            "requirements": ["Python", "FastAPI"],
        },
        "ats_type": "greenhouse",
    }


def _reset_state() -> None:
    applications_routes.reset_applications_state()
    jobs_routes.reset_jobs_state()
    persona_routes._current_persona = None  # noqa: SLF001


def _seed_listing(listing: dict) -> None:
    jobs_routes._listings_by_id[listing["listing_id"]] = listing  # noqa: SLF001
    jobs_routes._listing_decisions[listing["listing_id"]] = "pending"  # noqa: SLF001


def test_application_lifecycle_with_edit_and_abort(sample_persona):
    _reset_state()
    persona_routes._current_persona = sample_persona  # noqa: SLF001
    listing = _sample_listing("listing-app-1")
    _seed_listing(listing)

    start = client.post(
        f"/api/apply/{listing['listing_id']}",
        json={"submission_mode": "shadow", "run_now": False},
    )
    assert start.status_code == 200
    app_id = start.json()["application_id"]
    assert start.json()["workflow_status"] == "QUEUED"

    status = client.get(f"/api/apply/{app_id}/status")
    assert status.status_code == 200
    assert status.json()["workflow_status"] == "QUEUED"

    approve = client.post(f"/api/apply/{app_id}/approve")
    assert approve.status_code == 200
    assert approve.json()["workflow_status"] == "APPROVED"

    edit = client.post(
        f"/api/apply/{app_id}/edit",
        json={
            "resume_text": "Updated resume text",
            "cover_letter_text": "Updated cover letter text",
            "question_responses": [{"field_id": "q1", "response_text": "Updated response"}],
        },
    )
    assert edit.status_code == 200
    assert edit.json()["workflow_status"] == "QUEUED"

    detail = client.get(f"/api/applications/{app_id}")
    assert detail.status_code == 200
    state = detail.json()["application"]["state"]
    assert state["tailored_resume_tokenized"] == "Updated resume text"
    assert state["cover_letter_tokenized"] == "Updated cover letter text"
    assert state["question_responses"][0]["field_id"] == "q1"

    abort = client.post(f"/api/apply/{app_id}/abort")
    assert abort.status_code == 200
    assert abort.json()["workflow_status"] == "ABORTED"

    listing_resp = client.get("/api/applications")
    assert listing_resp.status_code == 200
    assert listing_resp.json()["count"] == 1
    assert listing_resp.json()["applications"][0]["application_id"] == app_id


def test_application_resume_and_escalation_resolution(monkeypatch, sample_persona):
    _reset_state()
    persona_routes._current_persona = sample_persona  # noqa: SLF001
    listing = _sample_listing("listing-app-2")
    _seed_listing(listing)

    start = client.post(
        f"/api/apply/{listing['listing_id']}",
        json={"submission_mode": "shadow", "run_now": False},
    )
    app_id = start.json()["application_id"]

    applications_routes._applications[app_id]["state"]["human_escalations"] = [  # noqa: SLF001
        {
            "type": "form_interpreter",
            "field_id": "salary_expectation",
            "priority": "BLOCKING",
            "message": "Need user input",
        }
    ]

    resolve = client.post(
        f"/api/apply/{app_id}/escalation/salary_expectation/resolve",
        json={"value": "150000", "note": "Target base salary"},
    )
    assert resolve.status_code == 200
    assert resolve.json()["remaining_escalations"] == 0

    async def fake_execute_workflow(state):
        payload = state.model_dump()
        payload["status"] = "SUBMITTED"
        payload["status_history"] = payload.get("status_history", []) + [
            {"status": "SUBMITTED", "timestamp": "2026-04-10T00:00:00Z"}
        ]
        return payload

    monkeypatch.setattr("api.routes.applications._execute_workflow", fake_execute_workflow)
    resume = client.post(f"/api/apply/{app_id}/resume")
    assert resume.status_code == 200
    assert resume.json()["workflow_status"] == "SUBMITTED"

    detail = client.get(f"/api/applications/{app_id}")
    state = detail.json()["application"]["state"]
    assert state["status"] == "SUBMITTED"
    assert state["escalation_resolutions"][0]["field_id"] == "salary_expectation"


def test_resolve_escalation_clears_all_field_entries_and_stale_preflight(sample_persona):
    _reset_state()
    persona_routes._current_persona = sample_persona  # noqa: SLF001
    listing = _sample_listing("listing-escalation-clear-all")
    _seed_listing(listing)

    start = client.post(
        f"/api/apply/{listing['listing_id']}",
        json={"submission_mode": "live", "run_now": False},
    )
    app_id = start.json()["application_id"]

    applications_routes._applications[app_id]["state"]["fill_plan"] = {  # noqa: SLF001
        "fields": [{"field_id": "salary_expectation", "value": "{{SALARY_EXPECTATION}}"}]
    }
    applications_routes._applications[app_id]["state"]["human_escalations"] = [  # noqa: SLF001
        {
            "type": "form_interpreter",
            "field_id": "salary_expectation",
            "priority": "BLOCKING",
            "message": "HIGH sensitivity field requires manual approval",
        },
        {
            "type": "pii_injector",
            "field_id": "salary_expectation",
            "priority": "BLOCKING",
            "message": "HIGH sensitivity field requires manual approval.",
        },
        {
            "type": "submitter",
            "field_id": "__preflight__",
            "priority": "BLOCKING",
            "message": "Browser execution skipped until BLOCKING escalations are resolved.",
        },
    ]

    resolve = client.post(
        f"/api/apply/{app_id}/escalation/salary_expectation/resolve",
        json={"value": "180000", "note": "manual approval"},
    )
    assert resolve.status_code == 200
    assert resolve.json()["remaining_escalations"] == 0

    detail = client.get(f"/api/applications/{app_id}")
    assert detail.status_code == 200
    state = detail.json()["application"]["state"]
    assert state["human_escalations"] == []
    assert state["fill_plan"]["fields"][0]["value"] == "180000"


def test_start_application_wires_browser_automation_flags(sample_persona):
    _reset_state()
    persona_routes._current_persona = sample_persona  # noqa: SLF001
    listing = _sample_listing("listing-app-flags")
    _seed_listing(listing)

    start = client.post(
        f"/api/apply/{listing['listing_id']}",
        json={
            "submission_mode": "shadow",
            "run_now": False,
            "use_browser_automation": True,
            "headless": False,
            "apply_url": "https://boards.greenhouse.io/acme/jobs/custom-apply-url",
            "artifact_paths": {
                "resume": "data/processed/resume_final.pdf",
                "cover_letter": "data/processed/cover_final.pdf",
            },
        },
    )
    assert start.status_code == 200
    app_id = start.json()["application_id"]

    detail = client.get(f"/api/applications/{app_id}")
    assert detail.status_code == 200
    state = detail.json()["application"]["state"]
    assert state["use_browser_automation"] is True
    assert state["headless"] is False
    assert state["apply_url"].endswith("custom-apply-url")
    assert state["artifact_paths"]["resume"].endswith("resume_final.pdf")
    assert state["artifact_paths"]["cover_letter"].endswith("cover_final.pdf")


def test_start_application_validates_persona_and_listing(sample_persona):
    _reset_state()

    no_persona = client.post(
        "/api/apply/missing-listing",
        json={"submission_mode": "shadow", "run_now": False},
    )
    assert no_persona.status_code == 400
    assert "No persona available" in no_persona.json()["detail"]

    persona_routes._current_persona = sample_persona  # noqa: SLF001
    missing_listing = client.post(
        "/api/apply/missing-listing",
        json={"submission_mode": "shadow", "run_now": False},
    )
    assert missing_listing.status_code == 404
    assert "Listing not found" in missing_listing.json()["detail"]


def test_application_detail_loads_from_db_when_cache_cleared(sample_persona):
    _reset_state()
    persona_routes._current_persona = sample_persona  # noqa: SLF001
    listing = _sample_listing("listing-db-persist")
    _seed_listing(listing)

    start = client.post(
        f"/api/apply/{listing['listing_id']}",
        json={"submission_mode": "shadow", "run_now": False},
    )
    assert start.status_code == 200
    app_id = start.json()["application_id"]

    applications_routes._applications.clear()  # noqa: SLF001

    detail = client.get(f"/api/applications/{app_id}")
    assert detail.status_code == 200
    assert detail.json()["application"]["application_id"] == app_id


def test_approve_with_run_now_triggers_workflow(monkeypatch, sample_persona):
    _reset_state()
    persona_routes._current_persona = sample_persona  # noqa: SLF001
    listing = _sample_listing("listing-approve-run")
    _seed_listing(listing)

    start = client.post(
        f"/api/apply/{listing['listing_id']}",
        json={"submission_mode": "shadow", "run_now": False},
    )
    app_id = start.json()["application_id"]

    async def fake_execute_workflow(state):
        payload = state.model_dump()
        payload["status"] = "SUBMITTED"
        payload["status_history"] = payload.get("status_history", []) + [
            {"status": "SUBMITTED", "timestamp": "2026-04-10T00:00:00Z"}
        ]
        return payload

    monkeypatch.setattr("api.routes.applications._execute_workflow", fake_execute_workflow)
    approve = client.post(
        f"/api/apply/{app_id}/approve",
        json={
            "run_now": True,
            "submission_mode": "live",
            "use_browser_automation": True,
        },
    )
    assert approve.status_code == 200
    assert approve.json()["workflow_status"] == "SUBMITTED"


def test_approve_run_now_starts_fresh_attempt_state(monkeypatch, sample_persona):
    _reset_state()
    persona_routes._current_persona = sample_persona  # noqa: SLF001
    listing = _sample_listing("listing-approve-attempt-scope")
    _seed_listing(listing)

    start = client.post(
        f"/api/apply/{listing['listing_id']}",
        json={"submission_mode": "live", "run_now": False},
    )
    app_id = start.json()["application_id"]
    applications_routes._applications[app_id]["status"] = "FAILED"  # noqa: SLF001
    applications_routes._applications[app_id]["state"].update(  # noqa: SLF001
        {
            "status": "FAILED",
            "attempt_number": 1,
            "current_attempt": {
                "attempt_id": f"{app_id}:1",
                "attempt_number": 1,
                "trigger": "start_application",
                "started_at": "2026-04-10T00:00:00Z",
            },
            "human_escalations": [{"type": "submitter", "priority": "BLOCKING", "message": "old"}],
            "failure_record": {"error_type": "SubmissionFailed", "error_message": "old failure"},
            "fields_filled": [{"field_id": "first_name"}],
        }
    )

    captured = {}

    async def fake_execute_workflow(state):
        payload = state.model_dump()
        captured.update(payload)
        payload["status"] = "AWAITING_APPROVAL"
        payload["status_history"] = payload.get("status_history", []) + [
            {"status": "AWAITING_APPROVAL", "timestamp": "2026-04-10T00:00:00Z"}
        ]
        return payload

    monkeypatch.setattr("api.routes.applications._execute_workflow", fake_execute_workflow)
    approve = client.post(
        f"/api/apply/{app_id}/approve",
        json={"run_now": True, "submission_mode": "live"},
    )
    assert approve.status_code == 200
    assert captured["human_escalations"] == []
    assert captured["failure_record"] is None
    assert captured["fields_filled"] == []
    assert captured["current_attempt"]["attempt_number"] == 2
    assert captured["current_attempt"]["trigger"] == "approve_run_now"

    detail = client.get(f"/api/applications/{app_id}")
    assert detail.status_code == 200
    state = detail.json()["application"]["state"]
    assert state["attempt_history"][0]["attempt_number"] == 1
    assert state["attempt_history"][0]["final_status"] == "FAILED"


def test_approve_run_now_fast_paths_from_awaiting_approval(monkeypatch, sample_persona):
    _reset_state()
    persona_routes._current_persona = sample_persona  # noqa: SLF001
    listing = _sample_listing("listing-approve-fast-path")
    _seed_listing(listing)

    start = client.post(
        f"/api/apply/{listing['listing_id']}",
        json={"submission_mode": "shadow", "run_now": False},
    )
    app_id = start.json()["application_id"]
    applications_routes._applications[app_id]["status"] = "AWAITING_APPROVAL"  # noqa: SLF001
    applications_routes._applications[app_id]["state"]["status"] = "AWAITING_APPROVAL"  # noqa: SLF001

    async def fake_execute_workflow(_state):  # pragma: no cover
        raise AssertionError("approve fast path should not execute full workflow")

    monkeypatch.setattr("api.routes.applications._execute_workflow", fake_execute_workflow)

    approve = client.post(
        f"/api/apply/{app_id}/approve",
        json={"run_now": True, "submission_mode": "live", "use_browser_automation": False},
    )
    assert approve.status_code == 200
    assert approve.json()["workflow_status"] == "SUBMITTED"


def test_approve_run_now_from_failed_submission_uses_full_workflow(monkeypatch, sample_persona):
    _reset_state()
    persona_routes._current_persona = sample_persona  # noqa: SLF001
    listing = _sample_listing("listing-approve-failed-submission-fast-path")
    _seed_listing(listing)

    start = client.post(
        f"/api/apply/{listing['listing_id']}",
        json={"submission_mode": "live", "run_now": False},
    )
    app_id = start.json()["application_id"]
    applications_routes._applications[app_id]["status"] = "FAILED"  # noqa: SLF001
    applications_routes._applications[app_id]["state"]["status"] = "FAILED"  # noqa: SLF001
    applications_routes._applications[app_id]["state"]["failure_record"] = {  # noqa: SLF001
        "error_type": "SubmissionBlocked",
        "error_message": "Submission blocked until BLOCKING escalations are resolved.",
        "failure_step": "submission",
    }

    async def fake_execute_workflow(state):
        payload = state.model_dump()
        payload["status"] = "AWAITING_APPROVAL"
        payload["status_history"] = payload.get("status_history", []) + [
            {"status": "AWAITING_APPROVAL", "timestamp": "2026-04-10T03:10:00Z"}
        ]
        return payload

    async def fake_submission_node(_state):  # pragma: no cover
        raise AssertionError("approve should not fast-path failed submission retries")

    async def fake_record_outcome_node(_state):  # pragma: no cover
        raise AssertionError("approve should not fast-path failed submission retries")

    monkeypatch.setattr("api.routes.applications._execute_workflow", fake_execute_workflow)
    monkeypatch.setattr("api.routes.applications.submission_node", fake_submission_node)
    monkeypatch.setattr("api.routes.applications.record_outcome_node", fake_record_outcome_node)

    approve = client.post(
        f"/api/apply/{app_id}/approve",
        json={"run_now": True, "submission_mode": "live", "use_browser_automation": False},
    )
    assert approve.status_code == 200
    assert approve.json()["workflow_status"] == "AWAITING_APPROVAL"


def test_resume_starts_fresh_attempt_state(monkeypatch, sample_persona):
    _reset_state()
    persona_routes._current_persona = sample_persona  # noqa: SLF001
    listing = _sample_listing("listing-resume-attempt-scope")
    _seed_listing(listing)

    start = client.post(
        f"/api/apply/{listing['listing_id']}",
        json={"submission_mode": "live", "run_now": False},
    )
    app_id = start.json()["application_id"]
    applications_routes._applications[app_id]["status"] = "FAILED"  # noqa: SLF001
    applications_routes._applications[app_id]["state"].update(  # noqa: SLF001
        {
            "status": "FAILED",
            "attempt_number": 1,
            "current_attempt": {
                "attempt_id": f"{app_id}:1",
                "attempt_number": 1,
                "trigger": "approve_run_now",
                "started_at": "2026-04-10T01:00:00Z",
            },
            "human_escalations": [{"type": "submitter", "priority": "BLOCKING", "message": "old"}],
            "failure_record": {"error_type": "SubmissionFailed", "error_message": "old failure"},
        }
    )

    captured = {}

    async def fake_execute_workflow(state):
        payload = state.model_dump()
        captured.update(payload)
        payload["status"] = "AWAITING_APPROVAL"
        payload["status_history"] = payload.get("status_history", []) + [
            {"status": "AWAITING_APPROVAL", "timestamp": "2026-04-10T02:00:00Z"}
        ]
        return payload

    monkeypatch.setattr("api.routes.applications._execute_workflow", fake_execute_workflow)
    resume = client.post(f"/api/apply/{app_id}/resume")
    assert resume.status_code == 200
    assert captured["human_escalations"] == []
    assert captured["failure_record"] is None
    assert captured["current_attempt"]["attempt_number"] == 2
    assert captured["current_attempt"]["trigger"] == "resume"
    assert captured["recovery_attempts"] == 1

    detail = client.get(f"/api/applications/{app_id}")
    assert detail.status_code == 200
    state = detail.json()["application"]["state"]
    assert state["attempt_history"][0]["attempt_number"] == 1
    assert state["attempt_history"][0]["final_status"] == "FAILED"


def test_resume_applies_runtime_overrides(monkeypatch, sample_persona):
    _reset_state()
    persona_routes._current_persona = sample_persona  # noqa: SLF001
    listing = _sample_listing("listing-resume-overrides")
    _seed_listing(listing)

    start = client.post(
        f"/api/apply/{listing['listing_id']}",
        json={"submission_mode": "shadow", "run_now": False},
    )
    app_id = start.json()["application_id"]
    applications_routes._applications[app_id]["status"] = "FAILED"  # noqa: SLF001
    applications_routes._applications[app_id]["state"]["status"] = "FAILED"  # noqa: SLF001

    captured = {}

    async def fake_execute_workflow(state):
        payload = state.model_dump()
        captured.update(payload)
        payload["status"] = "AWAITING_APPROVAL"
        payload["status_history"] = payload.get("status_history", []) + [
            {"status": "AWAITING_APPROVAL", "timestamp": "2026-04-10T04:00:00Z"}
        ]
        return payload

    monkeypatch.setattr("api.routes.applications._execute_workflow", fake_execute_workflow)

    resume = client.post(
        f"/api/apply/{app_id}/resume",
        json={
            "submission_mode": "live",
            "use_browser_automation": True,
            "headless": False,
            "apply_url": "https://boards.greenhouse.io/acme/jobs/override#app",
            "artifact_paths": {
                "resume": "data/processed/resume_override.pdf",
                "cover_letter": "data/processed/cover_override.pdf",
            },
        },
    )
    assert resume.status_code == 200
    assert captured["submission_mode"] == "live"
    assert captured["use_browser_automation"] is True
    assert captured["headless"] is False
    assert captured["apply_url"].endswith("override#app")
    assert captured["artifact_paths"]["resume"].endswith("resume_override.pdf")
    assert captured["artifact_paths"]["cover_letter"].endswith("cover_override.pdf")


def test_resume_uses_submission_failure_fast_path(monkeypatch, sample_persona):
    _reset_state()
    persona_routes._current_persona = sample_persona  # noqa: SLF001
    listing = _sample_listing("listing-resume-submission-fast-path")
    _seed_listing(listing)

    start = client.post(
        f"/api/apply/{listing['listing_id']}",
        json={"submission_mode": "live", "run_now": False},
    )
    app_id = start.json()["application_id"]
    applications_routes._applications[app_id]["status"] = "FAILED"  # noqa: SLF001
    applications_routes._applications[app_id]["state"].update(  # noqa: SLF001
        {
            "status": "FAILED",
            "submission_mode": "live",
            "fill_plan": {"fields": [{"field_id": "first_name", "type": "text_input"}]},
            "failure_record": {
                "error_type": "SubmissionFailed",
                "error_message": "submission failed",
                "failure_step": "submission",
            },
        }
    )

    async def fake_execute_workflow(_state):  # pragma: no cover
        raise AssertionError("resume should fast-path submission failures")

    async def fake_submission_node(state):
        payload = state.model_dump()
        return {
            "status": "SUBMITTED",
            "status_history": payload.get("status_history", []) + [
                {"status": "SUBMITTED", "timestamp": "2026-04-10T03:00:00Z"}
            ],
        }

    async def fake_record_outcome_node(state):
        payload = state.model_dump()
        return {
            "status_history": payload.get("status_history", []) + [
                {"status": "OUTCOME_RECORDED", "timestamp": "2026-04-10T03:00:01Z"}
            ]
        }

    monkeypatch.setattr("api.routes.applications._execute_workflow", fake_execute_workflow)
    monkeypatch.setattr("api.routes.applications.submission_node", fake_submission_node)
    monkeypatch.setattr("api.routes.applications.record_outcome_node", fake_record_outcome_node)

    resume = client.post(f"/api/apply/{app_id}/resume")
    assert resume.status_code == 200
    assert resume.json()["workflow_status"] == "SUBMITTED"

    detail = client.get(f"/api/applications/{app_id}")
    assert detail.status_code == 200
    state = detail.json()["application"]["state"]
    assert state["recovery_attempts"] == 1
    assert state["status"] == "SUBMITTED"
    assert state["recovery_checkpoint"]["last_status"] == "SUBMITTED"


def test_resume_with_apply_url_skips_submission_failure_fast_path(monkeypatch, sample_persona):
    _reset_state()
    persona_routes._current_persona = sample_persona  # noqa: SLF001
    listing = _sample_listing("listing-resume-submission-no-fast-path-with-override")
    _seed_listing(listing)

    start = client.post(
        f"/api/apply/{listing['listing_id']}",
        json={"submission_mode": "live", "run_now": False},
    )
    app_id = start.json()["application_id"]
    applications_routes._applications[app_id]["status"] = "FAILED"  # noqa: SLF001
    applications_routes._applications[app_id]["state"].update(  # noqa: SLF001
        {
            "status": "FAILED",
            "submission_mode": "live",
            "fill_plan": {"fields": [{"field_id": "first_name", "type": "text_input"}]},
            "failure_record": {
                "error_type": "SubmissionFailed",
                "error_message": "submission failed",
                "failure_step": "submission",
            },
        }
    )

    async def fake_submission_node(_state):  # pragma: no cover
        raise AssertionError("resume should skip fast-path when apply_url override is present")

    captured = {}

    async def fake_execute_workflow(state):
        payload = state.model_dump()
        captured["apply_url"] = payload.get("apply_url")
        payload["status"] = "AWAITING_APPROVAL"
        payload["status_history"] = payload.get("status_history", []) + [
            {"status": "AWAITING_APPROVAL", "timestamp": "2026-04-10T03:00:00Z"}
        ]
        return payload

    monkeypatch.setattr("api.routes.applications.submission_node", fake_submission_node)
    monkeypatch.setattr("api.routes.applications._execute_workflow", fake_execute_workflow)

    resume = client.post(
        f"/api/apply/{app_id}/resume",
        json={"apply_url": "https://example.com/override#app"},
    )
    assert resume.status_code == 200
    assert resume.json()["workflow_status"] == "AWAITING_APPROVAL"
    assert captured["apply_url"] == "https://example.com/override#app"


def test_blockers_resolve_clears_account_pii_and_preflight(monkeypatch, sample_persona):
    _reset_state()
    persona_routes._current_persona = sample_persona  # noqa: SLF001
    listing = _sample_listing("listing-blockers-resolve")
    _seed_listing(listing)

    start = client.post(
        f"/api/apply/{listing['listing_id']}",
        json={"submission_mode": "live", "run_now": False},
    )
    app_id = start.json()["application_id"]

    applications_routes._applications[app_id]["state"]["fill_plan"] = {  # noqa: SLF001
        "fields": [
            {"field_id": "first_name", "value": "{{FIRST_NAME}}", "pii_level": "LOW"},
            {"field_id": "salary_expectation", "value": "{{SALARY_EXPECTATION}}", "pii_level": "HIGH"},
        ]
    }
    applications_routes._applications[app_id]["state"]["human_escalations"] = [  # noqa: SLF001
        {"type": "account_manager", "priority": "BLOCKING", "message": "need credentials"},
        {"type": "form_interpreter", "field_id": "salary_expectation", "priority": "BLOCKING", "message": "need salary"},
        {"type": "pii_injector", "field_id": "first_name", "priority": "IMPORTANT", "message": "token missing"},
        {"type": "submitter", "field_id": "__preflight__", "priority": "BLOCKING", "message": "blocked"},
    ]

    class _FakeAccountVault:
        def store_account(self, **kwargs):
            return "acct-1"

    class _FakePIIVault:
        def __init__(self):
            self.tokens = {}

        def store_token(self, token_key, value, category="LOW"):
            self.tokens[token_key] = value

        def get_token(self, token_key):
            return self.tokens.get(token_key)

    fake_vault = _FakePIIVault()
    monkeypatch.setattr("api.routes.applications.AccountVault", _FakeAccountVault)
    monkeypatch.setattr("api.routes.applications.PIIVault", lambda: fake_vault)

    response = client.post(
        f"/api/apply/{app_id}/blockers/resolve",
        json={
            "account_username": "jane@example.com",
            "account_password": "secret-pass",
            "pii_values": {"first_name": "Jane"},
            "salary_expectation": "180000",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["remaining_blocking_escalations"] == 0

    detail = client.get(f"/api/applications/{app_id}")
    state = detail.json()["application"]["state"]
    assert state["account_credentials"]["username"] == "jane@example.com"
    assert state["fill_plan"]["fields"][1]["value"] == "180000"
    assert all(item.get("type") != "account_manager" for item in state["human_escalations"])
    assert all(item.get("field_id") != "salary_expectation" for item in state["human_escalations"])
    assert all(item.get("field_id") != "__preflight__" for item in state["human_escalations"])


def test_blockers_resolve_can_run_now(monkeypatch, sample_persona):
    _reset_state()
    persona_routes._current_persona = sample_persona  # noqa: SLF001
    listing = _sample_listing("listing-blockers-run-now")
    _seed_listing(listing)

    start = client.post(
        f"/api/apply/{listing['listing_id']}",
        json={"submission_mode": "live", "run_now": False},
    )
    app_id = start.json()["application_id"]
    applications_routes._applications[app_id]["status"] = "FAILED"  # noqa: SLF001
    applications_routes._applications[app_id]["state"]["status"] = "FAILED"  # noqa: SLF001
    applications_routes._applications[app_id]["state"]["human_escalations"] = [  # noqa: SLF001
        {"type": "account_manager", "priority": "BLOCKING", "message": "need credentials"},
    ]

    class _FakeAccountVault:
        def store_account(self, **kwargs):
            return "acct-1"

    class _FakePIIVault:
        def store_token(self, token_key, value, category="LOW"):
            return None

        def get_token(self, token_key):
            return None

    captured = {}

    async def fake_execute_workflow(state):
        payload = state.model_dump()
        captured.update(payload)
        payload["status"] = "AWAITING_APPROVAL"
        payload["status_history"] = payload.get("status_history", []) + [
            {"status": "AWAITING_APPROVAL", "timestamp": "2026-04-10T05:00:00Z"}
        ]
        return payload

    monkeypatch.setattr("api.routes.applications.AccountVault", _FakeAccountVault)
    monkeypatch.setattr("api.routes.applications.PIIVault", _FakePIIVault)
    monkeypatch.setattr("api.routes.applications._execute_workflow", fake_execute_workflow)

    response = client.post(
        f"/api/apply/{app_id}/blockers/resolve",
        json={
            "account_username": "jane@example.com",
            "account_password": "secret-pass",
            "run_now": True,
            "submission_mode": "live",
            "use_browser_automation": True,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["workflow_status"] == "AWAITING_APPROVAL"
    assert captured["account_credentials"]["username"] == "jane@example.com"
    assert captured["use_browser_automation"] is True


def test_blockers_resolve_run_now_uses_approve_path_for_awaiting_approval(
    monkeypatch, sample_persona
):
    _reset_state()
    persona_routes._current_persona = sample_persona  # noqa: SLF001
    listing = _sample_listing("listing-blockers-awaiting-approve-path")
    _seed_listing(listing)

    start = client.post(
        f"/api/apply/{listing['listing_id']}",
        json={"submission_mode": "live", "run_now": False},
    )
    app_id = start.json()["application_id"]
    applications_routes._applications[app_id]["status"] = "AWAITING_APPROVAL"  # noqa: SLF001
    applications_routes._applications[app_id]["state"]["status"] = "AWAITING_APPROVAL"  # noqa: SLF001
    applications_routes._applications[app_id]["state"]["human_escalations"] = []  # noqa: SLF001

    async def fake_approve_application(app_id, request):  # noqa: ANN001
        return {"status": "success", "application_id": app_id, "workflow_status": "SUBMITTED"}

    async def fake_resume_application(app_id, request):  # noqa: ANN001  # pragma: no cover
        raise AssertionError("resolve_blockers should not call resume for AWAITING_APPROVAL")

    monkeypatch.setattr("api.routes.applications.approve_application", fake_approve_application)
    monkeypatch.setattr("api.routes.applications.resume_application", fake_resume_application)

    response = client.post(
        f"/api/apply/{app_id}/blockers/resolve",
        json={
            "run_now": True,
            "submission_mode": "live",
            "use_browser_automation": True,
            "headless": True,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["workflow_status"] == "SUBMITTED"


def test_blockers_resolve_clears_pii_escalation_when_field_is_provided(monkeypatch, sample_persona):
    _reset_state()
    persona_routes._current_persona = sample_persona  # noqa: SLF001
    listing = _sample_listing("listing-blockers-pii-field-override")
    _seed_listing(listing)

    start = client.post(
        f"/api/apply/{listing['listing_id']}",
        json={"submission_mode": "live", "run_now": False},
    )
    app_id = start.json()["application_id"]

    # Simulate a prior attempt where fill plan values are no longer tokenized,
    # but a lingering pii_injector escalation still exists for the field id.
    applications_routes._applications[app_id]["state"]["fill_plan"] = {  # noqa: SLF001
        "fields": [
            {"field_id": "education_school", "value": "Old Value", "pii_level": "LOW"},
        ]
    }
    applications_routes._applications[app_id]["state"]["human_escalations"] = [  # noqa: SLF001
        {
            "type": "pii_injector",
            "field_id": "education_school",
            "priority": "IMPORTANT",
            "message": "PII token unresolved in local vault.",
        }
    ]

    class _FakePIIVault:
        def __init__(self):
            self.tokens = {}

        def store_token(self, token_key, value, category="LOW"):
            self.tokens[token_key] = value

        def get_token(self, token_key):
            return self.tokens.get(token_key)

    monkeypatch.setattr("api.routes.applications.PIIVault", _FakePIIVault)

    response = client.post(
        f"/api/apply/{app_id}/blockers/resolve",
        json={"pii_values": {"education_school": "University of Testing"}},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["remaining_escalations"] == 0

    detail = client.get(f"/api/applications/{app_id}")
    state = detail.json()["application"]["state"]
    assert state["human_escalations"] == []


def test_blockers_resolve_keeps_remaining_blockers_and_readds_single_preflight(sample_persona):
    _reset_state()
    persona_routes._current_persona = sample_persona  # noqa: SLF001
    listing = _sample_listing("listing-blockers-preflight-merge")
    _seed_listing(listing)

    start = client.post(
        f"/api/apply/{listing['listing_id']}",
        json={"submission_mode": "live", "run_now": False},
    )
    app_id = start.json()["application_id"]

    applications_routes._applications[app_id]["state"]["human_escalations"] = [  # noqa: SLF001
        {
            "type": "form_interpreter",
            "field_id": "salary_expectation",
            "priority": "BLOCKING",
            "message": "Need manual salary approval",
        }
    ]

    response = client.post(
        f"/api/apply/{app_id}/blockers/resolve",
        json={"run_now": False},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["remaining_blocking_escalations"] >= 1

    detail = client.get(f"/api/applications/{app_id}")
    state = detail.json()["application"]["state"]
    blockers = [
        item
        for item in state["human_escalations"]
        if str(item.get("priority", "")).upper() == "BLOCKING"
    ]
    preflight = [item for item in blockers if item.get("field_id") == "__preflight__"]
    assert len(preflight) == 1
    assert any(item.get("field_id") == "salary_expectation" for item in blockers)


def test_application_detail_includes_recovery_checkpoint(sample_persona):
    _reset_state()
    persona_routes._current_persona = sample_persona  # noqa: SLF001
    listing = _sample_listing("listing-recovery-checkpoint")
    _seed_listing(listing)

    start = client.post(
        f"/api/apply/{listing['listing_id']}",
        json={"submission_mode": "shadow", "run_now": False},
    )
    app_id = start.json()["application_id"]

    detail = client.get(f"/api/applications/{app_id}")
    assert detail.status_code == 200
    checkpoint = detail.json()["application"]["state"]["recovery_checkpoint"]
    assert checkpoint["last_status"] == "QUEUED"
    assert checkpoint["fields_filled_count"] == 0
    assert checkpoint["browser_url"].endswith("#app")


def test_status_sync_endpoint_uses_status_tracker(monkeypatch, sample_persona):
    _reset_state()
    persona_routes._current_persona = sample_persona  # noqa: SLF001
    listing = _sample_listing("listing-status-sync")
    _seed_listing(listing)

    start = client.post(
        f"/api/apply/{listing['listing_id']}",
        json={"submission_mode": "shadow", "run_now": False},
    )
    app_id = start.json()["application_id"]

    captured = {}

    def fake_track_status_updates(
        applications,
        router=None,
        inbox_client=None,
        emails=None,
        since=None,
        query=None,
        include_no_response=True,
        no_response_days=30,
        persist=False,
        outcomes_db_path="data/outcomes.db",
    ):
        captured["query"] = query
        return [
            {
                "application_id": app_id,
                "status": "RECEIVED",
                "detected_at": "2026-04-10T00:00:00Z",
            }
        ]

    monkeypatch.setattr("api.routes.applications.track_status_updates", fake_track_status_updates)

    sync = client.post(
        "/api/applications/status-sync",
        json={"since_days": 30, "persist": True, "query": "Acme Test Corp"},
    )
    assert sync.status_code == 200
    payload = sync.json()
    assert payload["scanned"] == 1
    assert payload["updates"][0]["status"] == "RECEIVED"
    assert captured["query"] == "Acme Test Corp"


def test_status_sync_endpoint_accepts_empty_body(monkeypatch, sample_persona):
    _reset_state()
    persona_routes._current_persona = sample_persona  # noqa: SLF001
    listing = _sample_listing("listing-status-sync-empty-body")
    _seed_listing(listing)

    start = client.post(
        f"/api/apply/{listing['listing_id']}",
        json={"submission_mode": "shadow", "run_now": False},
    )
    app_id = start.json()["application_id"]

    captured = {}

    def fake_track_status_updates(
        applications,
        router=None,
        inbox_client=None,
        emails=None,
        since=None,
        query=None,
        include_no_response=True,
        no_response_days=30,
        persist=False,
        outcomes_db_path="data/outcomes.db",
    ):
        captured["since"] = since
        captured["query"] = query
        captured["include_no_response"] = include_no_response
        captured["no_response_days"] = no_response_days
        captured["persist"] = persist
        return [
            {
                "application_id": app_id,
                "status": "RECEIVED",
                "detected_at": "2026-04-10T00:00:00Z",
            }
        ]

    monkeypatch.setattr("api.routes.applications.track_status_updates", fake_track_status_updates)

    sync = client.post("/api/applications/status-sync")
    assert sync.status_code == 200
    payload = sync.json()
    assert payload["status"] == "success"
    assert payload["scanned"] == 1
    assert payload["updates"][0]["status"] == "RECEIVED"
    assert captured["query"] is None
    assert captured["include_no_response"] is True
    assert captured["no_response_days"] == 30
    assert captured["persist"] is True
