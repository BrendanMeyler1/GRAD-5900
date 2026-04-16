"""
Integration test for api/routes/jobs.py — end-to-end with TestClient.

Uses a real SQLite DB (per-test tmp_path) with mock job_scout so we test
the full request → route → Store → response cycle without real scrapers.
Verifies: list, search, add-url, get, queue, skip, status.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from api.routes.jobs import router as jobs_router


@pytest.fixture
def test_app(seeded_store, sample_job):
    """Minimal FastAPI app wired to the seeded store."""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(jobs_router, prefix="/api/jobs")

    # Override dependencies
    from api.dependencies import get_job_scout, get_store

    mock_scout = MagicMock()
    mock_scout.discover = AsyncMock(return_value=[])

    app.dependency_overrides[get_store] = lambda: seeded_store
    app.dependency_overrides[get_job_scout] = lambda: mock_scout

    return app


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


def test_list_jobs_returns_seeded(client) -> None:
    resp = client.get("/api/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert any(j["company"] == "Stripe" for j in data)


def test_list_jobs_filters_by_status(client, seeded_store, sample_job) -> None:
    seeded_store.update_job_status(sample_job.id, "queued")
    resp = client.get("/api/jobs", params={"status": "queued"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["status"] == "queued"


def test_list_jobs_filters_by_min_fit_score(
    client, seeded_store, sample_job
) -> None:
    seeded_store.update_job_fit(
        sample_job.id,
        score=92.0,
        summary="Great match.",
        strengths=["Python"],
        gaps=[],
        interview_likelihood="high",
    )
    resp = client.get("/api/jobs", params={"min_fit_score": 90})
    data = resp.json()
    assert len(data) == 1
    assert data[0]["fit_score"] >= 90


def test_get_job_detail(client, sample_job) -> None:
    resp = client.get(f"/api/jobs/{sample_job.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == sample_job.id
    assert data["company"] == "Stripe"


def test_get_job_not_found(client) -> None:
    resp = client.get("/api/jobs/does-not-exist")
    assert resp.status_code == 404


def test_queue_job(client, sample_job, seeded_store) -> None:
    resp = client.post(f"/api/jobs/{sample_job.id}/queue")
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"
    assert seeded_store.get_job(sample_job.id).status == "queued"


def test_skip_job(client, sample_job, seeded_store) -> None:
    resp = client.post(f"/api/jobs/{sample_job.id}/skip")
    assert resp.status_code == 200
    assert resp.json()["status"] == "skipped"


def test_update_status_valid(client, sample_job) -> None:
    resp = client.patch(
        f"/api/jobs/{sample_job.id}/status",
        json={"status": "archived"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "archived"


def test_update_status_invalid(client, sample_job) -> None:
    resp = client.patch(
        f"/api/jobs/{sample_job.id}/status",
        json={"status": "banana"},
    )
    assert resp.status_code == 400


def test_add_url_creates_job(client) -> None:
    resp = client.post(
        "/api/jobs/add-url",
        json={
            "url": "https://boards.greenhouse.io/stripe/jobs/999",
            "title": "ML Engineer",
            "company": "Stripe",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "ML Engineer"
    assert data["ats_type"] == "greenhouse"
    assert data["source"] == "greenhouse"


def test_add_url_universal_ats(client) -> None:
    resp = client.post(
        "/api/jobs/add-url",
        json={"url": "https://somecompany.com/careers/apply"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ats_type"] == "universal"
    assert data["source"] == "manual"


def test_search_returns_task_id(client) -> None:
    resp = client.post(
        "/api/jobs/search",
        json={"query": "python backend", "location": "Boston", "limit": 10},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "task_id" in data
    assert data["message"] == "Search started"
