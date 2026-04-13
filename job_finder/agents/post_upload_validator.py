"""
Post-Upload Validator agent.

Validates filled ATS values against fill plan expectations and proposes
safe corrections, using normalizer logic for canonical/variant matching.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from pii.normalizer import Normalizer
from pii.vault import PIIVault


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _normalize_casefold(value: str) -> str:
    return _normalize_space(value).casefold()


def _digits_only(value: str) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


def _is_token(value: str | None) -> bool:
    if not value:
        return False
    return bool(re.fullmatch(r"\{\{[A-Z0-9_]+\}\}", value.strip()))


def _token_forms(token_key: str, normalizer: Normalizer | None) -> set[str]:
    """
    Build acceptable normalized forms for a token using normalizer/vault.
    """
    forms: set[str] = set()
    if normalizer is None:
        return forms

    canonical = normalizer.resolve(token_key, context="canonical")
    short = normalizer.resolve(token_key, context="abbreviation")
    if canonical:
        forms.add(_normalize_casefold(canonical))
    if short:
        forms.add(_normalize_casefold(short))

    # Include all known variants for best acceptance
    names = normalizer.vault.get_normalized_names(token_key)
    for item in names.get("variants", []) or []:
        forms.add(_normalize_casefold(str(item)))

    raw = normalizer.vault.get_token(token_key)
    if raw:
        forms.add(_normalize_casefold(raw))
    return forms


def _normalize_observed_map(observed_fields: dict[str, Any] | list[dict[str, Any]]) -> dict[str, str]:
    """
    Accept either:
    - {"field_id": "value", ...}
    - [{"field_id": "...", "value": "..."}, ...]
    """
    if isinstance(observed_fields, dict):
        return {str(k): str(v) for k, v in observed_fields.items()}

    result: dict[str, str] = {}
    if isinstance(observed_fields, list):
        for item in observed_fields:
            if not isinstance(item, dict):
                continue
            field_id = str(item.get("field_id", "")).strip()
            value = item.get("value")
            if field_id:
                result[field_id] = "" if value is None else str(value)
    return result


def _equivalent(expected: str, observed: str, normalizer: Normalizer | None) -> bool:
    expected = expected or ""
    observed = observed or ""

    if expected.startswith("QUESTION_RESPONDER:"):
        return True

    # Token-aware equivalence (normalizer canonical/variant support)
    if _is_token(expected):
        forms = _token_forms(expected, normalizer)
        if forms and _normalize_casefold(observed) in forms:
            return True

    # Phone-like formatting equivalence
    exp_digits = _digits_only(expected)
    obs_digits = _digits_only(observed)
    if len(exp_digits) >= 10 and exp_digits == obs_digits:
        return True

    # Generic case-insensitive whitespace-normalized equality
    return _normalize_casefold(expected) == _normalize_casefold(observed)


def _severity(pii_level: str, confidence: float) -> str:
    level = (pii_level or "").upper()
    if level == "HIGH":
        return "major"
    if confidence < 0.5:
        return "moderate"
    if level == "MEDIUM":
        return "moderate"
    return "minor"


def _best_suggested_value(expected: str, normalizer: Normalizer | None) -> str:
    if _is_token(expected) and normalizer is not None:
        resolved = normalizer.resolve(expected, context="canonical")
        if resolved:
            return resolved
    return expected


def validate_post_upload(
    fill_plan: dict[str, Any],
    observed_fields: dict[str, Any] | list[dict[str, Any]],
    normalizer: Normalizer | None = None,
    vault: PIIVault | None = None,
) -> dict[str, Any]:
    """
    Validate autofilled ATS values after upload/fill.

    Returns corrections and review requirement flags.
    """
    if normalizer is None and vault is not None:
        normalizer = Normalizer(vault)

    fields = list(fill_plan.get("fields", []))
    observed_map = _normalize_observed_map(observed_fields)

    corrections: list[dict[str, Any]] = []
    checked = 0
    major_count = 0

    for field in fields:
        if not isinstance(field, dict):
            continue
        field_id = str(field.get("field_id", "")).strip()
        if not field_id:
            continue

        expected_value = field.get("value")
        if expected_value is None:
            continue
        expected_value = str(expected_value)

        # Skip file uploads and responder placeholders
        if str(field.get("type", "")).lower() == "file_upload":
            continue
        if expected_value.startswith("QUESTION_RESPONDER:"):
            continue

        if field_id not in observed_map:
            severity = _severity(
                pii_level=str(field.get("pii_level", "MEDIUM")),
                confidence=float(field.get("confidence", 0.0)),
            )
            if severity == "major":
                major_count += 1
            corrections.append(
                {
                    "field_id": field_id,
                    "expected_value": expected_value,
                    "observed_value": None,
                    "suggested_value": _best_suggested_value(expected_value, normalizer),
                    "severity": severity,
                    "reason": "Field missing after upload/autofill",
                }
            )
            continue

        observed_value = observed_map[field_id]
        checked += 1
        if _equivalent(expected_value, observed_value, normalizer):
            continue

        severity = _severity(
            pii_level=str(field.get("pii_level", "MEDIUM")),
            confidence=float(field.get("confidence", 0.0)),
        )
        if severity == "major":
            major_count += 1
        corrections.append(
            {
                "field_id": field_id,
                "expected_value": expected_value,
                "observed_value": observed_value,
                "suggested_value": _best_suggested_value(expected_value, normalizer),
                "severity": severity,
                "reason": "Autofill mismatch",
            }
        )

    needs_human = major_count > 0
    return {
        "validated_at": _utc_now(),
        "corrections": corrections,
        "needs_human_review": needs_human,
        "summary": {
            "total_fields_planned": len(fields),
            "total_fields_checked": checked,
            "mismatches": len(corrections),
            "major_mismatches": major_count,
        },
    }
