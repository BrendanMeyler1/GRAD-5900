"""Tests for Phase 3 Step 3 settings API routes."""

from fastapi.testclient import TestClient

from api.main import app
from api.routes import settings as settings_routes


client = TestClient(app)


def _reset_state() -> None:
    settings_routes.reset_settings_state()


def test_get_and_update_settings():
    _reset_state()

    initial = client.get("/api/settings")
    assert initial.status_code == 200
    assert initial.json()["status"] == "success"
    assert "daily_application_cap" in initial.json()["settings"]

    updated = client.put(
        "/api/settings",
        json={
            "daily_application_cap": 15,
            "per_ats_hourly_cap": 4,
            "default_submission_mode": "shadow",
            "use_browser_automation": True,
            "headless": False,
            "cooldown_seconds": 240,
        },
    )
    assert updated.status_code == 200
    settings_payload = updated.json()["settings"]
    assert settings_payload["daily_application_cap"] == 15
    assert settings_payload["per_ats_hourly_cap"] == 4
    assert settings_payload["use_browser_automation"] is True
    assert settings_payload["headless"] is False
    assert settings_payload["cooldown_seconds"] == 240


def test_update_settings_rejects_invalid_values():
    _reset_state()
    response = client.put("/api/settings", json={"daily_application_cap": 0})
    assert response.status_code == 422
