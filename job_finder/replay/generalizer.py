"""
Replay trace generalizer.

Transforms brittle raw traces into reusable semantic descriptors so future runs
can remap fields by meaning (label/type/position) instead of hard-coded selectors.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from feedback.company_memory_store import CompanyMemoryStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str | None) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in (value or "").strip())
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_") or "unknown"


def _tokens(text: str | None) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", str(text or "").lower()))


def _similarity(a: str | None, b: str | None) -> float:
    ta = _tokens(a)
    tb = _tokens(b)
    if not ta or not tb:
        return 0.0
    overlap = len(ta & tb)
    union = len(ta | tb)
    return overlap / union if union else 0.0


def _position_label(index: int, total: int) -> str:
    if total <= 1:
        return "top_form"
    ratio = index / max(1, total - 1)
    if ratio < 0.34:
        return "top_form"
    if ratio < 0.67:
        return "middle_form"
    return "bottom_form"


def _confidence_band(value: float | None) -> str:
    score = float(value or 0.0)
    if score >= 0.8:
        return "AUTO_FILL"
    if score >= 0.5:
        return "FLAG"
    return "ESCALATE"


class ReplayGeneralizer:
    """Persists raw traces and produces semantic generalized traces."""

    def __init__(
        self,
        traces_dir: str = "replay/traces",
        company_memory_db_path: str = "feedback/company_memory.db",
        company_store: CompanyMemoryStore | None = None,
    ) -> None:
        self.traces_dir = Path(traces_dir)
        self.raw_dir = self.traces_dir / "raw"
        self.generalized_dir = self.traces_dir / "generalized"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.generalized_dir.mkdir(parents=True, exist_ok=True)
        self.company_store = company_store or CompanyMemoryStore(db_path=company_memory_db_path)

    def create_trace_id(
        self,
        company_name: str | None = None,
        ats_type: str | None = None,
        listing_id: str | None = None,
    ) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"{_slug(company_name)}_{_slug(ats_type)}_{_slug(listing_id)}_{stamp}_{uuid4().hex[:8]}"

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8")

    def save_raw_trace(
        self,
        trace: dict[str, Any],
        trace_id: str | None = None,
        company_name: str | None = None,
        ats_type: str | None = None,
        listing_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist one raw trace JSON file."""
        trace_copy = dict(trace or {})
        listing = trace_copy.get("listing", {}) if isinstance(trace_copy.get("listing"), dict) else {}
        company = (
            company_name
            or (
                listing.get("company", {}).get("name")
                if isinstance(listing.get("company"), dict)
                else listing.get("company")
            )
            or "unknown_company"
        )
        ats = ats_type or listing.get("ats_type") or trace_copy.get("ats_type") or "unknown"
        lid = listing_id or listing.get("listing_id") or trace_copy.get("listing_id") or "unknown_listing"
        tid = trace_id or trace_copy.get("trace_id") or self.create_trace_id(
            company_name=str(company),
            ats_type=str(ats),
            listing_id=str(lid),
        )
        trace_copy["trace_id"] = tid
        trace_copy.setdefault("captured_at", _utc_now())
        path = self.raw_dir / f"{tid}.json"
        self._write_json(path, trace_copy)

        try:
            self.company_store.add_replay_ref(
                company_name=str(company),
                trace_id=tid,
                ats_type=str(ats),
            )
        except Exception:
            # Keep trace persistence non-blocking if company memory is unavailable.
            pass

        return {"trace_id": tid, "path": str(path)}

    def _build_descriptors(self, trace: dict[str, Any]) -> list[dict[str, Any]]:
        fill_plan = trace.get("fill_plan", {}) if isinstance(trace.get("fill_plan"), dict) else {}
        fields = list(fill_plan.get("fields", []) or [])
        execution = trace.get("execution", {}) if isinstance(trace.get("execution"), dict) else {}
        executed_actions = list(execution.get("executed_actions", []) or [])
        dom_snapshot = trace.get("dom_snapshot", {}) if isinstance(trace.get("dom_snapshot"), dict) else {}
        dom_fields = list(dom_snapshot.get("fields", []) or [])

        action_by_field: dict[str, dict[str, Any]] = {}
        for action in executed_actions:
            if not isinstance(action, dict):
                continue
            field_id = str(action.get("field_id", "")).strip()
            if field_id:
                action_by_field[field_id] = action

        dom_by_selector: dict[str, dict[str, Any]] = {}
        for dom in dom_fields:
            if not isinstance(dom, dict):
                continue
            selector = str(dom.get("selector", "")).strip()
            if selector:
                dom_by_selector[selector] = dom

        descriptors: list[dict[str, Any]] = []
        total = max(1, len(fields))
        seen_field_ids: set[str] = set()

        for index, field in enumerate(fields):
            if not isinstance(field, dict):
                continue
            field_id = str(field.get("field_id", "")).strip()
            if not field_id:
                continue
            seen_field_ids.add(field_id)

            action = action_by_field.get(field_id, {})
            selector = (
                str(action.get("selector") or "").strip()
                or str(field.get("selector") or "").strip()
                or None
            )
            dom = dom_by_selector.get(selector or "", {}) if selector else {}

            label = (
                str(field.get("label") or "").strip()
                or str(dom.get("label") or "").strip()
                or field_id
            )
            field_type = str(field.get("type") or "").strip() or str(dom.get("input_type") or "text_input")
            strategy = (
                str(action.get("strategy_used") or "").strip()
                or str(field.get("selector_strategy") or "").strip()
                or "unknown"
            )
            confidence = (
                action.get("confidence")
                if action.get("confidence") is not None
                else field.get("confidence")
            )

            descriptors.append(
                {
                    "field_id": field_id,
                    "label": label,
                    "type": field_type,
                    "relative_position": _position_label(index=index, total=total),
                    "aria_label": dom.get("aria_label") or dom.get("label"),
                    "placeholder": dom.get("placeholder"),
                    "selector_that_worked": selector,
                    "strategy_used": strategy,
                    "confidence": confidence,
                    "confidence_band": _confidence_band(confidence),
                    "pii_level": field.get("pii_level"),
                    "source": field.get("source"),
                }
            )

        # Include execution-only fields not present in fill plan.
        for field_id, action in action_by_field.items():
            if field_id in seen_field_ids:
                continue
            selector = str(action.get("selector") or "").strip() or None
            dom = dom_by_selector.get(selector or "", {}) if selector else {}
            label = str(dom.get("label") or action.get("field_id") or field_id)
            field_type = str(action.get("action") or "unknown")
            confidence = action.get("confidence")
            descriptors.append(
                {
                    "field_id": field_id,
                    "label": label,
                    "type": field_type,
                    "relative_position": "unknown",
                    "aria_label": dom.get("aria_label") or dom.get("label"),
                    "placeholder": dom.get("placeholder"),
                    "selector_that_worked": selector,
                    "strategy_used": str(action.get("strategy_used") or "unknown"),
                    "confidence": confidence,
                    "confidence_band": _confidence_band(confidence),
                    "pii_level": None,
                    "source": "execution_only",
                }
            )

        return descriptors

    def generalize_trace(
        self,
        trace: dict[str, Any],
        trace_id: str | None = None,
        save: bool = True,
    ) -> dict[str, Any]:
        """
        Generalize one raw trace into semantic descriptors.
        """
        raw = dict(trace or {})
        listing = raw.get("listing", {}) if isinstance(raw.get("listing"), dict) else {}
        company = (
            listing.get("company", {}).get("name")
            if isinstance(listing.get("company"), dict)
            else listing.get("company")
        ) or raw.get("company") or "unknown_company"
        ats_type = raw.get("ats_type") or listing.get("ats_type") or "unknown"
        listing_id = raw.get("listing_id") or listing.get("listing_id")

        source_trace_id = str(raw.get("trace_id") or "")
        effective_trace_id = trace_id or source_trace_id or self.create_trace_id(
            company_name=str(company),
            ats_type=str(ats_type),
            listing_id=str(listing_id or "unknown_listing"),
        )

        descriptors = self._build_descriptors(raw)
        strategy_counts = Counter(
            str(item.get("strategy_used") or "unknown") for item in descriptors
        )

        generalized = {
            "trace_id": effective_trace_id,
            "source_trace_id": source_trace_id or None,
            "company": company,
            "ats_type": ats_type,
            "listing_id": listing_id,
            "descriptor_count": len(descriptors),
            "strategy_stats": dict(strategy_counts),
            "descriptors": descriptors,
            "generated_at": _utc_now(),
        }

        if save:
            path = self.generalized_dir / f"{effective_trace_id}.json"
            self._write_json(path, generalized)

            try:
                self.company_store.add_replay_ref(
                    company_name=str(company),
                    trace_id=effective_trace_id,
                    ats_type=str(ats_type),
                )
            except Exception:
                pass

        return generalized

    def generalize_trace_file(self, trace_path: str, save: bool = True) -> dict[str, Any]:
        """Load one raw trace file and generalize it."""
        path = Path(trace_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        return self.generalize_trace(payload, trace_id=payload.get("trace_id"), save=save)

    def load_generalized_trace(self, trace_id: str) -> dict[str, Any]:
        """Load a generalized trace by trace_id."""
        path = self.generalized_dir / f"{trace_id}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def remap_to_dom(
        self,
        generalized_trace: dict[str, Any],
        dom_fields: list[dict[str, Any]],
        min_score: float = 0.25,
    ) -> dict[str, dict[str, Any]]:
        """
        Remap descriptors to current DOM fields by semantic similarity.

        Returns:
            {
              "field_id": {
                  "selector": "...",
                  "score": 0.82,
                  "matched_label": "...",
                  "strategy": "semantic_remap"
              }
            }
        """
        descriptors = list(generalized_trace.get("descriptors", []) or [])
        candidates = [item for item in dom_fields if isinstance(item, dict)]
        results: dict[str, dict[str, Any]] = {}

        for desc in descriptors:
            if not isinstance(desc, dict):
                continue
            field_id = str(desc.get("field_id", "")).strip()
            if not field_id:
                continue

            best: tuple[dict[str, Any] | None, float] = (None, -1.0)
            for dom in candidates:
                label_score = _similarity(desc.get("label"), dom.get("label"))
                aria_score = _similarity(desc.get("aria_label"), dom.get("aria_label"))
                placeholder_score = _similarity(desc.get("label"), dom.get("placeholder"))
                type_bonus = (
                    0.08
                    if str(desc.get("type", "")).lower() == str(dom.get("input_type", "")).lower()
                    else 0.0
                )
                score = max(label_score, aria_score, placeholder_score) + type_bonus
                if score > best[1]:
                    best = (dom, score)

            matched, score = best
            if matched is None or score < min_score:
                continue
            selector = str(matched.get("selector") or "").strip()
            if not selector:
                continue

            results[field_id] = {
                "selector": selector,
                "score": round(score, 3),
                "matched_label": matched.get("label"),
                "strategy": "semantic_remap",
            }

        return results


def build_submission_trace(
    listing: dict[str, Any],
    fill_plan: dict[str, Any],
    execution: dict[str, Any] | None = None,
    dom_snapshot: dict[str, Any] | None = None,
    application_id: str | None = None,
) -> dict[str, Any]:
    """Construct a normalized raw trace payload from submission artifacts."""
    return {
        "trace_id": str(uuid4()),
        "application_id": application_id,
        "listing_id": listing.get("listing_id"),
        "ats_type": listing.get("ats_type"),
        "company": (
            listing.get("company", {}).get("name")
            if isinstance(listing.get("company"), dict)
            else listing.get("company")
        ),
        "listing": listing,
        "fill_plan": fill_plan,
        "execution": execution or {},
        "dom_snapshot": dom_snapshot or {},
        "captured_at": _utc_now(),
    }

