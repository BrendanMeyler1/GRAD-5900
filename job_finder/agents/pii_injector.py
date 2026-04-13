"""
PII Injector agent (local-only).

Resolves tokenized artifacts (resume/cover letter/fill plan) into final values
using the encrypted local PII vault and Normalizer. Supports optional local-LLM
post-processing through task_type="pii_injection" in LLMRouter.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from typing import Any

from llm_router.router import LLMRouter
from pii.field_classifier import FieldClassifier
from pii.normalizer import Normalizer
from pii.vault import PIIVault

logger = logging.getLogger("job_finder.agents.pii_injector")

TOKEN_PATTERN = re.compile(r"\{\{([A-Za-z0-9_ ]+)\}\}")
_LEVEL_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}


def _normalize_level(level: str | None) -> str:
    normalized = str(level or "").strip().upper()
    if normalized in _LEVEL_RANK:
        return normalized
    return "MEDIUM"


def _max_level(*levels: str | None) -> str:
    best = "LOW"
    best_rank = 0
    for level in levels:
        normalized = _normalize_level(level)
        rank = _LEVEL_RANK.get(normalized, 0)
        if rank > best_rank:
            best = normalized
            best_rank = rank
    return best


def _extract_tokens(text: str | None) -> list[str]:
    if not text:
        return []
    # Find tokens but keep the full match for replacement, while extracting the inner key for standardizing
    tokens = []
    # re.finditer gives us the full match text
    for match in re.finditer(r"\{\{[A-Za-z0-9_ ]+\}\}", text):
        token_full = match.group(0)
        if token_full not in tokens:
            tokens.append(token_full)
    return tokens


def _standardize_token_key(token_full: str) -> str:
    """Convert variable cases like '{{First Name}}' back to '{{FIRST_NAME}}' for vault lookup."""
    inner = token_full.strip("{}")
    standardized = inner.strip().upper().replace(" ", "_").replace("-", "_")
    return f"{{{{{standardized}}}}}"


def _resolve_token_value(
    token_key: str,
    vault: PIIVault,
    normalizer: Normalizer | None,
    context: str | None = None,
) -> str | None:
    if normalizer is not None:
        try:
            resolved = normalizer.resolve(token_key, context=context)
            if resolved:
                return resolved
        except Exception:
            logger.debug("Normalizer failed for %s", token_key, exc_info=True)
    return vault.get_token(token_key)


def _token_level(token_key: str, vault: PIIVault) -> str:
    return _normalize_level(
        vault.get_token_category(token_key) or FieldClassifier.classify_token(token_key)
    )


def _field_level(field: dict[str, Any]) -> str:
    explicit = field.get("pii_level")
    if explicit:
        return _normalize_level(str(explicit))
    label = str(field.get("label") or "")
    return _normalize_level(FieldClassifier.classify(label))


def _maybe_local_llm_polish(
    text: str,
    router: LLMRouter | None,
    enabled: bool,
) -> str:
    """
    Optional local LLM pass.

    This is intentionally conservative: if anything fails, keep deterministic output.
    """
    if not enabled or not text.strip():
        return text
    if router is None:
        router = LLMRouter()

    system_prompt = (
        "You are a local PII injector post-processor. Preserve meaning exactly. "
        "Do not add facts. Return plain text only."
    )
    user_prompt = json.dumps(
        {
            "instruction": (
                "Return the text exactly as valid final text. "
                "Do not include markdown fences."
            ),
            "text": text,
        }
    )
    try:
        polished = router.route(
            task_type="pii_injection",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        polished = (polished or "").strip()
        return polished or text
    except Exception:
        logger.warning("Local LLM post-processing failed; using deterministic text.")
        return text


def inject_pii_text(
    tokenized_text: str,
    vault: PIIVault | None = None,
    normalizer: Normalizer | None = None,
    context_overrides: dict[str, str] | None = None,
    default_context: str | None = None,
    allow_high_sensitivity: bool = False,
    use_local_llm: bool = False,
    router: LLMRouter | None = None,
) -> dict[str, Any]:
    """
    Resolve tokens in free-form text.

    Behavior by effective sensitivity:
    - LOW: auto-inject
    - MEDIUM: auto-inject + notify
    - HIGH: block by default (leave token unresolved)
    """
    vault = vault or PIIVault()
    normalizer = normalizer or Normalizer(vault)
    context_overrides = context_overrides or {}

    resolved_text = tokenized_text or ""
    blocked_tokens: list[str] = []
    unresolved_tokens: list[str] = []
    medium_sensitivity_tokens: list[str] = []
    injected_tokens: list[str] = []

    for token_key in _extract_tokens(resolved_text):
        # Standardize key for vault lookup
        standard_key = _standardize_token_key(token_key)
        sensitivity = _token_level(standard_key, vault)
        context = context_overrides.get(standard_key, default_context)

        if sensitivity == "HIGH" and not allow_high_sensitivity:
            blocked_tokens.append(standard_key)
            continue

        value = _resolve_token_value(
            token_key=standard_key,
            vault=vault,
            normalizer=normalizer,
            context=context,
        )
        if value is None:
            unresolved_tokens.append(standard_key)
            continue

        resolved_text = resolved_text.replace(token_key, value)
        injected_tokens.append(standard_key)
        if sensitivity == "MEDIUM":
            medium_sensitivity_tokens.append(standard_key)

    resolved_text = _maybe_local_llm_polish(
        text=resolved_text,
        router=router,
        enabled=use_local_llm,
    )

    return {
        "resolved_text": resolved_text,
        "injected_tokens": injected_tokens,
        "medium_sensitivity_tokens": list(dict.fromkeys(medium_sensitivity_tokens)),
        "blocked_tokens": blocked_tokens,
        "unresolved_tokens": unresolved_tokens,
        "needs_human_review": bool(blocked_tokens),
    }


def inject_pii_fill_plan(
    fill_plan: dict[str, Any],
    vault: PIIVault | None = None,
    normalizer: Normalizer | None = None,
    allow_high_sensitivity: bool = False,
) -> dict[str, Any]:
    """
    Resolve token values in fill plan field values.

    Honors field-level normalization context and sensitivity:
    - HIGH fields are blocked by default and escalated.
    """
    vault = vault or PIIVault()
    normalizer = normalizer or Normalizer(vault)

    output_plan = copy.deepcopy(fill_plan or {})
    fields = list(output_plan.get("fields", []))

    escalations = list(output_plan.get("escalations", []))
    notifications: list[dict[str, Any]] = []
    blocked_fields: list[str] = []
    unresolved_fields: list[str] = []
    injected_fields: list[str] = []

    for field in fields:
        if not isinstance(field, dict):
            continue

        field_id = str(field.get("field_id") or "")
        value = field.get("value")
        if not isinstance(value, str):
            continue

        field_tokens = _extract_tokens(value)
        if not field_tokens:
            continue

        field_context = str(field.get("normalization_context") or "").strip() or None
        field_sensitivity = _field_level(field)
        mutated_value = value

        field_blocked = False
        field_unresolved = False
        medium_notified = False

        for token_full in field_tokens:
            standard_key = _standardize_token_key(token_full)
            token_sensitivity = _token_level(standard_key, vault)
            effective_sensitivity = _max_level(field_sensitivity, token_sensitivity)

            if effective_sensitivity == "HIGH" and not allow_high_sensitivity:
                field_blocked = True
                continue

            resolved = _resolve_token_value(
                token_key=standard_key,
                vault=vault,
                normalizer=normalizer,
                context=field_context,
            )
            if resolved is None:
                field_unresolved = True
                continue

            mutated_value = mutated_value.replace(token_full, resolved)
            if effective_sensitivity == "MEDIUM":
                medium_notified = True

        field["value"] = mutated_value

        if field_blocked:
            blocked_fields.append(field_id)
            escalations.append(
                {
                    "field_id": field_id,
                    "reason": "HIGH sensitivity field requires manual approval",
                    "priority": "BLOCKING",
                    "label": field.get("label"),
                }
            )
        if field_unresolved:
            unresolved_fields.append(field_id)
            escalations.append(
                {
                    "field_id": field_id,
                    "reason": "PII token could not be resolved from local vault",
                    "priority": "IMPORTANT",
                    "label": field.get("label"),
                }
            )
        if medium_notified:
            notifications.append(
                {
                    "field_id": field_id,
                    "level": "MEDIUM",
                    "message": "Auto-filled medium-sensitivity field",
                }
            )
        if not field_blocked and not field_unresolved:
            injected_fields.append(field_id)

    # Deduplicate escalations
    seen = set()
    deduped_escalations: list[dict[str, Any]] = []
    for esc in escalations:
        key = (esc.get("field_id"), esc.get("reason"), esc.get("priority"))
        if key in seen:
            continue
        seen.add(key)
        deduped_escalations.append(esc)
    output_plan["escalations"] = deduped_escalations
    output_plan["fields"] = fields

    return {
        "fill_plan": output_plan,
        "injected_fields": injected_fields,
        "blocked_fields": blocked_fields,
        "unresolved_fields": unresolved_fields,
        "notifications": notifications,
        "needs_human_review": bool(blocked_fields),
    }


def inject_application_artifacts(
    tailored_resume_tokenized: str | None = None,
    cover_letter_tokenized: str | None = None,
    fill_plan: dict[str, Any] | None = None,
    vault: PIIVault | None = None,
    normalizer: Normalizer | None = None,
    allow_high_sensitivity: bool = False,
    use_local_llm: bool = False,
    router: LLMRouter | None = None,
) -> dict[str, Any]:
    """
    Inject PII for all application artifacts in one call.
    """
    vault = vault or PIIVault()
    normalizer = normalizer or Normalizer(vault)

    resume_result = inject_pii_text(
        tokenized_text=tailored_resume_tokenized or "",
        vault=vault,
        normalizer=normalizer,
        allow_high_sensitivity=allow_high_sensitivity,
        use_local_llm=use_local_llm,
        router=router,
    )
    cover_result = inject_pii_text(
        tokenized_text=cover_letter_tokenized or "",
        vault=vault,
        normalizer=normalizer,
        allow_high_sensitivity=allow_high_sensitivity,
        use_local_llm=use_local_llm,
        router=router,
    )
    fill_result = inject_pii_fill_plan(
        fill_plan=fill_plan or {"fields": [], "escalations": []},
        vault=vault,
        normalizer=normalizer,
        allow_high_sensitivity=allow_high_sensitivity,
    )

    blocked_tokens = list(
        dict.fromkeys(
            [*resume_result["blocked_tokens"], *cover_result["blocked_tokens"]]
        )
    )
    unresolved_tokens = list(
        dict.fromkeys(
            [*resume_result["unresolved_tokens"], *cover_result["unresolved_tokens"]]
        )
    )

    return {
        "tailored_resume_final": resume_result["resolved_text"],
        "cover_letter_final": cover_result["resolved_text"],
        "fill_plan_final": fill_result["fill_plan"],
        "medium_notifications": [
            *[
                {"token_key": token, "level": "MEDIUM"}
                for token in resume_result["medium_sensitivity_tokens"]
            ],
            *[
                {"token_key": token, "level": "MEDIUM"}
                for token in cover_result["medium_sensitivity_tokens"]
            ],
            *fill_result["notifications"],
        ],
        "blocked_tokens": blocked_tokens,
        "blocked_fields": fill_result["blocked_fields"],
        "unresolved_tokens": unresolved_tokens,
        "unresolved_fields": fill_result["unresolved_fields"],
        "needs_human_review": bool(
            blocked_tokens or fill_result["blocked_fields"] or fill_result["needs_human_review"]
        ),
    }

