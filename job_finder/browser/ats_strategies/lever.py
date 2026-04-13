"""
Lever-specific fill strategy.

Consumes Form Interpreter fill plans and executes them through PlaywrightDriver.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from browser.playwright_driver import PlaywrightDriver


class LeverStrategy:
    """Execution strategy for Lever ATS forms."""

    ATS_TYPE = "lever"

    @classmethod
    def supports(cls, ats_type: str | None) -> bool:
        value = (ats_type or "").strip().lower()
        return value in {cls.ATS_TYPE, "lever.co", "jobs.lever.co"}

    @staticmethod
    def _resolve_upload_path(
        field: dict[str, Any],
        artifact_paths: dict[str, str] | None = None,
    ) -> str:
        artifact_paths = artifact_paths or {}
        field_id = str(field.get("field_id") or "").lower()
        label = str(field.get("label") or "").strip().lower()

        if field_id and field_id in artifact_paths:
            return artifact_paths[field_id]

        if "resume" in label or "resume" in field_id or "cv" in label or "cv" in field_id:
            if "resume" in artifact_paths:
                return artifact_paths["resume"]
            elif "resume_upload" in artifact_paths:
                return artifact_paths["resume_upload"]
                
        if "cover" in label or "cover" in field_id or "letter" in label:
            if "cover_letter" in artifact_paths:
                return artifact_paths["cover_letter"]
            elif "cover_letter_upload" in artifact_paths:
                return artifact_paths["cover_letter_upload"]

        value = field.get("value")
        if isinstance(value, str) and value.strip():
            path_obj = Path(value.strip())
            if path_obj.exists() and path_obj.is_file():
                return value.strip()
                
        return ""

    def plan_actions(
        self,
        fill_plan: dict[str, Any],
        artifact_paths: dict[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """
        Convert fill plan into executable actions.

        Returns:
            actions, plan_failures
        """
        fields = list(fill_plan.get("fields", []))
        actions: list[dict[str, Any]] = []
        plan_failures: list[dict[str, Any]] = []

        text_actions: list[dict[str, Any]] = []
        upload_actions: list[dict[str, Any]] = []

        for field in fields:
            if not isinstance(field, dict):
                continue
            field_id = str(field.get("field_id") or "")
            selector = field.get("selector")
            field_type = str(field.get("type") or "").strip().lower()

            if not selector:
                plan_failures.append(
                    {
                        "field_id": field_id,
                        "error_type": "missing_selector",
                        "error_message": "No selector available for field.",
                    }
                )
                continue

            if field_type == "file_upload":
                file_path = self._resolve_upload_path(field, artifact_paths=artifact_paths)
                if not file_path:
                    plan_failures.append(
                        {
                            "field_id": field_id,
                            "error_type": "missing_file_path",
                            "error_message": "No upload file path resolved for file_upload field.",
                        }
                    )
                    continue
                upload_actions.append(
                    {
                        "field_id": field_id,
                        "action": "upload_file",
                        "selector": str(selector),
                        "value": file_path,
                        "confidence": field.get("confidence"),
                        "source": field.get("source"),
                    }
                )
                continue

            if field_type in {"checkbox", "radio"}:
                text_actions.append(
                    {
                        "field_id": field_id,
                        "action": "click",
                        "selector": str(selector),
                        "value": field.get("value"),
                        "confidence": field.get("confidence"),
                        "source": field.get("source"),
                    }
                )
                continue

            text_actions.append(
                {
                    "field_id": field_id,
                    "action": "fill_text",
                    "selector": str(selector),
                    "value": "" if field.get("value") is None else str(field.get("value")),
                    "confidence": field.get("confidence"),
                    "source": field.get("source"),
                }
            )

        actions.extend(text_actions)
        actions.extend(upload_actions)
        return actions, plan_failures

    @staticmethod
    def _classify_page_mismatch(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
        payload = metadata or {}
        title = str(payload.get("title") or "")
        url = str(payload.get("url") or "")
        text = str(payload.get("text_excerpt") or "")
        haystack = f"{title}\n{text}".lower()

        inactive_markers = (
            "no longer active",
            "no longer available",
            "has been filled",
            "position has been filled",
            "job is no longer accepting applications",
            "this job is no longer available",
        )
        if any(marker in haystack for marker in inactive_markers):
            return {
                "field_id": "__listing__",
                "error_type": "listing_inactive",
                "error_message": (
                    "Listing appears inactive or unavailable on the ATS page. "
                    "Verify the posting is still live and the apply URL is current."
                ),
                "detected_url": url,
                "page_title": title,
            }
        return None

    @staticmethod
    async def _preflight_selectors(
        driver: PlaywrightDriver,
        actions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Fail fast when none of the planned selectors are present on page.

        This avoids expensive per-field timeouts when the browser lands on an
        unexpected page (login wall, expired URL, wrong template, etc.).
        """
        if not hasattr(driver, "selector_exists"):
            return []

        selectors_checked: list[str] = []
        for action in actions:
            selector = str(action.get("selector") or "").strip()
            if not selector or selector in selectors_checked:
                continue
            selectors_checked.append(selector)
            if len(selectors_checked) >= 5:
                break

        if not selectors_checked:
            return []

        found_any = False
        for selector in selectors_checked:
            try:
                exists = await driver.selector_exists(selector=selector, timeout_ms=1_200)
            except TypeError:
                exists = await driver.selector_exists(selector)
            if exists:
                found_any = True
                break

        if found_any:
            return []

        page_metadata: dict[str, Any] | None = None
        if hasattr(driver, "get_page_metadata"):
            try:
                page_metadata = await driver.get_page_metadata(max_text_chars=2_000)
            except TypeError:
                page_metadata = await driver.get_page_metadata()
            except Exception:
                page_metadata = None

        classified = LeverStrategy._classify_page_mismatch(page_metadata)
        if classified:
            classified["selectors_checked"] = selectors_checked
            return [classified]

        return [
            {
                "field_id": "__page__",
                "error_type": "form_not_detected",
                "error_message": (
                    "None of the planned selectors were found on the loaded page. "
                    "Check apply URL, login/account gate, or ATS template mismatch."
                ),
                "selectors_checked": selectors_checked,
            }
        ]

    async def execute_fill_plan(
        self,
        driver: PlaywrightDriver,
        fill_plan: dict[str, Any],
        artifact_paths: dict[str, str] | None = None,
        submit: bool = False,
        submit_selector: str = "button[type='submit']",
    ) -> dict[str, Any]:
        """Execute a Lever fill plan through the Playwright driver."""
        actions, failures = self.plan_actions(fill_plan=fill_plan, artifact_paths=artifact_paths)
        failures.extend(await self._preflight_selectors(driver=driver, actions=actions))

        preflight_hard_failures = {"form_not_detected", "listing_inactive"}
        if any(str(item.get("error_type") or "") in preflight_hard_failures for item in failures):
            return {
                "ats_type": self.ATS_TYPE,
                "status": "partial",
                "submitted": False,
                "planned_actions": len(actions),
                "executed_count": 0,
                "executed_actions": [],
                "failures": failures,
                "executed_at": datetime.now(timezone.utc).isoformat(),
                "trace_hint": f"replay/traces/{Path(self.ATS_TYPE).name}/",
            }

        executed_actions: list[dict[str, Any]] = []
        for action in actions:
            try:
                action_type = action["action"]
                selector = action["selector"]
                value = action.get("value")

                if action_type == "fill_text":
                    result = await driver.fill_field(selector=selector, value=value)
                elif action_type == "upload_file":
                    result = await driver.upload_file(selector=selector, file_path=str(value))
                elif action_type == "click":
                    result = await driver.click(selector=selector)
                else:
                    raise ValueError(f"Unsupported action type: {action_type}")

                executed_actions.append({**action, "result": result})
            except Exception as exc:
                failures.append(
                    {
                        "field_id": action.get("field_id"),
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                        "action": action,
                    }
                )

        submitted = False
        if submit and not failures:
            try:
                await driver.click(selector=submit_selector)
                submitted = True
            except Exception as exc:
                failures.append(
                    {
                        "field_id": "__submit__",
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                        "action": {
                            "action": "click",
                            "selector": submit_selector,
                        },
                    }
                )

        status = "success" if not failures else "partial"
        return {
            "ats_type": self.ATS_TYPE,
            "status": status,
            "submitted": submitted,
            "planned_actions": len(actions),
            "executed_count": len(executed_actions),
            "executed_actions": executed_actions,
            "failures": failures,
            "executed_at": datetime.now(timezone.utc).isoformat(),
            "trace_hint": f"replay/traces/{Path(self.ATS_TYPE).name}/",
        }
