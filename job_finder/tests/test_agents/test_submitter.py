"""Tests for submission pipeline agent."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from agents.submitter import Submitter, _build_fields_filled, check_submission_rate_limit
from browser.humanizer import Humanizer, HumanizerConfig
from setup.init_db import init_outcomes_db


class _FakeStrategy:
    def __init__(self, execute_result: dict | None = None):
        self.execute_result = execute_result or {
            "status": "success",
            "submitted": False,
            "planned_actions": 1,
            "executed_count": 1,
            "executed_actions": [
                {
                    "field_id": "first_name",
                    "action": "fill_text",
                    "selector": "#first_name",
                    "value": "Jane",
                    "confidence": 0.98,
                    "source": "template",
                    "result": {"status": "filled"},
                }
            ],
            "failures": [],
        }

    def plan_actions(self, fill_plan: dict, artifact_paths=None):
        return (
            [
                {
                    "field_id": "first_name",
                    "action": "fill_text",
                    "selector": "#first_name",
                    "value": "Jane",
                }
            ],
            [],
        )

    async def execute_fill_plan(self, driver, fill_plan, artifact_paths=None, submit=False, submit_selector=""):
        result = dict(self.execute_result)
        result["submitted"] = bool(submit and result.get("submitted", False))
        return result


class _FakeDriver:
    def __init__(self, headless=True, humanizer=None):
        self.headless = headless
        self.humanizer = humanizer
        self.started = False
        self.stopped = False
        self.context = object()
        self.goto_url = None

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def goto(self, url: str):
        self.goto_url = url
        return {"url": url}

    async def screenshot(self, path: str, full_page: bool = True):
        return {"path": path, "status": "saved"}


def _listing(ats_type: str = "greenhouse") -> dict:
    return {
        "listing_id": "listing-1",
        "ats_type": ats_type,
        "apply_url": "https://boards.greenhouse.io/acme/jobs/123#app",
        "company": {"name": "Acme"},
    }


def _fill_plan() -> dict:
    return {
        "fields": [
            {
                "field_id": "first_name",
                "label": "First Name",
                "type": "text_input",
                "selector": "#first_name",
                "selector_strategy": "exact_css",
                "value": "Jane",
                "confidence": 0.98,
            }
        ]
    }


def test_submitter_dry_run_does_not_start_browser():
    created = {"count": 0}

    def factory(**kwargs):
        created["count"] += 1
        return _FakeDriver(**kwargs)

    submitter = Submitter(
        driver_factory=factory,
        strategy_registry={"greenhouse": _FakeStrategy()},
    )
    result = asyncio.run(
        submitter.run_submission(
            listing=_listing(),
            fill_plan=_fill_plan(),
            submission_mode="dry_run",
        )
    )
    assert result["status"] == "DRY_RUN"
    assert result["planned_actions"] == 1
    assert created["count"] == 0


def test_submitter_shadow_mode_returns_shadow_review():
    submitter = Submitter(
        driver_factory=lambda **kwargs: _FakeDriver(**kwargs),
        strategy_registry={"greenhouse": _FakeStrategy()},
    )
    result = asyncio.run(
        submitter.run_submission(
            listing=_listing(),
            fill_plan=_fill_plan(),
            submission_mode="shadow",
            capture_screenshot=False,
        )
    )
    assert result["status"] == "SHADOW_REVIEW"
    assert result["fields_filled"][0]["field_id"] == "first_name"
    assert result["human_escalations"] == []


def test_submitter_live_mode_registers_rate_limit_and_blocks_next():
    now = {"value": datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)}
    humanizer = Humanizer(
        config=HumanizerConfig(daily_cap=10, per_ats_limit=1, per_ats_window_seconds=3600),
        now_fn=lambda: now["value"],
    )
    strategy = _FakeStrategy(
        execute_result={
            "status": "success",
            "submitted": True,
            "planned_actions": 1,
            "executed_count": 1,
            "executed_actions": [
                {
                    "field_id": "first_name",
                    "action": "fill_text",
                    "selector": "#first_name",
                    "value": "Jane",
                    "confidence": 0.98,
                    "source": "template",
                    "result": {"status": "filled"},
                }
            ],
            "failures": [],
        }
    )
    submitter = Submitter(
        driver_factory=lambda **kwargs: _FakeDriver(**kwargs),
        strategy_registry={"greenhouse": strategy},
        humanizer=humanizer,
    )

    first = asyncio.run(
        submitter.run_submission(
            listing=_listing(),
            fill_plan=_fill_plan(),
            submission_mode="live",
            capture_screenshot=False,
        )
    )
    assert first["status"] == "SUBMITTED"
    assert first["rate_limit"]["ats_used"] == 1

    second = asyncio.run(
        submitter.run_submission(
            listing=_listing(),
            fill_plan=_fill_plan(),
            submission_mode="live",
            capture_screenshot=False,
        )
    )
    assert second["status"] == "FAILED"
    assert second["failure_record"]["error_type"] == "RateLimitBlocked"


def test_submitter_handles_strategy_failure_with_escalation():
    strategy = _FakeStrategy(
        execute_result={
            "status": "partial",
            "submitted": False,
            "planned_actions": 1,
            "executed_count": 0,
            "executed_actions": [],
            "failures": [
                {
                    "field_id": "first_name",
                    "error_type": "ATSFormError",
                    "error_message": "selector failed",
                }
            ],
        }
    )
    submitter = Submitter(
        driver_factory=lambda **kwargs: _FakeDriver(**kwargs),
        strategy_registry={"greenhouse": strategy},
    )
    result = asyncio.run(
        submitter.run_submission(
            listing=_listing(),
            fill_plan=_fill_plan(),
            submission_mode="live",
            capture_screenshot=False,
        )
    )
    assert result["status"] == "FAILED"
    assert result["human_escalations"][0]["priority"] == "BLOCKING"


def test_submitter_surfaces_listing_inactive_failure_type():
    strategy = _FakeStrategy(
        execute_result={
            "status": "partial",
            "submitted": False,
            "planned_actions": 1,
            "executed_count": 0,
            "executed_actions": [],
            "failures": [
                {
                    "field_id": "__listing__",
                    "error_type": "listing_inactive",
                    "error_message": "Listing appears inactive or unavailable on the ATS page.",
                }
            ],
        }
    )
    submitter = Submitter(
        driver_factory=lambda **kwargs: _FakeDriver(**kwargs),
        strategy_registry={"greenhouse": strategy},
    )
    result = asyncio.run(
        submitter.run_submission(
            listing=_listing(),
            fill_plan=_fill_plan(),
            submission_mode="live",
            capture_screenshot=False,
        )
    )
    assert result["status"] == "FAILED"
    assert result["failure_record"]["error_type"] == "ListingInactive"
    assert "inactive" in result["failure_record"]["error_message"].lower()
    assert result["human_escalations"][0]["field_id"] == "__listing__"


def test_submitter_resolves_lever_strategy_by_ats_type():
    submitter = Submitter(
        driver_factory=lambda **kwargs: _FakeDriver(**kwargs),
        strategy_registry={"lever": _FakeStrategy()},
    )
    result = asyncio.run(
        submitter.run_submission(
            listing=_listing(ats_type="lever"),
            fill_plan=_fill_plan(),
            submission_mode="shadow",
            capture_screenshot=False,
        )
    )
    assert result["status"] == "SHADOW_REVIEW"
    assert result["ats_type"] == "lever"


def test_submitter_screenshot_failure_is_non_blocking_for_live_submission():
    class _ScreenshotFailDriver(_FakeDriver):
        async def screenshot(self, path: str, full_page: bool = True):
            raise RuntimeError("screenshot permission denied")

    strategy = _FakeStrategy(
        execute_result={
            "status": "success",
            "submitted": True,
            "planned_actions": 1,
            "executed_count": 1,
            "executed_actions": [
                {
                    "field_id": "first_name",
                    "action": "fill_text",
                    "selector": "#first_name",
                    "value": "Jane",
                    "confidence": 0.98,
                    "source": "template",
                    "result": {"status": "filled"},
                }
            ],
            "failures": [],
        }
    )
    submitter = Submitter(
        driver_factory=lambda **kwargs: _ScreenshotFailDriver(**kwargs),
        strategy_registry={"greenhouse": strategy},
    )
    result = asyncio.run(
        submitter.run_submission(
            listing=_listing(),
            fill_plan=_fill_plan(),
            submission_mode="live",
            capture_screenshot=True,
        )
    )
    assert result["status"] == "SUBMITTED"
    assert any(item.get("field_id") == "__screenshot__" for item in result["execution_failures"])
    assert any(item.get("priority") == "IMPORTANT" for item in result["human_escalations"])


def test_submitter_notimplemented_error_gets_actionable_message():
    class _LoopFailDriver(_FakeDriver):
        async def start(self):
            raise NotImplementedError()

    submitter = Submitter(
        driver_factory=lambda **kwargs: _LoopFailDriver(**kwargs),
        strategy_registry={"greenhouse": _FakeStrategy()},
    )

    result = asyncio.run(
        submitter.run_submission(
            listing=_listing(),
            fill_plan=_fill_plan(),
            submission_mode="live",
            capture_screenshot=False,
        )
    )

    assert result["status"] == "FAILED"
    assert "without --reload" in result["failure_record"]["error_message"]
    assert "without --reload" in result["human_escalations"][0]["message"]


def test_check_submission_rate_limit_uses_persisted_outcomes_history():
    db_path = f"data/test_outcomes_{uuid4().hex}.db"
    init_outcomes_db(db_path)
    now = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO applications (
                application_id, listing_id, company, role_title, ats_type, fit_score,
                alive_score, status, resume_version, cover_letter_ver, time_to_apply_s,
                human_interventions, submitted_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "app-1",
                "listing-1",
                "Acme",
                "Backend Engineer",
                "greenhouse",
                None,
                None,
                "SUBMITTED",
                None,
                None,
                None,
                0,
                now,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO status_history (application_id, status, timestamp)
            VALUES (?, ?, ?)
            """,
            ("app-1", "SUBMITTED", now),
        )

    status = check_submission_rate_limit(
        ats_type="greenhouse",
        humanizer_config=HumanizerConfig(daily_cap=10, per_ats_limit=1, per_ats_window_seconds=3600),
        outcomes_db_path=db_path,
    )
    assert status.allowed is False
    assert status.reason == "per_ats_cooldown"


def test_build_fields_filled_includes_select_option_actions():
    fill_plan = {
        "fields": [
            {"field_id": "country", "label": "Country", "selector_strategy": "template"},
            {"field_id": "first_name", "label": "First Name", "selector_strategy": "template"},
        ]
    }
    executed_actions = [
        {
            "field_id": "country",
            "action": "select_option",
            "selector": "#country",
            "value": "United States",
            "confidence": 0.9,
            "result": {"status": "selected", "selected_text": "United States"},
        },
        {
            "field_id": "first_name",
            "action": "fill_text",
            "selector": "#first_name",
            "value": "Brendan",
            "confidence": 0.95,
            "result": {"status": "filled"},
        },
    ]

    rows = _build_fields_filled(fill_plan, executed_actions)
    ids = [row["field_id"] for row in rows]
    assert "country" in ids
    assert "first_name" in ids
