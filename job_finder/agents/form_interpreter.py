"""
Form Interpreter agent.

Converts ATS form HTML into a structured fill plan with deterministic
confidence scoring and escalation signals.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from browser.confidence_scorer import ConfidenceScorer
from browser.selector_resolver import DOMField, SelectorResolver
from llm_router.router import LLMRouter
from pii.field_classifier import FieldClassifier

logger = logging.getLogger("job_finder.agents.form_interpreter")

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "form_interpreter.md"
TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def _load_prompt() -> str:
    raw = PROMPT_PATH.read_text(encoding="utf-8")
    if "## System Prompt" not in raw:
        return raw.strip()
    start = raw.index("## System Prompt") + len("## System Prompt")
    next_header = raw.find("\n## ", start)
    if next_header == -1:
        return raw[start:].strip()
    return raw[start:next_header].strip()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").strip().lower()).strip("_") or "field"


def _strip_html(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", cleaned).strip()


def _parse_attrs(fragment: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in re.finditer(
        r'([:\w-]+)\s*=\s*(".*?"|\'.*?\'|[^\s>]+)',
        fragment or "",
        flags=re.DOTALL,
    ):
        key = match.group(1).lower()
        value = match.group(2).strip().strip('"').strip("'")
        attrs[key] = value
    return attrs


def _load_template(ats_type: str, template_path: str | Path | None = None) -> dict[str, Any]:
    if template_path:
        path = Path(template_path)
    else:
        path = TEMPLATES_DIR / f"{ats_type}.json"
        if not path.exists():
            path = TEMPLATES_DIR / "greenhouse.json"

    if not path.exists():
        raise FileNotFoundError(f"Template not found for ATS '{ats_type}': {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_dom_fields(form_html: str) -> list[DOMField]:
    """
    Parse input/textarea/select tags and associated labels from raw HTML.
    """
    html = form_html or ""

    label_for_map: dict[str, str] = {}
    orphan_labels: list[tuple[int, str]] = []

    for label_match in re.finditer(r"<label\b([^>]*)>(.*?)</label>", html, flags=re.I | re.S):
        attrs = _parse_attrs(label_match.group(1))
        label_text = _strip_html(label_match.group(2))
        if not label_text:
            continue
        if "for" in attrs and attrs["for"]:
            label_for_map[attrs["for"]] = label_text
        else:
            orphan_labels.append((label_match.start(), label_text))

    raw_fields: list[tuple[int, str, dict[str, str]]] = []

    for input_match in re.finditer(r"<input\b([^>]*)>", html, flags=re.I | re.S):
        raw_fields.append((input_match.start(), "input", _parse_attrs(input_match.group(1))))

    for ta_match in re.finditer(r"<textarea\b([^>]*)>(.*?)</textarea>", html, flags=re.I | re.S):
        attrs = _parse_attrs(ta_match.group(1))
        raw_fields.append((ta_match.start(), "textarea", attrs))

    for select_match in re.finditer(r"<select\b([^>]*)>(.*?)</select>", html, flags=re.I | re.S):
        attrs = _parse_attrs(select_match.group(1))
        raw_fields.append((select_match.start(), "select", attrs))

    raw_fields.sort(key=lambda item: item[0])

    dom_fields: list[DOMField] = []
    for index, (position, tag, attrs) in enumerate(raw_fields, start=1):
        id_attr = attrs.get("id")
        name_attr = attrs.get("name")
        aria = attrs.get("aria-label") or attrs.get("aria_label")
        placeholder = attrs.get("placeholder")

        if tag == "input":
            input_type = (attrs.get("type") or "text").lower()
        elif tag == "textarea":
            input_type = "textarea"
        else:
            input_type = "select"

        if id_attr:
            selector = f"#{id_attr}"
        elif name_attr:
            selector = f'[name="{name_attr}"]'
        else:
            selector = f"{tag}[data-jf-index='{index}']"

        label = ""
        if id_attr and id_attr in label_for_map:
            label = label_for_map[id_attr]
        elif aria:
            label = aria
        elif placeholder:
            label = placeholder
        elif name_attr:
            label = name_attr.replace("_", " ").replace("-", " ").title()

        if not label and orphan_labels:
            nearest = min(orphan_labels, key=lambda item: abs(item[0] - position))
            if abs(nearest[0] - position) < 400:
                label = nearest[1]

        dom_fields.append(
            DOMField(
                tag=tag.lower(),
                input_type=input_type,
                selector=selector,
                label=label,
                id_attr=id_attr,
                name_attr=name_attr,
                aria_label=aria,
                placeholder=placeholder,
                index=index,
            )
        )

    return dom_fields


def _is_question_field(field: DOMField) -> bool:
    label = (field.label or "").strip().lower()
    if not label:
        return False
    question_mark = "?" in label
    question_keywords = any(
        key in label
        for key in ("why", "describe", "tell us", "how do you", "explain", "challenge")
    )
    return field.tag == "textarea" and (question_mark or question_keywords)


def _question_key(label: str) -> str:
    return _slug(label)[:80]


def _field_type_from_dom(field: DOMField) -> str:
    if field.tag == "textarea":
        return "textarea"
    if field.tag == "select":
        return "select"
    if field.input_type == "file":
        return "file_upload"
    return "text_input"


def _normalize_pii_level(label: str, value: str | None, explicit_level: str | None = None) -> str:
    if explicit_level in {"LOW", "MEDIUM", "HIGH", "NONE"}:
        return explicit_level
    if value and value.startswith("{{") and value.endswith("}}"):
        level = FieldClassifier.classify_token(value)
        return level
    return FieldClassifier.classify(label)


def _default_fallback_chain() -> list[str]:
    return [
        "exact_css",
        "label_based_xpath",
        "aria_label_match",
        "placeholder_text_match",
        "spatial_proximity_match",
    ]


def _llm_supplement_fields(
    listing: dict[str, Any],
    form_html: str,
    template_fields: list[dict[str, Any]],
    persona: dict[str, Any] | None,
    router: LLMRouter,
) -> list[dict[str, Any]]:
    """
    Optional LLM supplement for additional fields not captured heuristically.
    """
    system_prompt = _load_prompt()
    payload = {
        "listing": listing,
        "form_html": form_html[:20000],
        "template_fields": template_fields,
        "persona": persona or {},
    }
    response = router.route_json(
        task_type="form_interpretation",
        system_prompt=system_prompt,
        user_prompt=json.dumps(payload, indent=2),
    )
    fields = response.get("fields", [])
    if not isinstance(fields, list):
        return []
    return [field for field in fields if isinstance(field, dict)]


def interpret_form(
    listing: dict[str, Any],
    form_html: str,
    persona: dict[str, Any] | None = None,
    template_path: str | Path | None = None,
    router: LLMRouter | None = None,
    allow_llm_assist: bool = False,
) -> dict[str, Any]:
    """
    Build a fill plan from ATS form HTML.
    """
    ats_type = str(listing.get("ats_type") or "greenhouse").lower()
    template = _load_template(ats_type=ats_type, template_path=template_path)
    template_fields = list(template.get("fields", []))
    replay_trace_id = template.get("replay_trace_id")

    dom_fields = _parse_dom_fields(form_html)
    resolver = SelectorResolver()
    scorer = ConfidenceScorer()

    fill_fields: list[dict[str, Any]] = []
    escalations: list[dict[str, Any]] = []
    consumed_dom_selectors: set[str] = set()
    known_field_ids: set[str] = set()

    for tpl in template_fields:
        field_id = str(tpl.get("field_id", "")).strip()
        if not field_id:
            continue
        known_field_ids.add(field_id)

        expected_label = str(tpl.get("label", field_id)).strip()
        expected_type = str(tpl.get("type", "")).strip().lower()
        expected_selector = tpl.get("selector")
        value = tpl.get("value")
        explicit_level = tpl.get("pii_level")
        fallback_chain = tpl.get("selector_fallback_chain") or _default_fallback_chain()

        resolution = resolver.resolve(
            expected_selector=expected_selector,
            expected_label=expected_label,
            expected_type=expected_type,
            dom_fields=dom_fields,
            fallback_chain=fallback_chain,
        )
        if resolution.selector is None and not dom_fields and expected_selector:
            # In synthetic/shadow runs we may not have live DOM. Keep template selectors
            # as assumed targets so the workflow can proceed to human review.
            resolution.selector = str(expected_selector)
            resolution.strategy = "template_assumed"

        actual_label = expected_label
        if resolution.matched_field and resolution.matched_field.label:
            actual_label = resolution.matched_field.label
            consumed_dom_selectors.add(resolution.matched_field.selector)

        score = scorer.score(
            strategy=resolution.strategy,
            expected_label=expected_label,
            actual_label=actual_label,
            in_template=True,
        )

        pii_level = _normalize_pii_level(
            label=expected_label,
            value=str(value) if value is not None else None,
            explicit_level=str(explicit_level) if explicit_level is not None else None,
        )

        field_entry: dict[str, Any] = {
            "field_id": field_id,
            "label": expected_label,
            "type": expected_type or (resolution.matched_field.input_type if resolution.matched_field else "text_input"),
            "selector": resolution.selector,
            "selector_strategy": resolution.strategy,
            "value": value,
            "pii_level": pii_level,
            "confidence": score.confidence,
            "confidence_breakdown": {
                "selector_match": score.selector_match,
                "label_similarity": score.label_similarity,
                "template_match": score.template_match,
            },
            "source": (
                "template"
                if resolution.strategy == "exact_css"
                else "template_assumed"
                if resolution.strategy == "template_assumed"
                else "template_resolved"
            ),
            "explanation": f"Resolved via {resolution.strategy}",
        }
        if fallback_chain:
            field_entry["selector_fallback_chain"] = list(fallback_chain)
        if tpl.get("normalization_context"):
            field_entry["normalization_context"] = tpl["normalization_context"]
        fill_fields.append(field_entry)

        if resolution.selector is None:
            escalations.append(
                {
                    "field_id": field_id,
                    "reason": "All selector strategies failed",
                    "priority": "BLOCKING",
                    "label": expected_label,
                }
            )
        elif pii_level == "HIGH":
            escalations.append(
                {
                    "field_id": field_id,
                    "reason": "HIGH sensitivity field requires manual approval",
                    "priority": "BLOCKING",
                    "label": expected_label,
                }
            )
        elif score.band == "ESCALATE":
            escalations.append(
                {
                    "field_id": field_id,
                    "reason": "Low confidence field",
                    "priority": "IMPORTANT",
                    "label": expected_label,
                }
            )

    # Heuristic free-text question detection (Question Responder handoff)
    for dom in dom_fields:
        if dom.selector in consumed_dom_selectors:
            continue
        if not _is_question_field(dom):
            continue

        field_id = _question_key(dom.label)
        if field_id in known_field_ids:
            continue
        known_field_ids.add(field_id)

        confidence = scorer.compute_confidence(
            selector_match_score=0.65,
            label_similarity_score=0.9,
            template_match_score=0.0,
        )

        fill_fields.append(
            {
                "field_id": field_id,
                "label": dom.label,
                "type": _field_type_from_dom(dom),
                "selector": dom.selector,
                "selector_strategy": "label_based_xpath",
                "value": f"QUESTION_RESPONDER:{_question_key(dom.label)}",
                "pii_level": "NONE",
                "confidence": confidence,
                "source": "llm_interpreted",
                "explanation": "Free-text question delegated to Question Responder",
                "requires_question_responder": True,
            }
        )

    # Optional LLM supplementation for additional unknown fields
    if allow_llm_assist and router is not None:
        try:
            suggestions = _llm_supplement_fields(
                listing=listing,
                form_html=form_html,
                template_fields=template_fields,
                persona=persona,
                router=router,
            )
            existing_ids = {field["field_id"] for field in fill_fields}
            for suggestion in suggestions:
                suggested_id = str(suggestion.get("field_id", "")).strip()
                if not suggested_id or suggested_id in existing_ids:
                    continue
                fill_fields.append(suggestion)
                existing_ids.add(suggested_id)
        except Exception as exc:
            logger.warning("LLM supplement for form interpreter failed: %s", exc)

    # Deduplicate escalations
    deduped: list[dict[str, Any]] = []
    seen = set()
    for esc in escalations:
        key = (esc.get("field_id"), esc.get("reason"), esc.get("priority"))
        if key not in seen:
            seen.add(key)
            deduped.append(esc)

    return {
        "fill_plan_id": str(uuid4()),
        "listing_id": listing.get("listing_id"),
        "ats_type": ats_type,
        "url": listing.get("apply_url") or listing.get("source_url"),
        "fields": fill_fields,
        "escalations": deduped,
        "replay_trace_used": replay_trace_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
