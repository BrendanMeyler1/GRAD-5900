"""Tests for Status Tracker agent."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

from agents.status_tracker import (
    IMAPInboxClient,
    StatusTracker,
    find_no_response_updates,
    persist_status_updates,
)
from setup.init_db import init_outcomes_db


def test_classify_email_normalizes_status():
    mock_router = MagicMock()
    mock_router.route_json.return_value = {
        "status": "interview_invite",
        "confidence": 0.92,
        "reason": "Contains scheduling language and interview request.",
        "matched_signals": ["schedule time", "interview"],
    }
    tracker = StatusTracker(router=mock_router)

    application = {
        "application_id": "app-123",
        "listing_id": "listing-123",
        "company": "Acme Test Corp",
        "role_title": "Senior Backend Engineer",
        "submitted_at": "2026-03-01T10:00:00+00:00",
    }
    email_record = {
        "message_id": "<msg-1@example.com>",
        "subject": "Interview request for Senior Backend Engineer",
        "from": "recruiter@acme.test",
        "date": "Thu, 02 Apr 2026 09:00:00 +0000",
        "body_excerpt": "Please share your availability to schedule an interview.",
    }

    update = tracker.classify_email(application=application, email_record=email_record)

    assert update["status"] == "INTERVIEW_SCHEDULED"
    assert update["confidence"] == 0.92
    assert update["application_id"] == "app-123"
    assert update["classification_source"] == "llm"
    mock_router.route_json.assert_called_once()
    assert mock_router.route_json.call_args.kwargs["task_type"] == "status_classification"


def test_scan_status_updates_matches_email_to_application():
    mock_router = MagicMock()
    mock_router.route_json.return_value = {
        "status": "REJECTED",
        "confidence": 0.88,
        "reason": "Email explicitly says not moving forward.",
        "matched_signals": ["not moving forward"],
    }
    tracker = StatusTracker(router=mock_router)

    application = {
        "application_id": "app-200",
        "listing_id": "listing-200",
        "company": "Acme Test Corp",
        "role_title": "Senior Backend Engineer",
        "status": "SUBMITTED",
        "submitted_at": "2026-03-20T10:00:00+00:00",
    }
    emails = [
        {
            "message_id": "<msg-a@example.com>",
            "subject": "Welcome to another company",
            "body_excerpt": "This should not match.",
        },
        {
            "message_id": "<msg-b@example.com>",
            "subject": "Update on your application at Acme Test Corp",
            "body_excerpt": "We are not moving forward for this Senior Backend Engineer role.",
        },
    ]

    updates = tracker.scan_status_updates(
        applications=[application],
        emails=emails,
        include_no_response=False,
    )

    assert len(updates) == 1
    assert updates[0]["application_id"] == "app-200"
    assert updates[0]["status"] == "REJECTED"


def test_scan_status_updates_does_not_match_role_only_email():
    mock_router = MagicMock()
    mock_router.route_json.return_value = {
        "status": "FOLLOW_UP_NEEDED",
        "confidence": 0.77,
        "reason": "Would match if classification was invoked.",
        "matched_signals": ["role_title_only"],
    }
    tracker = StatusTracker(router=mock_router)

    application = {
        "application_id": "app-role-only-1",
        "listing_id": "listing-role-only-1",
        "company": "Acme Test Corp",
        "role_title": "Software Engineer I",
        "status": "SUBMITTED",
        "submitted_at": "2026-03-20T10:00:00+00:00",
    }
    emails = [
        {
            "message_id": "<msg-role-only@example.com>",
            "subject": "Software Engineer I role and other opportunities open",
            "body_excerpt": "A digest of similar software roles.",
        }
    ]

    updates = tracker.scan_status_updates(
        applications=[application],
        emails=emails,
        include_no_response=False,
    )

    assert updates == []
    mock_router.route_json.assert_not_called()


def test_scan_status_updates_matches_application_id_without_company():
    mock_router = MagicMock()
    mock_router.route_json.return_value = {
        "status": "INTERVIEW_INVITE",
        "confidence": 0.9,
        "reason": "Matched via strong identifier.",
        "matched_signals": ["application_id"],
    }
    tracker = StatusTracker(router=mock_router)

    application = {
        "application_id": "app-strong-id-1",
        "listing_id": "listing-strong-id-1",
        "company": "Acme Test Corp",
        "role_title": "Senior Backend Engineer",
        "status": "SUBMITTED",
        "submitted_at": "2026-03-20T10:00:00+00:00",
    }
    emails = [
        {
            "message_id": "<msg-strong-id@example.com>",
            "subject": "Update for reference app-strong-id-1",
            "body_excerpt": "Please review the latest update.",
        }
    ]

    updates = tracker.scan_status_updates(
        applications=[application],
        emails=emails,
        include_no_response=False,
    )

    assert len(updates) == 1
    assert updates[0]["application_id"] == "app-strong-id-1"
    mock_router.route_json.assert_called_once()


def test_scan_status_updates_ignores_job_board_digest_with_company_body_text():
    mock_router = MagicMock()
    mock_router.route_json.return_value = {
        "status": "FOLLOW_UP_NEEDED",
        "confidence": 0.82,
        "reason": "Would match if invoked.",
        "matched_signals": ["company_mention"],
    }
    tracker = StatusTracker(router=mock_router)

    application = {
        "application_id": "app-digest-1",
        "listing_id": "listing-digest-1",
        "company": "Acme Test Corp",
        "role_title": "Software Engineer I",
        "status": "SUBMITTED",
        "submitted_at": "2026-03-20T10:00:00+00:00",
    }
    emails = [
        {
            "message_id": "<msg-digest@example.com>",
            "from": "LinkedIn Jobs <jobs-noreply@linkedin.com>",
            "subject": "Software Engineer I role and other opportunities open",
            "body_excerpt": "Acme Test Corp is hiring alongside many other companies.",
        }
    ]

    updates = tracker.scan_status_updates(
        applications=[application],
        emails=emails,
        include_no_response=False,
    )

    assert updates == []
    mock_router.route_json.assert_not_called()


def test_scan_status_updates_matches_body_company_when_sender_domain_aligns():
    mock_router = MagicMock()
    mock_router.route_json.return_value = {
        "status": "RECEIVED",
        "confidence": 0.93,
        "reason": "Company and sender domain aligned.",
        "matched_signals": ["company_domain_alignment"],
    }
    tracker = StatusTracker(router=mock_router)

    application = {
        "application_id": "app-domain-align-1",
        "listing_id": "listing-domain-align-1",
        "company": "Acme Test Corp",
        "role_title": "Senior Backend Engineer",
        "status": "SUBMITTED",
        "submitted_at": "2026-03-20T10:00:00+00:00",
    }
    emails = [
        {
            "message_id": "<msg-domain-align@example.com>",
            "from": "Talent Team <jobs@acmetestcorp.com>",
            "subject": "Application update",
            "body_excerpt": "Thanks for applying to Acme Test Corp. We received your application.",
        }
    ]

    updates = tracker.scan_status_updates(
        applications=[application],
        emails=emails,
        include_no_response=False,
    )

    assert len(updates) == 1
    assert updates[0]["application_id"] == "app-domain-align-1"
    assert updates[0]["status"] == "RECEIVED"
    mock_router.route_json.assert_called_once()


def test_scan_status_updates_ignores_email_older_than_submitted_at():
    mock_router = MagicMock()
    mock_router.route_json.return_value = {
        "status": "RECEIVED",
        "confidence": 0.91,
        "reason": "Would match if classification ran.",
        "matched_signals": ["company"],
    }
    tracker = StatusTracker(router=mock_router)

    application = {
        "application_id": "app-date-gate-1",
        "listing_id": "listing-date-gate-1",
        "company": "Acme Test Corp",
        "role_title": "Backend Engineer",
        "status": "SUBMITTED",
        "submitted_at": "2026-04-10T12:00:00+00:00",
    }
    emails = [
        {
            "message_id": "<msg-old@example.com>",
            "from": "Recruiting <jobs@acmetestcorp.com>",
            "subject": "Application update for Acme Test Corp",
            "date": "Thu, 09 Apr 2026 09:00:00 +0000",
            "body_excerpt": "Thanks for applying to Acme Test Corp.",
        }
    ]

    updates = tracker.scan_status_updates(
        applications=[application],
        emails=emails,
        include_no_response=False,
    )

    assert updates == []
    mock_router.route_json.assert_not_called()


def test_scan_status_updates_accepts_email_on_or_after_submitted_at():
    mock_router = MagicMock()
    mock_router.route_json.return_value = {
        "status": "RECEIVED",
        "confidence": 0.95,
        "reason": "Post-submission update.",
        "matched_signals": ["company", "date_gate_pass"],
    }
    tracker = StatusTracker(router=mock_router)

    application = {
        "application_id": "app-date-gate-2",
        "listing_id": "listing-date-gate-2",
        "company": "Acme Test Corp",
        "role_title": "Backend Engineer",
        "status": "SUBMITTED",
        "submitted_at": "2026-04-10T12:00:00+00:00",
    }
    emails = [
        {
            "message_id": "<msg-new@example.com>",
            "from": "Recruiting <jobs@acmetestcorp.com>",
            "subject": "Application update for Acme Test Corp",
            "date": "Fri, 10 Apr 2026 13:00:00 +0000",
            "body_excerpt": "Thanks for applying to Acme Test Corp.",
        }
    ]

    updates = tracker.scan_status_updates(
        applications=[application],
        emails=emails,
        include_no_response=False,
    )

    assert len(updates) == 1
    assert updates[0]["application_id"] == "app-date-gate-2"
    assert updates[0]["status"] == "RECEIVED"
    mock_router.route_json.assert_called_once()


def test_classify_email_falls_back_to_heuristics_when_llm_fails():
    mock_router = MagicMock()
    mock_router.route_json.side_effect = RuntimeError("llm unavailable")
    tracker = StatusTracker(router=mock_router)

    application = {
        "application_id": "app-heuristic-1",
        "listing_id": "listing-heuristic-1",
        "company": "Acme Test Corp",
        "role_title": "Senior Backend Engineer",
    }
    email_record = {
        "message_id": "<msg-fallback@example.com>",
        "subject": "Update on your application",
        "from": "recruiter@acme.test",
        "date": "Thu, 02 Apr 2026 09:00:00 +0000",
        "body_excerpt": "Unfortunately, we are not moving forward with your application.",
    }

    update = tracker.classify_email(application=application, email_record=email_record)
    assert update["status"] == "REJECTED"
    assert update["classification_source"] == "heuristic"
    assert update["confidence"] == 0.55
    assert "heuristic fallback" in update["reason"].lower()


def test_find_no_response_updates_after_threshold():
    as_of = datetime(2026, 4, 9, 12, 0, tzinfo=timezone.utc)
    applications = [
        {
            "application_id": "app-old",
            "listing_id": "listing-old",
            "company": "Acme Test Corp",
            "role_title": "Backend Engineer",
            "status": "SUBMITTED",
            "submitted_at": (as_of - timedelta(days=45)).isoformat(),
        },
        {
            "application_id": "app-fresh",
            "listing_id": "listing-fresh",
            "company": "Acme Test Corp",
            "role_title": "Backend Engineer",
            "status": "SUBMITTED",
            "submitted_at": (as_of - timedelta(days=10)).isoformat(),
        },
    ]

    updates = find_no_response_updates(applications=applications, threshold_days=30, as_of=as_of)

    assert len(updates) == 1
    assert updates[0]["application_id"] == "app-old"
    assert updates[0]["status"] == "NO_RESPONSE_30D"


def test_persist_status_updates_updates_outcomes_db():
    test_dir = Path(".tmp")
    test_dir.mkdir(parents=True, exist_ok=True)
    db_path = test_dir / f"outcomes_{uuid4().hex}.db"
    init_outcomes_db(db_path=str(db_path))

    created_at = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO applications (
                application_id, listing_id, company, role_title, ats_type, fit_score,
                alive_score, status, resume_version, cover_letter_ver, time_to_apply_s,
                human_interventions, submitted_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "app-db-1",
                "listing-db-1",
                "Acme Test Corp",
                "Senior Backend Engineer",
                "greenhouse",
                88,
                0.86,
                "SUBMITTED",
                None,
                None,
                120,
                0,
                created_at,
                created_at,
            ),
        )

    updates = [
        {
            "application_id": "app-db-1",
            "status": "REJECTED",
            "detected_at": created_at,
        }
    ]
    updated_rows = persist_status_updates(updates=updates, db_path=str(db_path))

    assert updated_rows == 1
    with sqlite3.connect(str(db_path)) as conn:
        app_row = conn.execute(
            "SELECT status FROM applications WHERE application_id = ?",
            ("app-db-1",),
        ).fetchone()
        history_row = conn.execute(
            """
            SELECT status FROM status_history
            WHERE application_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            ("app-db-1",),
        ).fetchone()

    assert app_row is not None and app_row[0] == "REJECTED"
    assert history_row is not None and history_row[0] == "REJECTED"


