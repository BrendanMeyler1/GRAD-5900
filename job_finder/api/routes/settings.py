"""
Settings routes for runtime configuration.

Endpoints:
    GET /api/settings
    PUT /api/settings
"""

from __future__ import annotations

import os
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter()


class SettingsModel(BaseModel):
    daily_application_cap: int = Field(default=10, ge=1, le=500)
    per_ats_hourly_cap: int = Field(default=3, ge=1, le=100)
    default_submission_mode: Literal["dry_run", "shadow", "live"] = "shadow"
    use_browser_automation: bool = False
    headless: bool = True
    cooldown_seconds: int = Field(default=120, ge=0, le=3600)


def _load_default_settings() -> SettingsModel:
    return SettingsModel(
        daily_application_cap=int(os.getenv("DAILY_APPLICATION_CAP", "10")),
        per_ats_hourly_cap=int(os.getenv("PER_ATS_HOURLY_CAP", "3")),
        default_submission_mode=os.getenv("DEFAULT_SUBMISSION_MODE", "shadow"),
        use_browser_automation=os.getenv("USE_BROWSER_AUTOMATION", "false").lower() == "true",
        headless=os.getenv("HEADLESS", "true").lower() == "true",
        cooldown_seconds=int(os.getenv("COOLDOWN_SECONDS", "120")),
    )


_settings_state = _load_default_settings()


class SettingsUpdateRequest(BaseModel):
    daily_application_cap: int | None = Field(default=None, ge=1, le=500)
    per_ats_hourly_cap: int | None = Field(default=None, ge=1, le=100)
    default_submission_mode: Literal["dry_run", "shadow", "live"] | None = None
    use_browser_automation: bool | None = None
    headless: bool | None = None
    cooldown_seconds: int | None = Field(default=None, ge=0, le=3600)


def reset_settings_state() -> None:
    global _settings_state
    _settings_state = _load_default_settings()


def get_settings_snapshot() -> dict:
    """Return current settings as a plain dict for internal route consumers."""
    return _settings_state.model_dump()


@router.get("/settings", summary="Current config (daily cap, mode, etc.)")
async def get_settings():
    return {
        "status": "success",
        "settings": _settings_state.model_dump(),
    }


@router.put("/settings", summary="Update runtime settings")
async def update_settings(request: SettingsUpdateRequest):
    global _settings_state
    merged = _settings_state.model_dump()
    for key, value in request.model_dump(exclude_none=True).items():
        merged[key] = value
    _settings_state = SettingsModel(**merged)
    return {
        "status": "success",
        "settings": _settings_state.model_dump(),
    }
