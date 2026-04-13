"""
Universal ATS fill strategy.

Catch-all strategy for ATS platforms not handled by specialized strategies
(Greenhouse, Lever), such as Ashby, iCIMS, SmartRecruiters, Workday, etc.

Works by:
1. Using Claude-interpreted fill plans from live DOM snapshots (A3 fix produces these)
2. Falling back to label-based field matching when template selectors are absent
3. Using the same file upload and submit logic as GreenhouseStrategy (reused)
"""

from __future__ import annotations

import logging
from typing import Any

from browser.playwright_driver import PlaywrightDriver

logger = logging.getLogger("job_finder.browser.ats_strategies.universal")


class UniversalStrategy:
    """
    Generalized fill strategy for any ATS.

    Designed to handle sites like Ashby, iCIMS, SmartRecruiters, and any
    other ATS where a specialized strategy doesn't exist. Falls back to
    GreenhouseStrategy's proven execution logic under the hood.
    """

    ATS_TYPE = "universal"

    @classmethod
    def supports(cls, ats_type: str | None) -> bool:
        """Supports any ATS type — acts as a catch-all."""
        return True

    def plan_actions(
        self,
        fill_plan: dict[str, Any],
        artifact_paths: dict[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Delegate to GreenhouseStrategy's plan_actions — same field logic works universally."""
        from browser.ats_strategies.greenhouse import GreenhouseStrategy
        return GreenhouseStrategy().plan_actions(fill_plan=fill_plan, artifact_paths=artifact_paths)

    async def execute_fill_plan(
        self,
        driver: PlaywrightDriver,
        fill_plan: dict[str, Any],
        artifact_paths: dict[str, str] | None = None,
        submit: bool = False,
        submit_selector: str = "button[type='submit']",
    ) -> dict[str, Any]:
        """
        Execute a fill plan universally using GreenhouseStrategy's battle-tested execution.

        The specialized Greenhouse logic (react-select handling, file upload heuristics,
        submit-click fallback, post-submit validation) works well across most ATS platforms.
        We reuse it verbatim rather than duplicating it here.
        """
        from browser.ats_strategies.greenhouse import GreenhouseStrategy

        logger.info(
            "UniversalStrategy: executing fill plan with %d fields (ats=%s)",
            len(fill_plan.get("fields", [])),
            fill_plan.get("ats_type", "unknown"),
        )
        return await GreenhouseStrategy().execute_fill_plan(
            driver=driver,
            fill_plan=fill_plan,
            artifact_paths=artifact_paths,
            submit=submit,
            submit_selector=submit_selector,
        )
