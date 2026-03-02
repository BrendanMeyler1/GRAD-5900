"""
mocks.py — Reusable mock classes for unit tests.

These stubs replace OpenAI/network-dependent components so tests run
offline without API keys.
"""


class MockClaimExtractor:
    def __init__(self, api_key=None):
        pass

    def extract_claims(self, text: str) -> list[dict]:
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

    def explain_result(self, scores, details, verified_claims, fallacies, winner):
        return f"[Mock explanation] Winner: Speaker {winner}"


class MockClaimVerifier:
    def verify_claim(self, claim: dict) -> dict:
        if claim["text"] == "Crime has increased every year.":
            return {
                "claim": claim["text"],
                "status": "CONTRADICTED",
                "reasoning": "Data shows decrease.",
                "evidence_source": "Wikipedia (Crime in the US)"
            }
        if claim["text"] == "FBI statistics show crime decreased in 2022.":
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
    def detect_fallacies(self, text: str) -> list[dict]:
        return [
            {
                "speaker": "A",
                "fallacy_name": "Ad Hominem",
                "quote": "you clearly don't understand economics",
                "explanation": "Personal attack."
            }
        ]
