"""Tests for Phase 3 Step 3 insights API routes."""

from fastapi.testclient import TestClient

from api.main import app
from api.routes import applications as applications_routes
from api.routes import jobs as jobs_routes


client = TestClient(app)


def _reset_state() -> None:
    applications_routes.reset_applications_state()
    jobs_routes.reset_jobs_state()


def test_insights_overview_reports_funnel_metrics(monkeypatch):
    _reset_state()
    jobs_routes._listings_by_id["listing-1"] = {"listing_id": "listing-1"}  # noqa: SLF001
    jobs_routes._listing_decisions["listing-1"] = "pending"  # noqa: SLF001

    async def fake_list_applications():
        return {
            "status": "success",
            "count": 3,
            "applications": [
                {
                    "application_id": "app-1",
                    "listing_id": "listing-1",
                    "company": "Acme",
                    "role_title": "Backend Engineer",
                    "status": "SUBMITTED",
                },
                {
                    "application_id": "app-2",
                    "listing_id": "listing-2",
                    "company": "Acme",
                    "role_title": "Backend Engineer",
                    "status": "INTERVIEW_SCHEDULED",
                },
                {
                    "application_id": "app-3",
                    "listing_id": "listing-3",
                    "company": "Acme",
                    "role_title": "Backend Engineer",
                    "status": "REJECTED",
                },
            ],
        }

    monkeypatch.setattr("api.routes.insights.applications_routes.list_applications", fake_list_applications)

    response = client.get("/api/insights/overview")
    assert response.status_code == 200
    payload = response.json()
    overview = payload["overview"]

    assert payload["status"] == "success"
    assert overview["queue_count"] == 1
    assert overview["application_count"] == 3
    assert overview["submitted_like_count"] == 3
    assert overview["interview_count"] == 1
    assert overview["rejected_count"] == 1
    assert overview["submission_rate_pct"] == 100.0


def test_insights_failures_uses_failure_store(monkeypatch):
    _reset_state()

    class FakeFailureStore:
        def top_failure_patterns(self, limit: int = 10):
            return [{"ats_type": "greenhouse", "error_type": "ATSFormError", "failure_step": "fill_form", "count": 2}]

        def list_recent(self, limit: int = 10):
            return [{"failure_id": "f-1", "error_type": "ATSFormError"}]

    monkeypatch.setattr("api.routes.insights.FailureStore", FakeFailureStore)

    response = client.get("/api/insights/failures?limit=5&include_recent=true")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["limit"] == 5
    assert payload["patterns"][0]["count"] == 2
    assert payload["recent"][0]["failure_id"] == "f-1"
