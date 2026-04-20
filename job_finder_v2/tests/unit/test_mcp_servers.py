"""
Unit tests for mcp_servers/ — profile, jobs, files servers.

We use the `_StubServer` fallback (active when `mcp` is not importable OR
when we invoke `build_*_server` and access the registry via `.call(tool)`).
Each test exercises the tool's handler end-to-end against a real Store
so we verify: correct shape, writes hit the DB, and error paths return
structured errors (not exceptions).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_servers.files_server import _files_tool_registry
from mcp_servers.jobs_server import _jobs_tool_registry
from mcp_servers.profile_server import _StubServer, _profile_tool_registry


# ─── profile_server ──────────────────────────────────────────────────────────


async def test_profile_get_returns_full_profile(seeded_store) -> None:
    tools = _profile_tool_registry(seeded_store)
    result = await tools["get_profile"]["handler"]()

    assert result["profile"]["first_name"] == "Jane"
    assert result["profile"]["email"]  # non-empty
    assert len(result["education"]) >= 1
    assert len(result["experience"]) >= 1


async def test_profile_update_persists_to_db(seeded_store) -> None:
    tools = _profile_tool_registry(seeded_store)
    result = await tools["update_profile"]["handler"](
        fields={"target_salary_min": 125_000, "remote_preference": "remote"}
    )

    assert result["updated"] is True
    # Confirm via the Store directly
    profile = seeded_store.get_profile()
    assert profile.target_salary_min == 125_000
    assert profile.remote_preference == "remote"


async def test_profile_update_rejects_empty_fields(seeded_store) -> None:
    tools = _profile_tool_registry(seeded_store)
    result = await tools["update_profile"]["handler"](fields={})
    assert result["updated"] is False
    assert "no fields" in result["reason"].lower()


async def test_profile_add_qa_note_persists(seeded_store) -> None:
    tools = _profile_tool_registry(seeded_store)
    result = await tools["add_qa_note"]["handler"](
        question="Willing to relocate?",
        answer="Only to NYC or SF.",
        category="preference",
    )
    assert result["saved"] is True
    assert isinstance(result["id"], int)

    # list_qa_notes should return it
    listed = await tools["list_qa_notes"]["handler"]()
    assert any("relocate" in n["question"].lower() for n in listed["notes"])


async def test_profile_get_resume_text(seeded_store) -> None:
    tools = _profile_tool_registry(seeded_store)
    # sample_profile has resume_raw_text populated
    result = await tools["get_resume_text"]["handler"]()
    assert result["present"] is True
    assert "Jane Doe" in result["text"]


async def test_profile_get_completeness_reports_missing(store) -> None:
    """A brand-new (empty) store reports several missing fields."""
    tools = _profile_tool_registry(store)
    result = await tools["get_profile_completeness"]["handler"]()

    assert 0 <= result["completion_pct"] <= 100
    assert "name" in result["missing_fields"] or "email" in result["missing_fields"]
    assert isinstance(result["can_apply"], bool)


# ─── jobs_server ─────────────────────────────────────────────────────────────


async def test_jobs_list_returns_seeded_job(seeded_store) -> None:
    tools = _jobs_tool_registry(seeded_store)
    result = await tools["list_jobs"]["handler"]()

    assert result["count"] >= 1
    assert any(j["company"] == "Stripe" for j in result["jobs"])


async def test_jobs_list_filters_by_min_fit_score(seeded_store, sample_job) -> None:
    # Boost the seeded job's fit score
    seeded_store.update_job_fit(
        sample_job.id,
        score=85.0,
        summary="Strong.",
        strengths=["Python"],
        gaps=[],
        interview_likelihood="high",
    )
    tools = _jobs_tool_registry(seeded_store)
    high = await tools["list_jobs"]["handler"](min_fit_score=80)
    low = await tools["list_jobs"]["handler"](min_fit_score=90)

    assert high["count"] == 1
    assert low["count"] == 0


async def test_jobs_get_unknown_returns_error(seeded_store) -> None:
    tools = _jobs_tool_registry(seeded_store)
    result = await tools["get_job"]["handler"](job_id="does-not-exist")
    assert "error" in result


async def test_jobs_update_status_persists(seeded_store, sample_job) -> None:
    tools = _jobs_tool_registry(seeded_store)
    result = await tools["update_job_status"]["handler"](
        job_id=sample_job.id, status="queued"
    )
    assert result["updated"] is True

    job = seeded_store.get_job(sample_job.id)
    assert job.status == "queued"


async def test_jobs_list_applications_empty_initially(seeded_store) -> None:
    tools = _jobs_tool_registry(seeded_store)
    result = await tools["list_applications"]["handler"]()
    assert result["count"] == 0
    assert result["applications"] == []


async def test_jobs_application_roundtrip(seeded_store, sample_job) -> None:
    # Create an application via Store, then read it back via MCP tool
    app = seeded_store.create_application(
        job_id=sample_job.id, status="shadow_review"
    )
    tools = _jobs_tool_registry(seeded_store)

    listed = await tools["list_applications"]["handler"](status="shadow_review")
    assert listed["count"] == 1
    assert listed["applications"][0]["company"] == "Stripe"

    single = await tools["get_application"]["handler"](app_id=app.id)
    assert single["id"] == app.id
    assert single["status"] == "shadow_review"


# ─── files_server ────────────────────────────────────────────────────────────


async def test_files_read_missing_app_returns_error(seeded_store) -> None:
    tools = _files_tool_registry(seeded_store)
    result = await tools["read_tailored_resume"]["handler"](app_id="nope")
    assert "error" in result


async def test_files_write_and_read_cover_letter_roundtrip(
    seeded_store, sample_job, tmp_path, monkeypatch
) -> None:
    # Redirect generated_dir
    from config import settings

    monkeypatch.setattr(
        type(settings), "generated_dir", property(lambda self: tmp_path / "gen")
    )
    (tmp_path / "gen").mkdir()

    # Build registry AFTER the monkeypatch so gen_dir captures the new path
    tools = _files_tool_registry(seeded_store)

    app = seeded_store.create_application(job_id=sample_job.id, status="shadow_review")
    content = "Dear hiring team,\n\nI'm excited to apply..."

    write_result = await tools["write_cover_letter"]["handler"](
        app_id=app.id, content=content
    )
    assert write_result["written"] is True

    read_result = await tools["read_cover_letter"]["handler"](app_id=app.id)
    assert content in read_result["content"]


async def test_files_list_screenshots_empty(seeded_store, sample_job) -> None:
    tools = _files_tool_registry(seeded_store)
    app = seeded_store.create_application(job_id=sample_job.id, status="pending")
    result = await tools["list_screenshots"]["handler"](app_id=app.id)
    assert result["count"] == 0
    assert result["screenshots"] == []


async def test_files_get_fill_log_empty(seeded_store, sample_job) -> None:
    tools = _files_tool_registry(seeded_store)
    app = seeded_store.create_application(job_id=sample_job.id, status="pending")
    result = await tools["get_fill_log"]["handler"](app_id=app.id)
    assert result["fill_log"] == []
    assert result["custom_qa"] == {}


# ─── _StubServer ─────────────────────────────────────────────────────────────


async def test_stub_server_dispatches_tools(seeded_store) -> None:
    """The stub server's .call() method routes to handlers correctly."""
    stub = _StubServer("profile", _profile_tool_registry(seeded_store))
    result = await stub.call("get_profile")
    assert "profile" in result
    assert isinstance(stub.list_tools(), list)
    assert "get_profile" in stub.list_tools()


async def test_stub_server_unknown_tool_raises(seeded_store) -> None:
    stub = _StubServer("profile", _profile_tool_registry(seeded_store))
    with pytest.raises(ValueError, match="Unknown tool"):
        await stub.call("nonexistent_tool")


# ─── input schema sanity ──────────────────────────────────────────────────────


def test_all_profile_tools_have_input_schema(seeded_store) -> None:
    tools = _profile_tool_registry(seeded_store)
    for name, spec in tools.items():
        assert "description" in spec
        assert "input_schema" in spec
        assert spec["input_schema"]["type"] == "object"
        assert callable(spec["handler"])


def test_all_jobs_tools_have_input_schema(seeded_store) -> None:
    tools = _jobs_tool_registry(seeded_store)
    for name, spec in tools.items():
        assert "description" in spec
        assert "input_schema" in spec
        assert spec["input_schema"]["type"] == "object"


def test_all_files_tools_have_input_schema(seeded_store) -> None:
    tools = _files_tool_registry(seeded_store)
    for name, spec in tools.items():
        assert "description" in spec
        assert "input_schema" in spec
        assert spec["input_schema"]["type"] == "object"
