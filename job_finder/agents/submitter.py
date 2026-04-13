"""
Submission pipeline agent (Phase 2.5).

Runs ATS form execution in dry_run/shadow/live modes using:
- browser.playwright_driver.PlaywrightDriver
- browser.humanizer.Humanizer
- browser.ats_strategies.greenhouse.GreenhouseStrategy
- browser.ats_strategies.lever.LeverStrategy
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from browser.ats_strategies.greenhouse import GreenhouseStrategy
from browser.ats_strategies.lever import LeverStrategy
from browser.ats_strategies.universal import UniversalStrategy
from browser.humanizer import Humanizer, HumanizerConfig, RateLimitStatus
from browser.playwright_driver import PlaywrightDriver
from errors import ATSFormError

logger = logging.getLogger("job_finder.agents.submitter")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTCOMES_DB_PATH = str(PROJECT_ROOT / "data" / "outcomes.db")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_rate_limit_dict(status: RateLimitStatus | None) -> dict[str, Any] | None:
    if status is None:
        return None
    return {
        "allowed": status.allowed,
        "reason": status.reason,
        "retry_after_seconds": status.retry_after_seconds,
        "daily_used": status.daily_used,
        "daily_remaining": status.daily_remaining,
        "ats_used": status.ats_used,
        "ats_remaining": status.ats_remaining,
    }


def _parse_iso_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _load_submission_events_from_outcomes(
    db_path: str,
    *,
    now: datetime,
    lookback_seconds: int,
) -> list[tuple[str, datetime]]:
    """
    Load historical SUBMITTED events from outcomes.db for rate-limit checks.
    """
    cutoff = (now - timedelta(seconds=max(lookback_seconds, 1))).isoformat()
    rows: list[tuple[str, str]] = []
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                """
                SELECT COALESCE(a.ats_type, 'unknown') AS ats_type, sh.timestamp
                FROM status_history sh
                JOIN applications a ON a.application_id = sh.application_id
                WHERE UPPER(sh.status) = 'SUBMITTED' AND sh.timestamp >= ?
                ORDER BY sh.timestamp ASC
                """,
                (cutoff,),
            ).fetchall()
    except sqlite3.Error:
        logger.warning("Could not read submission history from %s", db_path, exc_info=True)
        return []

    events: list[tuple[str, datetime]] = []
    for ats_type, timestamp in rows:
        parsed = _parse_iso_datetime(timestamp)
        if parsed is None:
            continue
        events.append((str(ats_type or "unknown"), parsed))
    return events


def check_submission_rate_limit(
    *,
    ats_type: str,
    humanizer_config: HumanizerConfig | None = None,
    outcomes_db_path: str = DEFAULT_OUTCOMES_DB_PATH,
) -> RateLimitStatus:
    """
    Check current submission limits using persisted outcomes history.

    This keeps limits effective across API/server restarts.
    """
    humanizer = Humanizer(config=humanizer_config) if humanizer_config else Humanizer()
    now = datetime.now(timezone.utc)
    lookback_seconds = max(24 * 3600, int(humanizer.config.per_ats_window_seconds))
    events = _load_submission_events_from_outcomes(
        outcomes_db_path,
        now=now,
        lookback_seconds=lookback_seconds,
    )
    humanizer.seed_submission_log(events)
    return humanizer.check_rate_limits(ats_type)


def _extract_url(listing: dict[str, Any] | None, override: str | None = None) -> str | None:
    if override:
        return override
    payload = listing or {}
    return payload.get("apply_url") or payload.get("source_url")


def _field_lookup(fill_plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for field in fill_plan.get("fields", []) or []:
        if not isinstance(field, dict):
            continue
        field_id = str(field.get("field_id", "")).strip()
        if field_id:
            lookup[field_id] = field
    return lookup


def _build_fields_filled(
    fill_plan: dict[str, Any],
    executed_actions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    lookup = _field_lookup(fill_plan)
    rows: list[dict[str, Any]] = []
    for action in executed_actions:
        action_type = str(action.get("action", "")).lower()
        if action_type not in {"fill_text", "select_option"}:
            continue
        field_id = str(action.get("field_id", ""))
        metadata = lookup.get(field_id, {})
        result_payload = action.get("result") if isinstance(action.get("result"), dict) else {}
        observed_value = result_payload.get("selected_text") or action.get("value")
        rows.append(
            {
                "field_id": field_id,
                "label": metadata.get("label"),
                "value": observed_value,
                "selector": action.get("selector"),
                "selector_strategy": metadata.get("selector_strategy"),
                "confidence": action.get("confidence"),
            }
        )
    return rows


def _failures_to_escalations(failures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    escalations: list[dict[str, Any]] = []
    for item in failures:
        field_id = item.get("field_id")
        reason = item.get("error_message") or item.get("error_type") or "Submission failure"
        priority = "IMPORTANT" if field_id == "__screenshot__" else "BLOCKING"
        escalations.append(
            {
                "type": "submitter",
                "field_id": None if field_id == "__submit__" else field_id,
                "priority": priority,
                "message": str(reason),
            }
        )
    return escalations


def _friendly_submitter_error(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return message
    if isinstance(exc, NotImplementedError):
        return (
            "Playwright could not start on the current asyncio event loop. "
            "On Windows, run the API without --reload and with Proactor event-loop policy."
        )
    return f"{type(exc).__name__} during browser submission."


def _failure_record_from_failures(failures: list[dict[str, Any]]) -> dict[str, Any]:
    listing_inactive = next(
        (
            item
            for item in failures
            if str(item.get("error_type", "")).lower() == "listing_inactive"
        ),
        None,
    )
    if isinstance(listing_inactive, dict):
        return {
            "error_type": "ListingInactive",
            "error_message": str(
                listing_inactive.get("error_message")
                or "Listing appears inactive or unavailable."
            ),
            "timestamp": _utc_now(),
        }

    return {
        "error_type": "SubmissionFailed",
        "error_message": "One or more submission actions failed.",
        "timestamp": _utc_now(),
    }


class Submitter:
    """Submission orchestrator across execution modes."""

    def __init__(
        self,
        driver_factory: Callable[..., PlaywrightDriver] | None = None,
        humanizer: Humanizer | None = None,
        strategy_registry: dict[str, Any] | None = None,
        screenshots_dir: str = "replay/traces/screenshots",
    ) -> None:
        self.humanizer = humanizer or Humanizer()
        self.driver_factory = driver_factory or (
            lambda **kwargs: PlaywrightDriver(**kwargs)
        )
        self.strategy_registry = strategy_registry or {
            "greenhouse": GreenhouseStrategy(),
            "lever": LeverStrategy(),
            # Universal catch-all for Ashby, iCIMS, SmartRecruiters, Workday, etc.
            "ashby": UniversalStrategy(),
            "workday": UniversalStrategy(),
            "icims": UniversalStrategy(),
            "smartrecruiters": UniversalStrategy(),
            "jobvite": UniversalStrategy(),
            "taleo": UniversalStrategy(),
            "unknown": UniversalStrategy(),
        }
        self.screenshots_dir = Path(screenshots_dir)
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_strategy(self, ats_type: str | None):
        normalized = str(ats_type or "unknown").strip().lower()
        strategy = self.strategy_registry.get(normalized)
        if strategy is None:
            # Fall back to UniversalStrategy rather than crashing on unknown ATS
            logger.warning(
                "No strategy registered for ATS '%s'. Using UniversalStrategy as catch-all.",
                normalized,
            )
            strategy = self.strategy_registry.get("unknown") or UniversalStrategy()
        return strategy, normalized

    def _new_driver(self, headless: bool) -> PlaywrightDriver:
        try:
            return self.driver_factory(headless=headless, humanizer=self.humanizer)
        except TypeError:
            # Test doubles or custom factories may not accept kwargs.
            driver = self.driver_factory()
            if getattr(driver, "humanizer", None) is None:
                setattr(driver, "humanizer", self.humanizer)
            return driver

    async def run_submission(
        self,
        listing: dict[str, Any],
        fill_plan: dict[str, Any],
        submission_mode: str = "shadow",
        artifact_paths: dict[str, str] | None = None,
        apply_url: str | None = None,
        submit_selector: str = "button[type='submit']",
        headless: bool = True,
        capture_screenshot: bool = True,
        allow_rate_limit_bypass: bool = False,
        visible_hold_seconds: float | None = None,
    ) -> dict[str, Any]:
        """
        Execute submission flow by mode:
        - dry_run: plan only (no browser)
        - shadow: fill + preview, no final submit click
        - live: fill + submit
        """
        started_at = time.perf_counter()
        listing = listing or {}
        fill_plan = fill_plan or {"fields": []}
        listing_id = str(listing.get("listing_id") or "unknown_listing")

        strategy, ats_type = self._resolve_strategy(listing.get("ats_type"))
        rate_status = None
        if submission_mode in {"shadow", "live"} and not allow_rate_limit_bypass:
            rate_status = self.humanizer.check_rate_limits(ats_type)
            if not rate_status.allowed:
                message = (
                    f"Submission blocked by rate limit: {rate_status.reason}. "
                    f"Retry after {rate_status.retry_after_seconds}s."
                )
                return {
                    "status": "FAILED",
                    "submission_mode": submission_mode,
                    "ats_type": ats_type,
                    "fields_filled": [],
                    "human_escalations": [
                        {
                            "type": "submitter",
                            "priority": "BLOCKING",
                            "message": message,
                        }
                    ],
                    "failure_record": {
                        "error_type": "RateLimitBlocked",
                        "error_message": message,
                        "timestamp": _utc_now(),
                    },
                    "rate_limit": _as_rate_limit_dict(rate_status),
                    "time_to_apply_seconds": int(time.perf_counter() - started_at),
                }

        if submission_mode == "dry_run":
            actions, failures = strategy.plan_actions(
                fill_plan=fill_plan,
                artifact_paths=artifact_paths,
            )
            return {
                "status": "DRY_RUN",
                "submission_mode": submission_mode,
                "ats_type": ats_type,
                "planned_actions": len(actions),
                "preview_actions": actions,
                "fields_filled": [],
                "human_escalations": _failures_to_escalations(failures),
                "execution_failures": failures,
                "rate_limit": _as_rate_limit_dict(rate_status),
                "time_to_apply_seconds": int(time.perf_counter() - started_at),
            }

        target_url = _extract_url(listing, override=apply_url)
        driver = self._new_driver(headless=headless)
        screenshot_path = None
        hold_seconds = 0.0
        if not headless and submission_mode in {"shadow", "live"}:
            raw_hold = (
                visible_hold_seconds
                if visible_hold_seconds is not None
                else os.getenv("BROWSER_VISIBLE_HOLD_SECONDS", "20")
            )
            try:
                hold_seconds = max(0.0, float(raw_hold))
            except (TypeError, ValueError):
                hold_seconds = 10.0

        try:
            await driver.start()
            nav_result: dict[str, Any] = {}
            if target_url:
                nav_result = await driver.goto(target_url)

            # --- A1/A2: Override ATS type from live page URL ---
            detected_ats = nav_result.get("detected_ats", "")
            if detected_ats and detected_ats != "unknown" and detected_ats != ats_type:
                logger.info(
                    "ATS type override: listing said '%s', live URL detected '%s'. Switching strategy.",
                    ats_type,
                    detected_ats,
                )
                try:
                    strategy, ats_type = self._resolve_strategy(detected_ats)
                except ATSFormError:
                    logger.warning(
                        "No strategy registered for detected ATS '%s'. Keeping original strategy '%s'.",
                        detected_ats,
                        ats_type,
                    )

            # --- A3: Capture live DOM and re-interpret fill plan with live HTML ---
            try:
                live_snapshot = await driver.get_dom_snapshot()
                live_form_html = live_snapshot.get("form_html") or ""
                if live_form_html and len(live_form_html) > 200:
                    logger.info(
                        "Captured live DOM snapshot (%d chars). Re-interpreting fill plan against live form.",
                        len(live_form_html),
                    )
                    from agents.form_interpreter import interpret_form
                    from agents.pii_injector import inject_pii_fill_plan
                    from llm_router.router import LLMRouter

                    live_listing = dict(listing)
                    live_listing["ats_type"] = ats_type  # Use corrected ATS type
                    live_listing["form_html"] = live_form_html

                    live_fill_plan = interpret_form(
                        listing=live_listing,
                        form_html=live_form_html,
                        persona=None,
                        allow_llm_assist=True,
                        router=LLMRouter(),
                    )

                    # Inject PII immediately — the workflow's inject_pii_node already ran on
                    # the OLD fill plan, so we must re-inject on the fresh live plan here.
                    try:
                        pii_result = inject_pii_fill_plan(live_fill_plan)
                        fill_plan = pii_result.get("fill_plan", live_fill_plan)
                        unresolved = pii_result.get("unresolved_fields", [])
                        if unresolved:
                            logger.warning(
                                "Live fill plan has %d unresolved PII fields: %s",
                                len(unresolved),
                                unresolved,
                            )
                    except Exception as pii_exc:
                        logger.warning("PII injection on live fill plan failed: %s. Using tokenized plan.", pii_exc)
                        fill_plan = live_fill_plan

                    logger.info(
                        "Live DOM form interpretation produced %d fields.",
                        len(fill_plan.get("fields", [])),
                    )
                    # Diagnostic: log each field for debugging
                    for f_idx, f in enumerate(fill_plan.get("fields", [])):
                        logger.info(
                            "  Field[%d] id=%s type=%s selector=%s value=%s",
                            f_idx,
                            f.get("field_id"),
                            f.get("type"),
                            f.get("selector", "<NONE>"),
                            str(f.get("value", ""))[:80],
                        )
                else:
                    logger.info("Live DOM snapshot was empty or too small; using existing fill plan.")

            except Exception as dom_exc:
                logger.warning("Live DOM re-interpretation failed, using existing fill plan: %s", dom_exc)

            # Log artifact_paths so we can verify files exist for upload actions
            logger.info("artifact_paths for submission: %s", artifact_paths)

            execution = await strategy.execute_fill_plan(
                driver=driver,
                fill_plan=fill_plan,
                artifact_paths=artifact_paths,
                submit=(submission_mode == "live"),
                submit_selector=submit_selector,
            )

            failures = list(execution.get("failures", []))
            fields_filled = _build_fields_filled(
                fill_plan=fill_plan,
                executed_actions=list(execution.get("executed_actions", [])),
            )

            if capture_screenshot:
                screenshot_name = f"{listing_id}_{submission_mode}_{int(time.time())}.png"
                screenshot_file = self.screenshots_dir / screenshot_name
                try:
                    shot = await driver.screenshot(path=str(screenshot_file), full_page=True)
                    screenshot_path = shot.get("path")
                except Exception as exc:
                    failures.append(
                        {
                            "field_id": "__screenshot__",
                            "error_type": type(exc).__name__,
                            "error_message": str(exc),
                        }
                    )

            blocking_failures = [
                item for item in failures if str(item.get("field_id")) != "__screenshot__"
            ]
            if submission_mode == "shadow":
                workflow_status = "SHADOW_REVIEW"
            else:
                workflow_status = (
                    "SUBMITTED" if execution.get("submitted") and not blocking_failures else "FAILED"
                )

            # Register successful live submission in rate tracker.
            if submission_mode == "live" and workflow_status == "SUBMITTED":
                rate_status = self.humanizer.register_submission(ats_type)

            result = {
                "status": workflow_status,
                "submission_mode": submission_mode,
                "ats_type": ats_type,
                "target_url": target_url,
                "fields_filled": fields_filled,
                "execution": execution,
                "human_escalations": _failures_to_escalations(failures),
                "execution_failures": failures,
                "screenshot_path": screenshot_path,
                "session_context_id": str(id(getattr(driver, "context", None))),
                "rate_limit": _as_rate_limit_dict(rate_status),
                "time_to_apply_seconds": int(time.perf_counter() - started_at),
            }
            if workflow_status == "FAILED":
                result["failure_record"] = _failure_record_from_failures(failures)
            return result
        except Exception as exc:
            logger.error("Submitter execution failed: %s", exc, exc_info=True)
            message = _friendly_submitter_error(exc)
            return {
                "status": "FAILED",
                "submission_mode": submission_mode,
                "ats_type": ats_type,
                "target_url": target_url,
                "fields_filled": [],
                "human_escalations": [
                    {
                        "type": "submitter",
                        "priority": "BLOCKING",
                        "message": message,
                    }
                ],
                "failure_record": {
                    "error_type": type(exc).__name__,
                    "error_message": message,
                    "timestamp": _utc_now(),
                },
                "screenshot_path": screenshot_path,
                "rate_limit": _as_rate_limit_dict(rate_status),
                "time_to_apply_seconds": int(time.perf_counter() - started_at),
            }
        finally:
            try:
                if hold_seconds > 0:
                    page = getattr(driver, "page", None)
                    if page is not None and hasattr(page, "wait_for_timeout"):
                        await page.wait_for_timeout(int(hold_seconds * 1000))
                await driver.stop()
            except Exception:
                logger.warning("Submitter driver cleanup failed.", exc_info=True)


async def submit_application(
    listing: dict[str, Any],
    fill_plan: dict[str, Any],
    submission_mode: str = "shadow",
    artifact_paths: dict[str, str] | None = None,
    apply_url: str | None = None,
    headless: bool = True,
    humanizer_config: HumanizerConfig | None = None,
    outcomes_db_path: str = DEFAULT_OUTCOMES_DB_PATH,
) -> dict[str, Any]:
    """Convenience function for one-shot submissions."""
    humanizer = Humanizer(config=humanizer_config) if humanizer_config else Humanizer()
    if submission_mode in {"shadow", "live"}:
        now = datetime.now(timezone.utc)
        lookback_seconds = max(24 * 3600, int(humanizer.config.per_ats_window_seconds))
        history = _load_submission_events_from_outcomes(
            outcomes_db_path,
            now=now,
            lookback_seconds=lookback_seconds,
        )
        humanizer.seed_submission_log(history)

    submitter = Submitter(humanizer=humanizer)
    return await submitter.run_submission(
        listing=listing,
        fill_plan=fill_plan,
        submission_mode=submission_mode,
        artifact_paths=artifact_paths,
        apply_url=apply_url,
        headless=headless,
    )
