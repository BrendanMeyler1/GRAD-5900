"""
api/middleware.py — Request logging + global exception handling.

Every HTTP request gets:
    - A unique X-Request-ID injected into request.state and response headers
    - Structured logs for start + end (with status, duration, method, path)
    - Unhandled exceptions caught and returned as a uniform JSON error

This is the primary debug tool: tailing the log file + grepping for a
request ID shows exactly what happened for a given user action.
"""

from __future__ import annotations

import logging
import time
import traceback
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

log = logging.getLogger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Inject X-Request-ID and log every request."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = request_id

        start = time.monotonic()
        log.info(
            "http.request.start",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "query": str(request.url.query) if request.url.query else "",
                "client": request.client.host if request.client else "unknown",
            },
        )

        try:
            response = await call_next(request)
        except Exception as exc:  # noqa: BLE001 — we re-raise after logging
            duration_ms = int((time.monotonic() - start) * 1000)
            log.exception(
                "http.request.unhandled",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": duration_ms,
                    "error": str(exc),
                },
            )
            raise

        duration_ms = int((time.monotonic() - start) * 1000)
        response.headers["X-Request-ID"] = request_id
        log_level = (
            log.error
            if response.status_code >= 500
            else log.warning
            if response.status_code >= 400
            else log.info
        )
        log_level(
            "http.request.end",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response


async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all for unhandled exceptions. Returns uniform JSON shape."""
    request_id = getattr(request.state, "request_id", "unknown")
    log.exception(
        "http.exception",
        extra={
            "request_id": request_id,
            "path": request.url.path,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc()[:4000],
        },
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_server_error",
            "message": "An unexpected error occurred. See server logs for details.",
            "request_id": request_id,
        },
    )
