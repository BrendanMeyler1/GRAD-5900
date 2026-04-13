"""
Workday ATS Strategy.

Workday is incredibly brittle and dynamic. It relies heavily on shadow DOM and 
non-standard dynamic component IDs. This strategy overrides standard static identifiers 
with semantic layout fallbacks to navigate Workday's SPA flows.
"""

from __future__ import annotations

import logging
from typing import Any

from playwright.async_api import Page, ElementHandle

from browser.ats_strategies.universal import UniversalStrategy
from browser.filler import FillerConfig

logger = logging.getLogger("job_finder.browser.ats_strategies.workday")


class WorkdayStrategy(UniversalStrategy):
    """
    Workday-specific overrides for navigating and filling applications.
    Workday uses highly nested components, so we rely heavily on generic 
    aria-labels and placeholder structures over hard-coded IDs.
    """

    @classmethod
    def supports(cls, url: str) -> bool:
        return "myworkdaysite.com" in url.lower() or "workday" in url.lower()

    async def _preflight_check(self, page: Page) -> None:
        """Verify we are on a Workday careers page."""
        try:
            await page.wait_for_selector(
                "div[data-automation-id='jobPostingHeader']", timeout=10000
            )
        except Exception:
            logger.info("Custom Workday careers structure detected. Attempting to proceed without static preflight.")

    async def fill_field(
        self,
        page: Page,
        field: dict[str, Any],
        config: FillerConfig,
        dom_snapshot: dict[str, Any] | None = None,
        trace_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Wrap the generic fill_field to handle workday specific component layers."""
        
        # Workday specifically hides elements behind custom divs. We try to augment
        # the selector to look for inputs nested under labels with the given text.
        field_label = str(field.get("label") or "").strip()
        field_type = str(field.get("type") or "text_input").strip()
        selector = field.get("selector")
        
        # If the selector is generic or missing, try semantic resolution first
        if not selector and field_label:
            logger.debug(f"Attempting semantic resolution for Workday field: {field_label}")
            if field_type == "text_input":
                selector = f"//label[contains(text(), '{field_label}')]/following::input[1]"
            elif field_type == "checkbox":
                selector = f"//label[contains(text(), '{field_label}')]/input[@type='checkbox']"
                
            field["selector"] = selector
            field["selector_strategy"] = "workday_semantic_override"

        # Call the parent execute method
        return await super().fill_field(page, field, config, dom_snapshot, trace_context)
