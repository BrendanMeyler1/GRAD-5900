"""Tests for browser.playwright_driver."""

from __future__ import annotations

import asyncio

import pytest

from browser.playwright_driver import PlaywrightDriver
from errors import ATSFormError


class _FakePage:
    def __init__(self) -> None:
        self.url = "https://boards.greenhouse.io/acme/jobs/123#app"
        self.fill_calls: list[tuple[str, str]] = []
        self.upload_calls: list[tuple[str, str]] = []
        self.click_calls: list[str] = []
        self.screenshot_calls: list[str] = []
        self.default_timeout = None

    def set_default_timeout(self, timeout_ms: int) -> None:
        self.default_timeout = timeout_ms

    async def goto(self, url: str, wait_until: str = "domcontentloaded") -> None:
        self.url = url

    async def title(self) -> str:
        return "Acme Job"

    async def evaluate(self, script: str, selector: str):
        if "outerHTML" in script:
            return "<form><input id='first_name'/></form>"
        return [
            {
                "index": 1,
                "tag": "input",
                "input_type": "text",
                "id_attr": "first_name",
                "name_attr": "first_name",
                "label": "First Name",
                "selector": "#first_name",
            }
        ]

    async def fill(self, selector: str, value: str) -> None:
        self.fill_calls.append((selector, value))

    async def set_input_files(self, selector: str, file_path: str) -> None:
        self.upload_calls.append((selector, file_path))

    async def click(self, selector: str) -> None:
        self.click_calls.append(selector)

    async def screenshot(self, path: str, full_page: bool = True) -> None:
        self.screenshot_calls.append(path)

    def locator(self, selector: str):
        return _FakeLocator(selector)


class _FakeLocator:
    def __init__(self, selector: str) -> None:
        self.selector = selector
        self.first = self

    async def evaluate(self, script: str, timeout: int | None = None):
        return False


def _driver_with_fake_page(fake_page: _FakePage) -> PlaywrightDriver:
    driver = PlaywrightDriver()
    driver._started = True  # noqa: SLF001
    driver.page = fake_page
    return driver


def test_get_dom_snapshot():
    driver = _driver_with_fake_page(_FakePage())
    snapshot = asyncio.run(driver.get_dom_snapshot())
    assert snapshot["title"] == "Acme Job"
    assert snapshot["fields"][0]["selector"] == "#first_name"


def test_fill_upload_click_and_screenshot():
    fake = _FakePage()
    driver = _driver_with_fake_page(fake)

    fill_result = asyncio.run(driver.fill_field("#first_name", "Jane"))
    upload_result = asyncio.run(driver.upload_file("input[type=file]", "resume.pdf"))
    click_result = asyncio.run(driver.click("button[type=submit]"))
    screenshot_result = asyncio.run(driver.screenshot(".tmp/shot.png"))

    assert fill_result["status"] == "filled"
    assert upload_result["status"] == "uploaded"
    assert click_result["status"] == "clicked"
    assert screenshot_result["status"] == "saved"

    assert fake.fill_calls[0] == ("#first_name", "Jane")
    assert fake.upload_calls
    assert fake.click_calls == ["button[type=submit]"]
    assert fake.screenshot_calls


def test_fill_field_wraps_errors_as_ats_form_error():
    class _BrokenPage(_FakePage):
        async def fill(self, selector: str, value: str) -> None:
            raise RuntimeError("selector not found")

    driver = _driver_with_fake_page(_BrokenPage())
    with pytest.raises(ATSFormError):
        asyncio.run(driver.fill_field("#missing", "x"))


def test_prepare_fill_value_normalizes_phone_digits_for_masked_inputs():
    assert PlaywrightDriver._prepare_fill_value("#phone", "+1 (914) 844-6887") == "9148446887"
    assert PlaywrightDriver._prepare_fill_value("input[name='phone']", "8 (914) 844-68-87") == "9148446887"
    assert PlaywrightDriver._prepare_fill_value("#first_name", "Jane") == "Jane"


def test_is_us_country_value_detects_us_aliases():
    assert PlaywrightDriver._is_us_country_value("US") is True
    assert PlaywrightDriver._is_us_country_value("United States") is True
    assert PlaywrightDriver._is_us_country_value("United States of America") is True
    assert PlaywrightDriver._is_us_country_value("United Kingdom") is False
