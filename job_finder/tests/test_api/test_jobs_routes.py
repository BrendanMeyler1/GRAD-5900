"""Tests for Phase 2 Step 10 jobs API routes."""

from fastapi.testclient import TestClient

from api.main import app
from api.routes import persona as persona_routes
from api.routes import jobs as jobs_routes


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
    jobs_routes.reset_jobs_state()
    persona_routes._current_persona = None  # noqa: SLF001


def test_jobs_scan_queue_flag_skip(monkeypatch, sample_persona):
    _reset_state()
    persona_routes._current_persona = sample_persona  # noqa: SLF001

    queue_listing = _sample_listing("listing-queue-1")
    deprioritized_listing = _sample_listing("listing-deprioritized-1")
    deprioritized_listing["smart_skip_recommended"] = True

    def fake_build_decision_queue(*args, **kwargs):
        return {
            "queue": [queue_listing],
            "deprioritized": [deprioritized_listing],
        }

    monkeypatch.setattr("api.routes.jobs.build_decision_queue", fake_build_decision_queue)

    scan = client.post(
        "/api/jobs/scan",
        json={
            "listings": [queue_listing, deprioritized_listing],
            "use_llm": False,
            "max_results": 25,
        },
    )
    assert scan.status_code == 200
    assert scan.json()["queue_size"] == 1
    assert scan.json()["deprioritized_size"] == 1

    queue = client.get("/api/jobs/queue")
    assert queue.status_code == 200
    payload = queue.json()
    assert payload["count"] == 1
    assert payload["queue"][0]["listing_id"] == "listing-queue-1"
    assert payload["queue"][0]["decision"] == "pending"

    flag = client.post("/api/jobs/listing-queue-1/flag", json={"reason": "salary missing"})
    assert flag.status_code == 200
    assert flag.json()["decision"] == "flagged"

    queue_after_flag = client.get("/api/jobs/queue").json()
    assert queue_after_flag["queue"][0]["decision"] == "flagged"
    assert queue_after_flag["queue"][0]["flag_reason"] == "salary missing"

    skip = client.post("/api/jobs/listing-queue-1/skip")
    assert skip.status_code == 200
    assert skip.json()["decision"] == "skipped"

    queue_after_skip = client.get("/api/jobs/queue").json()
    assert queue_after_skip["count"] == 0


def test_jobs_scan_validates_persona_and_input(sample_persona):
    _reset_state()
    listing = _sample_listing("listing-1")

    no_persona = client.post("/api/jobs/scan", json={"listings": [listing]})
    assert no_persona.status_code == 400
    assert "No persona available" in no_persona.json()["detail"]

    persona_routes._current_persona = sample_persona  # noqa: SLF001
    no_listings = client.post("/api/jobs/scan", json={"listings": []})
    assert no_listings.status_code == 400
    assert "No listings provided" in no_listings.json()["detail"]


def test_jobs_skip_and_flag_404_for_unknown_listing():
    _reset_state()

    skip = client.post("/api/jobs/does-not-exist/skip")
    assert skip.status_code == 404

    flag = client.post("/api/jobs/does-not-exist/flag", json={"reason": "manual"})
    assert flag.status_code == 404

