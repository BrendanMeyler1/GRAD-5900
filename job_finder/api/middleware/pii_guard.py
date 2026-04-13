"""
PII Guard Middleware — Scans API responses for PII leaks.

Defense-in-depth: even if tokenization is bypassed somewhere,
this middleware catches PII before it leaves the server.

In production, connects to PIIVault for vault-based detection.
Falls back to heuristic-only mode if vault is unavailable.
"""

import logging
import os

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger("job_finder.api.middleware.pii_guard")

# Paths exempt from PII scanning (health checks, static files, etc.)
_EXEMPT_PATHS = {"/", "/api/health", "/docs", "/openapi.json", "/redoc"}


class PIIGuardMiddleware(BaseHTTPMiddleware):
    """Middleware that scans outgoing API responses for PII leaks.

    Uses both vault-based detection (if vault is available)
    and heuristic pattern matching (email, phone, SSN formats).
    """

    def __init__(self, app, vault=None):
        super().__init__(app)
        self._vault = vault
        self._sanitizer = None
        self._enabled = os.getenv("PII_GUARD_ENABLED", "true").lower() == "true"

    @property
    def sanitizer(self):
        """Lazy-load the PII sanitizer."""
        if self._sanitizer is None:
            try:
                from pii.sanitizer import PIISanitizer
                self._sanitizer = PIISanitizer(vault=self._vault)
            except Exception as e:
                logger.warning(f"Could not initialize PII sanitizer: {e}")
                self._sanitizer = None
        return self._sanitizer

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Process request and scan response for PII."""
        # Skip exempt paths
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        # Skip if disabled
        if not self._enabled:
            return await call_next(request)

        response = await call_next(request)

        # Only scan JSON responses
        if not hasattr(response, "body"):
            return response

        # Try to scan the response body
        if self.sanitizer:
            try:
                body = b""
                async for chunk in response.body_iterator:
                    body += chunk if isinstance(chunk, bytes) else chunk.encode()

                body_text = body.decode("utf-8", errors="ignore")
                leaks = self.sanitizer.scan(body_text, raise_on_leak=False)

                if leaks:
                    logger.critical(
                        f"PII LEAK detected in API response to {request.url.path}: "
                        f"{len(leaks)} leak(s) found"
                    )
                    # In strict mode, block the response
                    return JSONResponse(
                        status_code=500,
                        content={
                            "error": "PII leak detected in response",
                            "detail": "Response blocked by PII guard middleware. "
                                      "Check server logs for details.",
                            "leak_count": len(leaks),
                        },
                    )

                # No leaks — reconstruct response
                return Response(
                    content=body,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    media_type=response.media_type,
                )

            except Exception as e:
                logger.error(f"PII guard scan error: {e}")
                # On error, let the response through (fail-open)
                # In production, you may want fail-closed instead
                return response

        return response
