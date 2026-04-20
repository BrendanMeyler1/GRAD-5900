"""
config.py — Centralized, typed application settings.

Reads from environment variables and .env file. Fails fast at startup
if required variables are missing rather than dying deep in the stack.

Usage:
    from config import settings
    print(settings.anthropic_api_key)
"""

from __future__ import annotations

from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All application configuration, validated on startup."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,  # Don't let empty OS env vars shadow .env file values
    )

    # ── LLM ──────────────────────────────────────────────────────────────────
    anthropic_api_key: str
    openai_api_key: str | None = None

    # ── Job Discovery ─────────────────────────────────────────────────────────
    jsearch_api_key: str | None = None  # RapidAPI JSearch; optional if DEV_MODE=true

    # ── Email (Outlook IMAP) ──────────────────────────────────────────────────
    outlook_email: str | None = None
    outlook_app_password: str | None = None
    # IMAP host — defaults to Outlook; override for other providers
    imap_host: str = "outlook.office365.com"
    imap_port: int = 993

    # ── Database & Encryption ─────────────────────────────────────────────────
    db_encryption_key: str | None = None  # Fernet key; auto-generated if absent
    data_dir: str = "./data"

    # ── Browser ───────────────────────────────────────────────────────────────
    headless: bool = True
    slow_mo: int = 50  # ms between Stagehand actions (humanization)

    # ── Dev / Testing ─────────────────────────────────────────────────────────
    dev_mode: bool = False  # True = mock browser + scrapers; no real external calls
    log_level: str = "INFO"

    # ── Server ────────────────────────────────────────────────────────────────
    port: int = 8000
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # ── Derived paths (not env vars) ──────────────────────────────────────────
    @property
    def db_path(self) -> Path:
        return Path(self.data_dir) / "v2.db"

    @property
    def resumes_dir(self) -> Path:
        return Path(self.data_dir) / "resumes"

    @property
    def generated_dir(self) -> Path:
        return Path(self.data_dir) / "generated"

    @property
    def screenshots_dir(self) -> Path:
        return Path(self.data_dir) / "screenshots"

    @property
    def logs_dir(self) -> Path:
        return Path(self.data_dir) / "logs"

    @property
    def linkedin_cookies_path(self) -> Path:
        return Path(self.data_dir) / "linkedin_cookies.json"

    # ── Feature flags ─────────────────────────────────────────────────────────
    @property
    def email_configured(self) -> bool:
        """True if Outlook IMAP credentials are present."""
        return bool(self.outlook_email and self.outlook_app_password)

    @property
    def jsearch_configured(self) -> bool:
        """True if JSearch API key is present or dev_mode is on."""
        return bool(self.jsearch_api_key) or self.dev_mode

    # ── Validators ────────────────────────────────────────────────────────────
    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid:
            raise ValueError(f"log_level must be one of {valid}, got {v!r}")
        return upper

    @model_validator(mode="after")
    def warn_missing_optionals(self) -> "Settings":
        import logging

        log = logging.getLogger(__name__)
        if not self.jsearch_api_key and not self.dev_mode:
            log.warning(
                "JSEARCH_API_KEY not set — job discovery will be limited. "
                "Set DEV_MODE=true to use mock data."
            )
        if not self.email_configured:
            log.info(
                "Outlook email not configured — email tracking disabled. "
                "Set OUTLOOK_EMAIL and OUTLOOK_APP_PASSWORD to enable."
            )
        return self


# Module-level singleton — import from anywhere.
settings = Settings()  # type: ignore[call-arg]
