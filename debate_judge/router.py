"""
router.py — Complexity Router

Determines whether a claim requires external verification, and which
LLM model is appropriate for analyzing it.

  FACTUAL, STATISTICAL, CAUSAL  → verify  (use stronger model)
  VALUE, RHETORICAL              → skip    (no model needed)
"""

# Claim types that warrant Wikipedia lookup + LLM comparison
_VERIFIABLE_TYPES = {"FACTUAL", "STATISTICAL", "CAUSAL"}

# Claim types whose complexity justifies the full reasoning model
_COMPLEX_TYPES = {"CAUSAL", "STATISTICAL"}


def should_verify(claim: dict) -> bool:
    """Return True if the claim type requires external evidence verification."""
    return claim.get("type", "").upper() in _VERIFIABLE_TYPES


def select_model(claim: dict) -> str:
    """
    Return the appropriate OpenAI model name for verifying a claim.

    - CAUSAL and STATISTICAL claims involve nuanced reasoning and get gpt-4o.
    - Straightforward FACTUAL claims use the cheaper gpt-4o-mini.
    """
    if claim.get("type", "").upper() in _COMPLEX_TYPES:
        return "gpt-4o"
    return "gpt-4o-mini"
