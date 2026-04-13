"""
Multi-strategy selector resolver for ATS field targeting.

Fallback chain (implementation plan §8.2):
1. exact_css
2. label_based_xpath
3. aria_label_match
4. placeholder_text_match
5. spatial_proximity_match
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _similarity(a: str, b: str) -> float:
    ta = _tokenize(a)
    tb = _tokenize(b)
    if not ta or not tb:
        return 0.0
    overlap = len(ta & tb)
    union = len(ta | tb)
    return overlap / union if union else 0.0


@dataclass
class DOMField:
    """Normalized representation of a parsed form field."""

    tag: str
    input_type: str
    selector: str
    label: str
    id_attr: str | None = None
    name_attr: str | None = None
    aria_label: str | None = None
    placeholder: str | None = None
    index: int = 0


@dataclass
class SelectorResolution:
    """Resolution result for one target field."""

    selector: str | None
    strategy: str
    strategies_tried: list[str] = field(default_factory=list)
    matched_field: DOMField | None = None


class SelectorResolver:
    """Resolves selectors using a fallback strategy chain."""

    DEFAULT_CHAIN = [
        "exact_css",
        "label_based_xpath",
        "aria_label_match",
        "placeholder_text_match",
        "spatial_proximity_match",
    ]

    def resolve(
        self,
        expected_selector: str | None,
        expected_label: str,
        expected_type: str | None,
        dom_fields: list[DOMField],
        fallback_chain: list[str] | None = None,
    ) -> SelectorResolution:
        chain = fallback_chain or self.DEFAULT_CHAIN
        tried: list[str] = []
        expected_type = (expected_type or "").strip().lower()

        for strategy in chain:
            tried.append(strategy)
            if strategy == "exact_css":
                match = self._exact_css(expected_selector, dom_fields)
                if match:
                    return SelectorResolution(
                        selector=match.selector,
                        strategy="exact_css",
                        strategies_tried=tried,
                        matched_field=match,
                    )
            elif strategy == "label_based_xpath":
                match = self._best_label_match(expected_label, dom_fields, min_score=0.72)
                if match:
                    return SelectorResolution(
                        selector=self._to_xpath(match),
                        strategy="label_based_xpath",
                        strategies_tried=tried,
                        matched_field=match,
                    )
            elif strategy == "aria_label_match":
                match = self._best_attr_match(expected_label, dom_fields, attr="aria_label", min_score=0.65)
                if match:
                    return SelectorResolution(
                        selector=match.selector,
                        strategy="aria_label_match",
                        strategies_tried=tried,
                        matched_field=match,
                    )
            elif strategy == "placeholder_text_match":
                match = self._best_attr_match(expected_label, dom_fields, attr="placeholder", min_score=0.6)
                if match:
                    return SelectorResolution(
                        selector=match.selector,
                        strategy="placeholder_text_match",
                        strategies_tried=tried,
                        matched_field=match,
                    )
            elif strategy == "spatial_proximity_match":
                match = self._spatial_proximity_match(
                    expected_label=expected_label,
                    expected_type=expected_type,
                    dom_fields=dom_fields,
                )
                if match:
                    return SelectorResolution(
                        selector=match.selector,
                        strategy="spatial_proximity_match",
                        strategies_tried=tried,
                        matched_field=match,
                    )

        return SelectorResolution(
            selector=None,
            strategy="none",
            strategies_tried=tried,
            matched_field=None,
        )

    @staticmethod
    def _exact_css(expected_selector: str | None, dom_fields: list[DOMField]) -> DOMField | None:
        if not expected_selector:
            return None
        normalized = expected_selector.strip()
        for field in dom_fields:
            if field.selector == normalized:
                return field

            if normalized.startswith("#") and field.id_attr:
                if normalized == f"#{field.id_attr}":
                    return field

            name_match = re.match(r'^\[name="([^"]+)"\]$', normalized)
            if name_match and field.name_attr == name_match.group(1):
                return field

            if normalized.startswith("input[type=\"file\"]") and field.tag == "input":
                if field.input_type == "file":
                    if "[name=\"" not in normalized:
                        return field
                    name_match = re.search(r'\[name="([^"]+)"\]', normalized)
                    if name_match and field.name_attr == name_match.group(1):
                        return field

        return None

    @staticmethod
    def _best_label_match(expected_label: str, dom_fields: list[DOMField], min_score: float) -> DOMField | None:
        best: tuple[DOMField | None, float] = (None, 0.0)
        for field in dom_fields:
            score = _similarity(expected_label, field.label)
            if score > best[1]:
                best = (field, score)
        if best[0] is not None and best[1] >= min_score:
            return best[0]
        return None

    @staticmethod
    def _best_attr_match(
        expected_label: str,
        dom_fields: list[DOMField],
        attr: str,
        min_score: float,
    ) -> DOMField | None:
        best: tuple[DOMField | None, float] = (None, 0.0)
        for field in dom_fields:
            candidate = getattr(field, attr) or ""
            score = _similarity(expected_label, candidate)
            if score > best[1]:
                best = (field, score)
        if best[0] is not None and best[1] >= min_score:
            return best[0]
        return None

    @staticmethod
    def _spatial_proximity_match(
        expected_label: str,
        expected_type: str,
        dom_fields: list[DOMField],
    ) -> DOMField | None:
        """
        Heuristic: pick field with nearest label similarity, preferring type match.
        """
        best_field: DOMField | None = None
        best_score = -1.0
        for field in dom_fields:
            sim = _similarity(expected_label, field.label)
            type_bonus = 0.08 if expected_type and expected_type == field.input_type else 0.0
            score = sim + type_bonus
            if score > best_score:
                best_field = field
                best_score = score
        if best_field is None:
            return None
        return best_field if best_score >= 0.25 else None

    @staticmethod
    def _to_xpath(field: DOMField) -> str:
        if field.id_attr:
            return f"//*[@id='{field.id_attr}']"
        if field.name_attr:
            return f"//*[@name='{field.name_attr}']"
        return field.selector
