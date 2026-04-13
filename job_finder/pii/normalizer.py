"""
Normalizer — Canonical + variant name resolution for PII tokens.

Handles the mapping between different forms of the same entity:
- "University of Connecticut" (canonical) ↔ "UConn", "UCONN" (variants)
- "International Business Machines Corporation" (canonical) ↔ "IBM" (variant)

Used by: Profile Analyst, PII Injector, Post-Upload Validator.

The Normalizer reads from the PII Vault's normalized_names table
and provides context-aware resolution — returning the appropriate
form based on what the ATS field expects.
"""

import logging
from typing import Optional

from pii.vault import PIIVault

logger = logging.getLogger("job_finder.pii.normalizer")


class Normalizer:
    """Resolves PII tokens to the appropriate name form based on context.

    Example:
        normalizer.resolve("{{SCHOOL}}", context="full_name")
        → "University of Connecticut"

        normalizer.resolve("{{SCHOOL}}", context="abbreviation")
        → "UConn"
    """

    def __init__(self, vault: PIIVault):
        self.vault = vault
        # Cache normalized names to avoid repeated DB lookups
        self._cache: dict[str, dict] = {}

    def _load_names(self, token_key: str) -> dict:
        """Load and cache normalized names for a token."""
        if token_key not in self._cache:
            names = self.vault.get_normalized_names(token_key)
            self._cache[token_key] = names
        return self._cache[token_key]

    def resolve(
        self,
        token_key: str,
        context: Optional[str] = None,
    ) -> Optional[str]:
        """Resolve a token to the appropriate name form.

        Args:
            token_key: The PII token, e.g. "{{SCHOOL}}"
            context: Resolution context:
                - "full_name" or "canonical" → canonical form
                - "abbreviation" or "short" → first variant (shortest)
                - None → canonical form (default)

        Returns:
            The resolved name string, or the raw vault value if no
            normalized names are registered for this token.
        """
        names = self._load_names(token_key)
        canonical = names.get("canonical")
        variants = names.get("variants", [])

        if not canonical and not variants:
            # No normalization registered — fall back to raw vault value
            return self.vault.get_token(token_key)

        if context in ("abbreviation", "short"):
            if variants:
                # Return shortest variant
                return min(variants, key=len)
            return canonical
        else:
            # Default: return canonical form
            return canonical or (variants[0] if variants else None)

    def register(
        self,
        token_key: str,
        canonical: str,
        variants: Optional[list[str]] = None,
    ) -> None:
        """Register canonical + variant forms for a token.

        This clears any existing normalized names for this token
        and replaces them with the new set.

        Args:
            token_key: The PII token, e.g. "{{SCHOOL}}"
            canonical: The full/canonical form, e.g. "University of Connecticut"
            variants: Alternative forms, e.g. ["UConn", "UCONN"]
        """
        # Clear existing normalized names for this token
        from contextlib import closing
        with closing(self.vault._connect()) as conn, conn:
            conn.execute(
                "DELETE FROM normalized_names WHERE token_key = ?",
                (token_key,),
            )

        # Store canonical
        self.vault.store_normalized_name(token_key, "canonical", canonical)

        # Store variants
        for variant in (variants or []):
            self.vault.store_normalized_name(token_key, "variant", variant)

        # Invalidate cache
        self._cache.pop(token_key, None)
        logger.info(
            f"Registered normalization for {token_key}: "
            f"canonical='{canonical}', variants={variants or []}"
        )

    def find_best_match(self, token_key: str, target_text: str) -> Optional[str]:
        """Find the best matching name form for a target text.

        Useful when a form field shows a specific expected format.
        Matches by checking if any form is a substring of target_text
        or vice versa.

        Args:
            token_key: The PII token
            target_text: The field label or existing value to match against

        Returns:
            The best matching name form, or canonical as default.
        """
        names = self._load_names(token_key)
        canonical = names.get("canonical", "")
        variants = names.get("variants", [])

        target_lower = target_text.lower()
        all_forms = ([canonical] if canonical else []) + variants

        # Exact match first
        for form in all_forms:
            if form and form.lower() == target_lower:
                return form

        # Substring match
        for form in all_forms:
            if form and form.lower() in target_lower:
                return form
            if form and target_lower in form.lower():
                return form

        # Default to canonical
        return canonical

    def clear_cache(self) -> None:
        """Clear the normalization cache."""
        self._cache.clear()