def test_imap_inbox_client_uses_oauth2_access_token(monkeypatch):
    monkeypatch.delenv("IMAP_OAUTH2_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("IMAP_OAUTH2_REFRESH_TOKEN", raising=False)

    class _FakeIMAPClient:
        def __init__(self, *args, **kwargs):
            self.auth_mech = None
            self.auth_payload = None
            self.login_called = False

        def authenticate(self, mech, auth_cb):
            self.auth_mech = mech
            self.auth_payload = auth_cb(b"").decode("utf-8", errors="replace")

        def login(self, username, password):  # pragma: no cover
            self.login_called = True

        def select(self, folder):
            return "OK", [b""]

        def search(self, *args):
            return "OK", [b""]

        def logout(self):
            return "BYE", [b""]

    fake_client = _FakeIMAPClient()
    monkeypatch.setattr("agents.status_tracker.imaplib.IMAP4_SSL", lambda *args, **kwargs: fake_client)

    inbox = IMAPInboxClient(
        host="imap.example.test",
        port=993,
        username="person@example.com",
        auth_mode="oauth2",
        oauth2_access_token="token-abc-123",
    )
    result = inbox.search_inbox(limit=1)

    assert result == []
    assert fake_client.auth_mech == "XOAUTH2"
    assert "user=person@example.com" in fake_client.auth_payload
    assert "Bearer token-abc-123" in fake_client.auth_payload
    assert fake_client.login_called is False


def test_imap_inbox_client_refreshes_oauth2_token(monkeypatch):
    monkeypatch.delenv("IMAP_OAUTH2_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("IMAP_OAUTH2_REFRESH_TOKEN", raising=False)

    class _FakeIMAPClient:
        def __init__(self, *args, **kwargs):
            self.auth_payload = None

        def authenticate(self, mech, auth_cb):
            self.auth_payload = auth_cb(b"").decode("utf-8", errors="replace")

        def select(self, folder):
            return "OK", [b""]

        def search(self, *args):
            return "OK", [b""]

        def logout(self):
            return "BYE", [b""]

    class _FakeHTTPResponse:
        def __init__(self, body: bytes):
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return self._body

    captured_request = {}

    def fake_urlopen(request, timeout=20):
        captured_request["url"] = request.full_url
        captured_request["body"] = request.data.decode("utf-8", errors="replace")
        return _FakeHTTPResponse(b'{"access_token":"refreshed-token-xyz","expires_in":3600}')

    fake_client = _FakeIMAPClient()
    monkeypatch.setattr("agents.status_tracker.urlopen", fake_urlopen)
    monkeypatch.setattr("agents.status_tracker.imaplib.IMAP4_SSL", lambda *args, **kwargs: fake_client)

    inbox = IMAPInboxClient(
        host="imap.example.test",
        port=993,
        username="person@example.com",
        auth_mode="oauth2",
        oauth2_refresh_token="refresh-token",
        oauth2_client_id="client-id-123",
        oauth2_tenant="consumers",
    )
    result = inbox.search_inbox(limit=1)

    assert result == []
    assert "grant_type=refresh_token" in captured_request["body"]
    assert "client_id=client-id-123" in captured_request["body"]
    assert "refresh_token=refresh-token" in captured_request["body"]
    assert "Bearer refreshed-token-xyz" in fake_client.auth_payload
