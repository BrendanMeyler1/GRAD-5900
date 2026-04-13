"""Tests for Phase 3 Step 3 batch API routes."""

from fastapi.testclient import TestClient

from api.main import app
from api.routes import applications as applications_routes
from api.routes import jobs as jobs_routes
from api.routes import persona as persona_routes


client = TestClient(app)


def _sample_listing(listing_id: str, company: str = "Acme Test Corp", role: str = "Backend Engineer") -> dict:
    return {
        "listing_id": listing_id,
        "source": "greenhouse",
        "source_url": f"https://boards.greenhouse.io/acme/jobs/{listing_id}",
        "apply_url": f"https://boards.greenhouse.io/acme/jobs/{listing_id}#app",
        "company": {"name": company},
        "role": {
            "title": role,
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


def test_batch_candidates_groups_similar_listings():
    _reset_state()
    _seed_listing(_sample_listing("listing-1", company="Acme Test Corp", role="Backend Engineer"))
    _seed_listing(_sample_listing("listing-2", company="Acme Test Corp", role="Backend Engineer"))
    _seed_listing(_sample_listing("listing-3", company="Different Corp", role="Data Engineer"))

    response = client.get("/api/batch/candidates")
    assert response.status_code == 200
    payload = response.json()

    assert payload["status"] == "success"
    assert payload["count"] == 1
    assert payload["groups"][0]["company"] == "Acme Test Corp"
    assert payload["groups"][0]["role_title"] == "Backend Engineer"
    assert sorted(payload["groups"][0]["listing_ids"]) == ["listing-1", "listing-2"]


def test_batch_approve_returns_started_and_failed(monkeypatch):
    _reset_state()

    async def fake_start_application(listing_id: str, request):
        if listing_id == "listing-fail":
            raise RuntimeError("intentional fail")
        return {
            "status": "success",
            "application_id": f"app-{listing_id}",
            "workflow_status": "QUEUED",
        }

    monkeypatch.setattr(
        "api.routes.batch.applications_routes.start_application",
        fake_start_application,
    )

    response = client.post(
        "/api/batch/approve",
        json={
            "listing_ids": ["listing-ok", "listing-fail", "listing-ok"],
            "submission_mode": "shadow",
            "run_now": False,
        },
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["requested"] == 2  # duplicate deduped
    assert payload["started"] == 1
    assert payload["failed"] == 1
    results = {item["listing_id"]: item for item in payload["results"]}
    assert results["listing-ok"]["status"] == "started"
    assert results["listing-fail"]["status"] == "failed"
