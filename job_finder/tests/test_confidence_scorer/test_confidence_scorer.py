"""Tests for browser.confidence_scorer."""

from browser.confidence_scorer import ConfidenceScorer


def test_confidence_formula_weights_match_plan():
    confidence = ConfidenceScorer.compute_confidence(
        selector_match_score=1.0,
        label_similarity_score=0.5,
        template_match_score=1.0,
    )
    # 1.0 * 0.4 + 0.5 * 0.3 + 1.0 * 0.3 = 0.85
    assert confidence == 0.85


def test_confidence_bands():
    assert ConfidenceScorer.confidence_band(0.9) == "AUTO_FILL"
    assert ConfidenceScorer.confidence_band(0.65) == "FLAG"
    assert ConfidenceScorer.confidence_band(0.49) == "ESCALATE"


def test_label_similarity_prefers_close_labels():
    close = ConfidenceScorer.label_similarity("First Name", "Candidate First Name")
    far = ConfidenceScorer.label_similarity("First Name", "Expected Salary")
    assert close > far
