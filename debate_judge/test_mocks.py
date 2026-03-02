import sys
import unittest
from unittest.mock import MagicMock, patch
import json
import os

# Ensure we can import from the directory
sys.path.append(os.getcwd())

from router import ComplexityRouter
from scoring import Scorer

# Mock classes for components dependent on OpenAI/Network
class MockClaimExtractor:
    def __init__(self, api_key=None):
        pass
    
    def extract_claims(self, text):
        # Return dummy claims based on the example in README
        return [
            {
                "speaker": "A",
                "text": "Crime has increased every year.",
                "type": "STATISTICAL",
                "has_citation": False
            },
            {
                "speaker": "B",
                "text": "FBI statistics show crime decreased in 2022.",
                "type": "FACTUAL",
                "has_citation": True
            },
             {
                "speaker": "A",
                "text": "Those statistics are unreliable.",
                "type": "RHETORICAL",
                "has_citation": False
            }
        ]

class MockClaimVerifier:
    def verify_claim(self, claim):
        if claim["text"] == "Crime has increased every year.":
            return {
                "claim": claim["text"],
                "status": "CONTRADICTED",
                "reasoning": "Data shows decrease.",
                "evidence_source": "Wikipedia (Crime in the US)"
            }
        elif claim["text"] == "FBI statistics show crime decreased in 2022.":
            return {
                "claim": claim["text"],
                "status": "SUPPORTED",
                "reasoning": "FBI data confirms this.",
                "evidence_source": "Wikipedia (FBI Crime Statistics)"
            }
        return {
            "claim": claim["text"],
            "status": "INSUFFICIENT",
            "reasoning": "No evidence found.",
            "evidence_source": "None"
        }

class MockFallacyDetector:
    def detect_fallacies(self, text):
        return [
            {
                "speaker": "A",
                "fallacy_name": "Ad Hominem",
                "quote": "you clearly don’t understand economics",
                "explanation": "Personal attack."
            }
        ]

class TestDebateJudge(unittest.TestCase):
    def test_router(self):
        print("\nTesting Router...")
        self.assertTrue(ComplexityRouter.should_verify({"type": "FACTUAL"}))
        self.assertTrue(ComplexityRouter.should_verify({"type": "STATISTICAL"}))
        self.assertFalse(ComplexityRouter.should_verify({"type": "VALUE"}))
        print("Router OK")

    def test_scoring(self):
        print("\nTesting Scoring Logic...")
        claims = [
            {"speaker": "A", "verification_status": "CONTRADICTED", "has_citation": False},
            {"speaker": "B", "verification_status": "SUPPORTED", "has_citation": True}, # +2 supported +1 citation = 3
        ]
        fallacies = [
            {"speaker": "A", "fallacy_name": "Ad Hominem"} # -3
        ]
        
        scorer = Scorer()
        results = scorer.calculate_scores(claims, fallacies)
        
        print("Scores:", results["scores"])
        
        # A: -2 (contradicted) - 3 (fallacy) = -5
        self.assertEqual(results["scores"]["A"], -5)
        
        # B: +2 (supported) + 1 (citation) = 3
        self.assertEqual(results["scores"]["B"], 3)
        print("Scoring OK")

    def test_integration(self):
        print("\nTesting Integration Flow...")
        extractor = MockClaimExtractor()
        verifier = MockClaimVerifier()
        detector = MockFallacyDetector()
        scorer = Scorer()

        # 1. Extract
        text = "Dummy text"
        claims = extractor.extract_claims(text)
        self.assertEqual(len(claims), 3)

        # 2. Verify
        verified_claims = []
        for claim in claims:
            if ComplexityRouter.should_verify(claim):
                result = verifier.verify_claim(claim)
                claim["verification_status"] = result["status"]
            else:
                claim["verification_status"] = "UNVERIFIED"
            verified_claims.append(claim)

        # 3. Detect Fallacies
        fallacies = detector.detect_fallacies(text)

        # 4. Score
        score_result = scorer.calculate_scores(verified_claims, fallacies)
        scores = score_result["scores"]
        
        print("Integration Scores:", scores)
        # Expected A: 
        #   Claim 1: "Crime has increased..." (STATISTICAL) -> Verifies -> CONTRADICTED (-2)
        #   Claim 3: "Those statistics..." (RHETORICAL) -> Skip -> 0
        #   Fallacy: Ad Hominem (-3)
        #   Total A: -5
        
        # Expected B:
        #   Claim 2: "FBI statistics..." (FACTUAL) -> Verifies -> SUPPORTED (+2) + Citation (+1) = +3
        #   Total B: 3
        
        self.assertEqual(scores["A"], -5)
        self.assertEqual(scores["B"], 3)
        print("Integration OK")

if __name__ == "__main__":
    unittest.main()
