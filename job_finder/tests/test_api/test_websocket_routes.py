"""Tests for Phase 3 Step 3 websocket routes."""

from fastapi.testclient import TestClient

from api.main import app
from api.routes import applications as applications_routes
from api.routes import jobs as jobs_routes


client = TestClient(app)


def _reset_state() -> None:
    applications_routes.reset_applications_state()
    jobs_routes.reset_jobs_state()


def test_ws_queue_streams_snapshot():
    _reset_state()
    jobs_routes._listings_by_id["listing-queue-1"] = {  # noqa: SLF001
        "listing_id": "listing-queue-1",
        "company": {"name": "Acme Test Corp"},
        "role": {"title": "Backend Engineer"},
        "ats_type": "greenhouse",
    }
    jobs_routes._listing_decisions["listing-queue-1"] = "pending"  # noqa: SLF001

    with client.websocket_connect("/ws/queue") as ws:
        payload = ws.receive_json()
        assert payload["type"] == "queue_snapshot"
        assert payload["count"] == 1
        assert payload["queue"][0]["listing_id"] == "listing-queue-1"


def test_ws_application_streams_snapshot():
    _reset_state()
    applications_routes._applications["app-ws-1"] = {  # noqa: SLF001
        "application_id": "app-ws-1",
        "listing_id": "listing-ws-1",
        "status": "QUEUED",
        "status_history": [{"status": "QUEUED", "timestamp": "2026-04-10T00:00:00Z"}],
        "state": {"human_escalations": []},
    }

    with client.websocket_connect("/ws/application/app-ws-1") as ws:
        payload = ws.receive_json()
        assert payload["type"] == "application_snapshot"
        assert payload["application_id"] == "app-ws-1"
        assert payload["status"] == "QUEUED"


def test_ws_application_missing_sends_error():
    _reset_state()
    with client.websocket_connect("/ws/application/missing-app") as ws:
        payload = ws.receive_json()
        assert payload["type"] == "error"
        assert "not found" in payload["message"].lower()
