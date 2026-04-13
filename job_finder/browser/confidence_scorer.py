"""
Hybrid confidence scoring for ATS form interpretation.

Formula (from implementation plan §8.1):
confidence = selector_match_score * 0.4
           + label_similarity_score * 0.3
           + template_match_score * 0.3
"""

from __future__ import annotations

import re
from dataclasses import dataclass


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class ConfidenceResult:
    confidence: float
    band: str
    selector_match: float
    label_similarity: float
    template_match: float


class ConfidenceScorer:
    """Rule-based confidence scorer for fill-plan fields."""

    SELECTOR_WEIGHT = 0.4
    LABEL_WEIGHT = 0.3
    TEMPLATE_WEIGHT = 0.3

    AUTO_FILL_THRESHOLD = 0.8
    FLAG_THRESHOLD = 0.5

    STRATEGY_SCORES = {
        "exact_css": 1.0,
        "template_assumed": 0.35,
        "label_based_xpath": 0.65,
        "aria_label_match": 0.6,
        "placeholder_text_match": 0.5,
        "spatial_proximity_match": 0.45,
        "none": 0.0,
    }

    @staticmethod
    def label_similarity(expected_label: str, actual_label: str) -> float:
        """
        Compute lexical similarity in [0, 1] using token overlap.
        """
        expected_tokens = _tokenize(expected_label)
        actual_tokens = _tokenize(actual_label)
        if not expected_tokens or not actual_tokens:
            return 0.0
        overlap = len(expected_tokens & actual_tokens)
        union = len(expected_tokens | actual_tokens)
        if union == 0:
            return 0.0
        return _clamp(overlap / union)

    @classmethod
    def selector_match_score(cls, strategy: str) -> float:
        """
        Map resolution strategy to selector match score.
        """
        return cls.STRATEGY_SCORES.get(strategy, 0.0)

    @classmethod
    def compute_confidence(
        cls,
        selector_match_score: float,
        label_similarity_score: float,
        template_match_score: float,
    ) -> float:
        """
        Compute weighted confidence in [0, 1].
        """
        raw = (
            _clamp(selector_match_score) * cls.SELECTOR_WEIGHT
            + _clamp(label_similarity_score) * cls.LABEL_WEIGHT
            + _clamp(template_match_score) * cls.TEMPLATE_WEIGHT
        )
        return round(_clamp(raw), 3)

    @classmethod
    def confidence_band(cls, confidence: float) -> str:
        """
        Convert confidence score to action band.
        """
        if confidence >= cls.AUTO_FILL_THRESHOLD:
            return "AUTO_FILL"
        if confidence >= cls.FLAG_THRESHOLD:
            return "FLAG"
        return "ESCALATE"

    @classmethod
    def score(
        cls,
        strategy: str,
        expected_label: str,
        actual_label: str,
        in_template: bool,
    ) -> ConfidenceResult:
        """
        Full scoring helper from strategy + label context.
        """
        selector_score = cls.selector_match_score(strategy)
        label_score = cls.label_similarity(expected_label, actual_label)
        template_score = 1.0 if in_template else 0.0
        confidence = cls.compute_confidence(
            selector_match_score=selector_score,
            label_similarity_score=label_score,
            template_match_score=template_score,
        )
        return ConfidenceResult(
            confidence=confidence,
            band=cls.confidence_band(confidence),
            selector_match=round(selector_score, 3),
            label_similarity=round(label_score, 3),
            template_match=round(template_score, 3),
        )
