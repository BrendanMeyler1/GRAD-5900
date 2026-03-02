"""
test_debate_judge.py — Unit and integration tests for Debate Judge.

Run with:  python -m pytest test_debate_judge.py -v
       or: python test_debate_judge.py
"""

import sys
import os
import unittest

# Ensure project root is on the path when running directly
sys.path.insert(0, os.path.dirname(__file__))

import router
from scoring import Scorer
from mocks import MockClaimExtractor, MockClaimVerifier, MockFallacyDetector


class TestRouter(unittest.TestCase):
    def test_verifiable_types(self):
        self.assertTrue(router.should_verify({"type": "FACTUAL"}))
        self.assertTrue(router.should_verify({"type": "STATISTICAL"}))
        self.assertTrue(router.should_verify({"type": "CAUSAL"}))

    def test_non_verifiable_types(self):
        self.assertFalse(router.should_verify({"type": "VALUE"}))
        self.assertFalse(router.should_verify({"type": "RHETORICAL"}))

    def test_model_selection(self):
        """Complex claim types should use gpt-4o; simple FACTUAL gets gpt-4o-mini."""
        self.assertEqual(router.select_model({"type": "CAUSAL"}), "gpt-4o")
        self.assertEqual(router.select_model({"type": "STATISTICAL"}), "gpt-4o")
        self.assertEqual(router.select_model({"type": "FACTUAL"}), "gpt-4o-mini")


class TestScoring(unittest.TestCase):
    def test_basic_scoring(self):
        """A: -2 (contradicted) -3 (fallacy) = -5 | B: +2 (supported) +1 (citation) = 3"""
        claims = [
            {"speaker": "A", "verification_status": "CONTRADICTED", "has_citation": False},
            {"speaker": "B", "verification_status": "SUPPORTED",    "has_citation": True},
        ]
        fallacies = [{"speaker": "A", "fallacy_name": "Ad Hominem"}]

        scorer = Scorer()
        results = scorer.calculate_scores(claims, fallacies)

        self.assertEqual(results["scores"]["A"], -5)
        self.assertEqual(results["scores"]["B"], 3)

    def test_insufficient_penalty(self):
        """INSUFFICIENT should cost -1 point (not 0)."""
        claims = [{"speaker": "A", "verification_status": "INSUFFICIENT", "has_citation": False}]
        results = Scorer().calculate_scores(claims, [])
        self.assertEqual(results["scores"]["A"], -1)


class TestIntegration(unittest.TestCase):
    def test_full_pipeline(self):
        """End-to-end flow using mocks — no network or API calls required."""
        extractor = MockClaimExtractor()
        verifier = MockClaimVerifier()
        detector = MockFallacyDetector()
        scorer = Scorer()

        # Stage 1: Extract
        claims = extractor.extract_claims("Dummy text")
        self.assertEqual(len(claims), 3)

        # Stage 2: Route + Verify
        verified_claims = []
        for claim in claims:
            if router.should_verify(claim):
                result = verifier.verify_claim(claim)
                claim["verification_status"] = result["status"]
            else:
                claim["verification_status"] = "UNVERIFIED"
            verified_claims.append(claim)

        # Stage 3: Fallacy Detection
        fallacies = detector.detect_fallacies("Dummy text")

        # Stage 4: Score
        score_result = scorer.calculate_scores(verified_claims, fallacies)
        scores = score_result["scores"]

        # A: CONTRADICTED (-2) + RHETORICAL (UNVERIFIED, 0) + Fallacy (-3) = -5
        # B: SUPPORTED (+2) + Citation (+1) = +3
        self.assertEqual(scores["A"], -5)
        self.assertEqual(scores["B"], 3)


if __name__ == "__main__":
    unittest.main()
