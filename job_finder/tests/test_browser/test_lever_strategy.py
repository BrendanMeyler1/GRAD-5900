"""Tests for browser.ats_strategies.lever."""

from __future__ import annotations

import asyncio

from browser.ats_strategies.lever import LeverStrategy


class _FakeDriver:
    def __init__(self) -> None:
        self.fill_calls: list[tuple[str, str]] = []
        self.upload_calls: list[tuple[str, str]] = []
        self.click_calls: list[str] = []

    async def fill_field(self, selector: str, value: str):
        self.fill_calls.append((selector, value))
        return {"status": "filled"}

    async def upload_file(self, selector: str, file_path: str):
        self.upload_calls.append((selector, file_path))
        return {"status": "uploaded"}

    async def click(self, selector: str):
        self.click_calls.append(selector)
        return {"status": "clicked"}


def _fill_plan() -> dict:
    return {
        "ats_type": "lever",
        "fields": [
            {
                "field_id": "name",
                "type": "text_input",
                "selector": "input[name='name']",
                "value": "Jane TestPerson",
                "confidence": 0.98,
                "source": "template",
            },
            {
                "field_id": "resume_upload",
                "type": "file_upload",
                "selector": "input[name='resume']",
                "value": "generated_resume.pdf",
                "confidence": 0.95,
                "source": "template",
            },
            {
                "field_id": "missing_selector_field",
                "type": "text_input",
                "selector": None,
                "value": "x",
            },
        ],
    }


def test_plan_actions_orders_text_before_uploads():
    strategy = LeverStrategy()
    actions, failures = strategy.plan_actions(_fill_plan(), artifact_paths={"resume": "resume_final.pdf"})
    assert failures[0]["error_type"] == "missing_selector"
    assert actions[0]["action"] == "fill_text"
    assert actions[1]["action"] == "upload_file"


def test_execute_fill_plan_runs_actions_and_collects_failures():
    strategy = LeverStrategy()
    driver = _FakeDriver()
    result = asyncio.run(
        strategy.execute_fill_plan(
            driver=driver,
            fill_plan=_fill_plan(),
            artifact_paths={"resume": "resume_final.pdf"},
            submit=True,
        )
    )
    assert result["ats_type"] == "lever"
    assert result["planned_actions"] == 2
    assert result["executed_count"] == 2
    assert result["submitted"] is False
    assert result["status"] == "partial"
    assert any(item["error_type"] == "missing_selector" for item in result["failures"])
    assert driver.fill_calls[0] == ("input[name='name']", "Jane TestPerson")
    assert driver.upload_calls[0][0] == "input[name='resume']"


def test_execute_fill_plan_fails_fast_when_form_not_detected():
    class _NoSelectorDriver(_FakeDriver):
        async def selector_exists(self, selector: str, timeout_ms: int = 1200):
            return False

    strategy = LeverStrategy()
    driver = _NoSelectorDriver()
    result = asyncio.run(
        strategy.execute_fill_plan(
            driver=driver,
            fill_plan=_fill_plan(),
            artifact_paths={"resume": "resume_final.pdf"},
            submit=True,
        )
    )
    assert result["status"] == "partial"
    assert result["executed_count"] == 0
    assert any(item["error_type"] == "form_not_detected" for item in result["failures"])


def test_execute_fill_plan_classifies_inactive_listing_page():
    class _InactivePageDriver(_FakeDriver):
        async def selector_exists(self, selector: str, timeout_ms: int = 1200):
            return False

        async def get_page_metadata(self, max_text_chars: int = 2000):
            return {
                "url": "https://jobs.lever.co/acme/backend",
                "title": "Job not available",
                "text_excerpt": "This job is no longer available.",
            }

    strategy = LeverStrategy()
    driver = _InactivePageDriver()
    result = asyncio.run(
        strategy.execute_fill_plan(
            driver=driver,
            fill_plan=_fill_plan(),
            artifact_paths={"resume": "resume_final.pdf"},
            submit=True,
        )
    )
    assert result["status"] == "partial"
    assert result["executed_count"] == 0
    assert any(item["error_type"] == "listing_inactive" for item in result["failures"])
