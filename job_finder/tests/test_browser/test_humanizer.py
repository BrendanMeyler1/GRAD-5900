"""Tests for browser.humanizer."""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta, timezone

from browser.humanizer import Humanizer, HumanizerConfig


class _FakeLocator:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, int | None]] = []

    async def fill(self, text: str) -> None:
        self.events.append(("fill", text, None))

    async def type(self, text: str, delay: int = 0) -> None:
        self.events.append(("type", text, delay))


class _FakePage:
    def __init__(self) -> None:
        self.selector_used: str | None = None
        self.locator_obj = _FakeLocator()

    def locator(self, selector: str) -> _FakeLocator:
        self.selector_used = selector
        return self.locator_obj


def test_delay_sampling_respects_config_bounds():
    humanizer = Humanizer(
        config=HumanizerConfig(min_action_delay_s=2.0, max_action_delay_s=8.0),
        rng=random.Random(7),
    )
    delay = humanizer.next_action_delay()
    assert 2.0 <= delay <= 8.0


def test_rate_limits_enforced_for_daily_and_per_ats():
    base_time = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)
    now_holder = {"now": base_time}

    humanizer = Humanizer(
        config=HumanizerConfig(daily_cap=2, per_ats_limit=1, per_ats_window_seconds=3600),
        now_fn=lambda: now_holder["now"],
    )

    first = humanizer.check_rate_limits("greenhouse")
    assert first.allowed is True
    humanizer.register_submission("greenhouse")

    second = humanizer.check_rate_limits("greenhouse")
    assert second.allowed is False
    assert second.reason == "per_ats_cooldown"

    now_holder["now"] = base_time + timedelta(hours=2)
    third = humanizer.check_rate_limits("greenhouse")
    assert third.allowed is True
    humanizer.register_submission("greenhouse")

    fourth = humanizer.check_rate_limits("greenhouse")
    assert fourth.allowed is False
    assert fourth.reason == "daily_cap_reached"


def test_type_text_char_by_char():
    async def _no_sleep(_: float) -> None:
        return None

    humanizer = Humanizer(
        config=HumanizerConfig(min_key_delay_s=0.01, max_key_delay_s=0.01),
        rng=random.Random(1),
        sleep_fn=_no_sleep,
    )
    page = _FakePage()
    asyncio.run(humanizer.type_text(page=page, selector="#name", text="ABC"))

    assert page.selector_used == "#name"
    events = page.locator_obj.events
    assert events[0] == ("fill", "", None)
    assert events[1][0] == "type" and events[1][1] == "A"
    assert events[2][0] == "type" and events[2][1] == "B"
    assert events[3][0] == "type" and events[3][1] == "C"

