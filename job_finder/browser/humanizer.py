"""
Humanized browser interaction helpers.

Implements the behavior described in the implementation plan:
- randomized delays
- character-by-character typing
- daily cap + per-ATS cooldown checks
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable


@dataclass(frozen=True)
class HumanizerConfig:
    """Configuration for humanized browser behavior."""

    # Faster field-to-field movement with slightly slower typing.
    min_action_delay_s: float = 0.1
    max_action_delay_s: float = 0.35
    min_key_delay_s: float = 0.11
    max_key_delay_s: float = 0.2
    daily_cap: int = 10
    per_ats_limit: int = 3
    per_ats_window_seconds: int = 3600


@dataclass(frozen=True)
class RateLimitStatus:
    """Result of checking submission limits."""

    allowed: bool
    reason: str | None
    retry_after_seconds: int | None
    daily_used: int
    daily_remaining: int
    ats_used: int
    ats_remaining: int


class Humanizer:
    """Humanized interaction utilities + submission rate limits."""

    def __init__(
        self,
        config: HumanizerConfig | None = None,
        rng: random.Random | None = None,
        now_fn: Callable[[], datetime] | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self.config = config or HumanizerConfig()
        self._rng = rng or random.Random()
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._sleep_fn = sleep_fn or asyncio.sleep
        self._submission_log: list[tuple[str, datetime]] = []

    def _now(self) -> datetime:
        now = self._now_fn()
        if now.tzinfo is None:
            return now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc)

    def _prune_logs(self, now: datetime) -> None:
        daily_window_start = now - timedelta(days=1)
        self._submission_log = [
            (ats, ts)
            for ats, ts in self._submission_log
            if ts >= daily_window_start
        ]

    def seed_submission_log(self, events: list[tuple[str, datetime]]) -> None:
        """
        Replace in-memory submission history with normalized historical events.

        This is useful when limits should be enforced across process restarts
        by loading persisted submissions from a database.
        """
        normalized: list[tuple[str, datetime]] = []
        for ats_type, timestamp in events or []:
            ts = timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            else:
                ts = ts.astimezone(timezone.utc)
            normalized.append((str(ats_type or "unknown").strip().lower(), ts))
        self._submission_log = normalized
        self._prune_logs(self._now())

    def next_action_delay(self) -> float:
        """Sample a delay for high-level actions (click, navigate, upload)."""
        return self._rng.uniform(
            self.config.min_action_delay_s,
            self.config.max_action_delay_s,
        )

    def next_key_delay(self) -> float:
        """Sample a per-keystroke delay (seconds)."""
        return self._rng.uniform(
            self.config.min_key_delay_s,
            self.config.max_key_delay_s,
        )

    async def pause(self, min_s: float | None = None, max_s: float | None = None) -> float:
        """Pause for a randomized interval and return the duration."""
        if min_s is None:
            min_s = self.config.min_action_delay_s
        if max_s is None:
            max_s = self.config.max_action_delay_s
        duration = self._rng.uniform(min_s, max_s)
        await self._sleep_fn(duration)
        return duration

    async def pause_action(self) -> float:
        """Default action pause."""
        duration = self.next_action_delay()
        await self._sleep_fn(duration)
        return duration

    def check_rate_limits(self, ats_type: str) -> RateLimitStatus:
        """Check daily + per-ATS submission limits."""
        ats = (ats_type or "unknown").strip().lower()
        now = self._now()
        self._prune_logs(now)

        daily_used = len(self._submission_log)
        daily_remaining = max(0, self.config.daily_cap - daily_used)

        ats_window_start = now - timedelta(seconds=self.config.per_ats_window_seconds)
        ats_recent = [
            ts for logged_ats, ts in self._submission_log if logged_ats == ats and ts >= ats_window_start
        ]
        ats_used = len(ats_recent)
        ats_remaining = max(0, self.config.per_ats_limit - ats_used)

        if daily_remaining <= 0:
            oldest = min(ts for _, ts in self._submission_log) if self._submission_log else now
            retry = int(max(1, (oldest + timedelta(days=1) - now).total_seconds()))
            return RateLimitStatus(
                allowed=False,
                reason="daily_cap_reached",
                retry_after_seconds=retry,
                daily_used=daily_used,
                daily_remaining=daily_remaining,
                ats_used=ats_used,
                ats_remaining=ats_remaining,
            )

        if ats_remaining <= 0:
            oldest_ats = min(ats_recent)
            retry = int(
                max(
                    1,
                    (
                        oldest_ats
                        + timedelta(seconds=self.config.per_ats_window_seconds)
                        - now
                    ).total_seconds(),
                )
            )
            return RateLimitStatus(
                allowed=False,
                reason="per_ats_cooldown",
                retry_after_seconds=retry,
                daily_used=daily_used,
                daily_remaining=daily_remaining,
                ats_used=ats_used,
                ats_remaining=ats_remaining,
            )

        return RateLimitStatus(
            allowed=True,
            reason=None,
            retry_after_seconds=None,
            daily_used=daily_used,
            daily_remaining=daily_remaining,
            ats_used=ats_used,
            ats_remaining=ats_remaining,
        )

    def register_submission(self, ats_type: str) -> RateLimitStatus:
        """Record a submission event after validating limits."""
        status = self.check_rate_limits(ats_type)
        if not status.allowed:
            raise RuntimeError(
                f"Submission blocked by limits ({status.reason}). "
                f"Retry after {status.retry_after_seconds}s."
            )

        ats = (ats_type or "unknown").strip().lower()
        self._submission_log.append((ats, self._now()))
        return self.check_rate_limits(ats)

    async def type_text(
        self,
        page: object,
        selector: str,
        text: str,
        clear_first: bool = True,
    ) -> None:
        """
        Type character-by-character into a field.

        Works with Playwright-like `page` objects that expose `locator()` and with
        simple test doubles exposing `fill()` / `type()`.
        """
        target = page.locator(selector) if hasattr(page, "locator") else page

        if clear_first:
            if hasattr(target, "fill"):
                await target.fill("")
            elif hasattr(page, "fill"):
                await page.fill(selector, "")

        for ch in text:
            delay_ms = int(self.next_key_delay() * 1000)
            if hasattr(target, "type"):
                try:
                    await target.type(ch, delay=delay_ms)
                except TypeError:
                    await target.type(ch)
                    await self._sleep_fn(delay_ms / 1000)
            elif hasattr(page, "type"):
                try:
                    await page.type(selector, ch, delay=delay_ms)
                except TypeError:
                    await page.type(selector, ch)
                    await self._sleep_fn(delay_ms / 1000)
            else:
                raise AttributeError("Page object does not support typing operations.")

    async def random_scroll(
        self,
        page: object,
        min_pixels: int = 300,
        max_pixels: int = 1200,
    ) -> int:
        """Perform a realistic scroll gesture when supported by the page object."""
        pixels = self._rng.randint(min_pixels, max_pixels)
        if hasattr(page, "mouse") and hasattr(page.mouse, "wheel"):
            await page.mouse.wheel(0, pixels)
        elif hasattr(page, "evaluate"):
            await page.evaluate("(amount) => window.scrollBy(0, amount)", pixels)
        await self.pause(min_s=0.2, max_s=1.0)
        return pixels
