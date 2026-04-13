"""
PII Sanitizer — Middleware filter to prevent PII leakage.

Scans outbound text (destined for remote LLMs, logs, or API responses)
and raises PIILeakError if any real PII values are detected.

This is defense-in-depth: even if a bug bypasses tokenization,
the sanitizer catches it before data leaves the local machine.
"""

import logging
import re
from typing import Optional

from errors import PIILeakError
from pii.vault import PIIVault

logger = logging.getLogger("job_finder.pii.sanitizer")

# Common PII patterns (phone, email, SSN) for heuristic detection
_PATTERNS = {
    "email": re.compile(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
    ),
    "phone_us": re.compile(
        r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"
    ),
    "ssn": re.compile(
        r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b"
    ),
}


class PIISanitizer:
    """Scans text for PII leaks and raises PIILeakError if found.

    Two detection modes:
    1. Vault-based: Checks if any stored PII value appears in the text.
    2. Heuristic: Regex patterns for common PII formats (email, phone, SSN).
    """

    def __init__(self, vault: Optional[PIIVault] = None):
        self.vault = vault

    def scan(self, text: str, raise_on_leak: bool = True) -> list[dict]:
        """Scan text for PII leaks.

        Args:
            text: Text to scan.
            raise_on_leak: If True, raises PIILeakError on first detection.

        Returns:
            List of detected leaks (each with type and context).

        Raises:
            PIILeakError: If raise_on_leak is True and PII is found.
        """
        leaks = []

        # Vault-based detection
        if self.vault:
            all_tokens = self.vault.get_all_tokens_decrypted()
            for token_key, value in all_tokens.items():
                if value and len(value) > 2 and value in text:
                    leak = {
                        "type": "vault_match",
                        "token_key": token_key,
                        "context": self._extract_context(text, value),
                    }
                    leaks.append(leak)
                    logger.critical(
                        f"PII LEAK DETECTED: {token_key} found in outbound text"
                    )
                    if raise_on_leak:
                        raise PIILeakError(
                            f"PII leak detected: token {token_key} value found "
                            f"in text destined for remote processing"
                        )

        # Heuristic pattern detection
        for pattern_name, pattern in _PATTERNS.items():
            matches = pattern.findall(text)
            for match in matches:
                # Skip if it's inside a {{TOKEN}} placeholder
                token_context = f"{{{{{match}}}}}"
                if token_context in text:
                    continue

                leak = {
                    "type": "pattern_match",
                    "pattern": pattern_name,
                    "value_preview": match[:4] + "***",  # partial for logging
                }
                leaks.append(leak)
                logger.warning(
                    f"Potential PII pattern ({pattern_name}) detected in text"
                )
                if raise_on_leak:
                    raise PIILeakError(
                        f"Potential PII detected: {pattern_name} pattern found "
                        f"in text destined for remote processing"
                    )

        return leaks

    def sanitize(self, text: str) -> str:
        """Remove detected PII from text, replacing with [REDACTED].

        Use this for logging — removes PII but preserves structure.

        Args:
            text: Text potentially containing PII.

        Returns:
            Text with PII values replaced by [REDACTED].
        """
        sanitized = text

        # Replace vault values
        if self.vault:
            all_tokens = self.vault.get_all_tokens_decrypted()
            sorted_tokens = sorted(
                all_tokens.items(), key=lambda x: len(x[1]), reverse=True
            )
            for token_key, value in sorted_tokens:
                if value and len(value) > 2 and value in sanitized:
                    sanitized = sanitized.replace(value, "[REDACTED]")

        # Replace pattern matches
        for pattern_name, pattern in _PATTERNS.items():
            sanitized = pattern.sub("[REDACTED]", sanitized)

        return sanitized

    @staticmethod
    def _extract_context(text: str, value: str, window: int = 30) -> str:
        """Extract surrounding context for a detected value (for logging).

        Returns the value's position with surrounding characters,
        with the actual value masked.
        """
        idx = text.find(value)
        if idx == -1:
            return ""
        start = max(0, idx - window)
        end = min(len(text), idx + len(value) + window)
        context = text[start:end]
        return context.replace(value, "[***]")
