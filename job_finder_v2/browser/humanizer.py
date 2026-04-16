"""
browser/humanizer.py — Timing profiles for browser automation.

Stagehand accepts a configurable action delay. Setting it too low makes
actions instantaneous (bot-like, higher chance of triggering anti-automation
defenses on sites like LinkedIn). Setting it too high makes shadow runs
painful to watch.

We define three named profiles and let the filler pick one based on the
detected ATS. Heavily-protected hosts (LinkedIn) get longer delays;
friendly ATSes (Greenhouse, Lever) run fast.

Usage:
    from browser.humanizer import get_profile
    profile = get_profile("linkedin")  # or "universal", "fast"
    stagehand = Stagehand(action_delay_ms=profile.action_delay_ms, ...)
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class HumanProfile:
    """
    Timing parameters for a single browser session.

    Attributes:
        name: Human-readable profile name.
        action_delay_ms: Base pause between clicks/fills, in milliseconds.
        typing_jitter_ms: Random extra delay added per keystroke (0..jitter).
        page_settle_ms: Pause after navigation before interacting.
        max_retries: How many times to retry a flaky action before giving up.
    """

    name: str
    action_delay_ms: int
    typing_jitter_ms: int
    page_settle_ms: int
    max_retries: int = 3

    def action_jitter(self) -> int:
        """Return action_delay_ms plus random jitter (±25%)."""
        spread = int(self.action_delay_ms * 0.25)
        return self.action_delay_ms + random.randint(-spread, spread)


_PROFILES: dict[str, HumanProfile] = {
    # Fast: for trusted ATSes and headless CI
    "fast": HumanProfile(
        name="fast",
        action_delay_ms=250,
        typing_jitter_ms=20,
        page_settle_ms=500,
    ),
    # Universal: reasonable default for any ATS
    "universal": HumanProfile(
        name="universal",
        action_delay_ms=600,
        typing_jitter_ms=60,
        page_settle_ms=1200,
    ),
    # LinkedIn / Workday / anything with active bot detection
    "careful": HumanProfile(
        name="careful",
        action_delay_ms=1200,
        typing_jitter_ms=120,
        page_settle_ms=2500,
    ),
}


_ATS_TO_PROFILE: dict[str, str] = {
    "greenhouse": "fast",
    "lever": "fast",
    "ashby": "fast",
    "workable": "fast",
    "smartrecruiters": "universal",
    "workday": "careful",
    "linkedin": "careful",
    "icims": "careful",
    "taleo": "careful",
    "indeed": "careful",
    "handshake": "careful",
}


def get_profile(ats_type: str | None = None, *, override: str | None = None) -> HumanProfile:
    """
    Pick a timing profile for a browser run.

    Args:
        ats_type: ATS identifier from `detect_ats_type` (e.g. "greenhouse").
                  Ignored if `override` is given.
        override: Explicit profile name ("fast" | "universal" | "careful").

    Returns:
        The matching `HumanProfile`. Falls back to "universal" if the
        ATS is unknown and no override is given.
    """
    if override:
        return _PROFILES.get(override, _PROFILES["universal"])
    key = _ATS_TO_PROFILE.get((ats_type or "").lower(), "universal")
    return _PROFILES[key]


def list_profiles() -> list[str]:
    """Return the names of all built-in profiles."""
    return list(_PROFILES.keys())
