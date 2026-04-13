"""Tests for browser.ats_strategies.greenhouse."""

from __future__ import annotations

import asyncio

from browser.ats_strategies.greenhouse import GreenhouseStrategy


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


class _MissingSelectDriver(_FakeDriver):
    async def selector_exists(self, selector: str, timeout_ms: int = 1200):
        return selector != "#country"

    async def select_option(self, selector: str, value: str):
        return {"status": "selected", "selector": selector, "value": value}


def _fill_plan() -> dict:
    return {
        "ats_type": "greenhouse",
        "fields": [
            {
                "field_id": "first_name",
                "type": "text_input",
                "selector": "#first_name",
                "value": "Jane",
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
    strategy = GreenhouseStrategy()
    actions, failures = strategy.plan_actions(_fill_plan(), artifact_paths={"resume": "resume_final.pdf"})
    assert failures[0]["error_type"] == "missing_selector"
    assert actions[0]["action"] == "fill_text"
    assert actions[1]["action"] == "upload_file"


def test_plan_actions_prioritizes_country_before_phone():
    strategy = GreenhouseStrategy()
    plan = {
        "ats_type": "greenhouse",
        "fields": [
            {
                "field_id": "phone",
                "label": "Phone",
                "type": "text_input",
                "selector": "#phone",
                "value": "9148446887",
            },
            {
                "field_id": "country",
                "label": "Country",
                "type": "select",
                "selector": "#country",
                "value": "United States",
            },
        ],
    }
    actions, failures = strategy.plan_actions(plan, artifact_paths={})
    assert failures == []
    assert actions[0]["field_id"] == "country"
    assert actions[1]["field_id"] == "phone"


def test_execute_fill_plan_runs_actions_and_collects_failures():
    strategy = GreenhouseStrategy()
    driver = _FakeDriver()
    result = asyncio.run(
        strategy.execute_fill_plan(
            driver=driver,
            fill_plan=_fill_plan(),
            artifact_paths={"resume": "resume_final.pdf"},
            submit=True,
        )
    )
    assert result["ats_type"] == "greenhouse"
    assert result["planned_actions"] == 2
    assert result["executed_count"] == 2
    assert result["submitted"] is False
    assert result["status"] == "partial"
    assert any(item["error_type"] == "missing_selector" for item in result["failures"])
    assert driver.fill_calls[0] == ("#first_name", "Jane")
    assert driver.upload_calls[0][0] == "input[name='resume']"


def test_execute_fill_plan_fails_fast_when_form_not_detected():
    class _NoSelectorDriver(_FakeDriver):
        async def selector_exists(self, selector: str, timeout_ms: int = 1200):
            return False

    strategy = GreenhouseStrategy()
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
                "url": "https://boards.greenhouse.io/acme/jobs/123#app",
                "title": "Page not found",
                "text_excerpt": "The job board you were viewing is no longer active.",
            }

    strategy = GreenhouseStrategy()
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


def test_build_dynamic_variants_prefers_consent_synonyms():
    variants = GreenhouseStrategy._build_dynamic_variants(
        label="Do you consent to processing under the privacy policy?",
        answer="Yes",
    )
    lowered = {item.lower() for item in variants}
    assert "i consent" in lowered
    assert "agree" in lowered


def test_fallback_answer_for_label_uses_fill_plan_values():
    answer_country = GreenhouseStrategy._fallback_answer_for_label(
        label="Country",
        fallback_answers={"country": "United States"},
    )
    answer_location = GreenhouseStrategy._fallback_answer_for_label(
        label="Location (City)",
        fallback_answers={"candidate_location": "Ridgefield, Connecticut"},
    )
    assert answer_country == "United States"
    assert answer_location == "Ridgefield, Connecticut"


def test_plan_actions_marks_cover_letter_upload_optional_by_default():
    strategy = GreenhouseStrategy()
    plan = {
        "ats_type": "greenhouse",
        "fields": [
            {
                "field_id": "cover_letter_upload",
                "label": "Cover Letter",
                "type": "file_upload",
                "selector": "input[type='file'][name='cover_letter']",
                "value": "generated_cover_letter.pdf",
            }
        ],
    }
    actions, failures = strategy.plan_actions(plan, artifact_paths={"cover_letter": "cover_letter.pdf"})
    assert failures == []
    assert len(actions) == 1
    assert actions[0]["action"] == "upload_file"
    assert actions[0]["required"] is False


def test_canonical_dynamic_field_id_maps_common_greenhouse_labels():
    assert GreenhouseStrategy._canonical_dynamic_field_id("Country", "foo") == "country"
    assert (
        GreenhouseStrategy._canonical_dynamic_field_id("Location (City)", "candidate-location")
        == "candidate_location"
    )
    assert GreenhouseStrategy._canonical_dynamic_field_id("Phone", "tel") == "phone"


def test_execute_fill_plan_defers_missing_template_select_and_allows_dynamic_recovery():
    class _Strategy(GreenhouseStrategy):
        async def _discover_and_fill_dynamic_fields(self, driver, already_filled, fallback_answers=None):
            return {
                "filled": [
                    {
                        "field_id": "country",
                        "action": "select_option",
                        "selector": "input[name='country_dynamic']",
                        "value": "United States",
                        "result": {"status": "selected"},
                    }
                ]
            }

        async def _collect_unfilled_required_fields(self, driver):
            return []

    strategy = _Strategy()
    driver = _MissingSelectDriver()
    plan = {
        "ats_type": "greenhouse",
        "fields": [
            {
                "field_id": "first_name",
                "label": "First Name",
                "type": "text_input",
                "selector": "#first_name",
                "value": "Jane",
            },
            {
                "field_id": "country",
                "label": "Country",
                "type": "select",
                "selector": "#country",
                "value": "United States",
            }
        ],
    }

    result = asyncio.run(strategy.execute_fill_plan(driver=driver, fill_plan=plan, submit=False))

    assert result["status"] == "success"
    assert result["failures"] == []
    assert any(item.get("field_id") == "country" for item in result["executed_actions"])


def test_submit_selector_candidates_include_fallbacks_and_are_deduped():
    selectors = GreenhouseStrategy._submit_selector_candidates("button[type='submit']")
    assert selectors[0] == "button[type='submit']"
    assert "button:has-text('Submit')" in selectors
    assert "input[type='submit']" in selectors
    assert len(selectors) == len(set(selectors))


def test_execute_fill_plan_retries_required_upload_when_not_attached():
    class _UploadRecoveryStrategy(GreenhouseStrategy):
        async def _discover_and_fill_dynamic_fields(self, driver, already_filled, fallback_answers=None):
            return {"filled": []}

        async def _collect_unfilled_required_fields(self, driver):
            return []

        async def _has_uploaded_file(self, driver, upload_kind):
            # First upload attempt appears missing; recovery attempt succeeds.
            return len(getattr(driver, "upload_calls", [])) >= 2

    strategy = _UploadRecoveryStrategy()
    driver = _FakeDriver()
    plan = {
        "ats_type": "greenhouse",
        "fields": [
            {
                "field_id": "first_name",
                "label": "First Name",
                "type": "text_input",
                "selector": "#first_name",
                "value": "Jane",
            },
            {
                "field_id": "resume_upload",
                "label": "Resume",
                "type": "file_upload",
                "selector": "input#resume[type='file']",
                "value": "resume_final.pdf",
            },
        ],
    }

    result = asyncio.run(strategy.execute_fill_plan(driver=driver, fill_plan=plan, submit=False))

    assert result["status"] == "success"
    assert result["failures"] == []
    assert len(driver.upload_calls) == 2
    assert driver.upload_calls[1][0] == "input[type='file']"
