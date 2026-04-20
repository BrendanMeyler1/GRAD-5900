"""
logging_config.py — Centralized logging setup for job_finder_v2.

Sets up two handlers:
  1. Console (stdout): Human-readable, coloured by level, for development.
  2. File (data/logs/app.log): JSON-structured, one object per line, rotating.
     10 MB per file, 5 files kept (~50 MB total).

All third-party library loggers are capped at WARNING to reduce noise.

Usage:
    # In api/main.py lifespan, call once at startup:
    from logging_config import setup_logging
    setup_logging(level=settings.log_level, log_dir=str(settings.logs_dir))

    # In every module:
    import logging
    log = logging.getLogger(__name__)
    log.info("something happened", extra={"job_id": "abc", "duration_ms": 123})
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any


class JSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "module": record.name,
            "msg": record.getMessage(),
        }

        # Merge any extra fields passed via extra={...}
        for key, value in record.__dict__.items():
            if key not in {
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "message",
                "taskName",
            }:
                payload[key] = value

        if record.exc_info:
            payload["traceback"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


class HumanFormatter(logging.Formatter):
    """Coloured, human-readable format for console output."""

    COLOURS = {
        "DEBUG": "\033[36m",    # cyan
        "INFO": "\033[32m",     # green
        "WARNING": "\033[33m",  # yellow
        "ERROR": "\033[31m",    # red
        "CRITICAL": "\033[35m", # magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        colour = self.COLOURS.get(record.levelname, "")
        level = f"{colour}{record.levelname:<8}{self.RESET}"
        name = f"\033[2m{record.name}\033[0m"  # dim
        msg = record.getMessage()

        # Surface the most useful extra fields inline
        extras = []
        for key in ("job_id", "app_id", "request_id", "step", "duration_ms",
                    "company", "source", "status_code"):
            val = record.__dict__.get(key)
            if val is not None:
                extras.append(f"{key}={val}")

        suffix = f"  \033[2m{' '.join(extras)}\033[0m" if extras else ""
        base = f"[{self.formatTime(record, '%H:%M:%S')}] {level} {name} — {msg}{suffix}"

        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)

        return base


# Loggers that produce noise we don't need at DEBUG/INFO level
_NOISY_LOGGERS = [
    "httpx", "httpcore", "anthropic", "openai", "playwright",
    "stagehand", "uvicorn.access", "asyncio", "multipart",
]


def setup_logging(level: str = "INFO", log_dir: str = "./data/logs") -> None:
    """
    Configure root logger with console + rotating JSON file handlers.

    Args:
        level: Root log level string (e.g., "INFO", "DEBUG").
        log_dir: Directory for log files. Created if absent.
    """
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove any handlers already attached (e.g., from pytest)
    root.handlers.clear()

    # ── Console handler ───────────────────────────────────────────────────────
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(HumanFormatter())
    console_handler.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.addHandler(console_handler)

    # ── Rotating JSON file handler ─────────────────────────────────────────────
    file_handler = logging.handlers.RotatingFileHandler(
        filename=log_path / "app.log",
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(JSONFormatter())
    file_handler.setLevel(logging.DEBUG)  # file always captures DEBUG
    root.addHandler(file_handler)

    # ── Silence noisy third-party loggers ─────────────────────────────────────
    for logger_name in _NOISY_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "Logging configured",
        extra={"level": level, "log_file": str(log_path / "app.log")},
    )
