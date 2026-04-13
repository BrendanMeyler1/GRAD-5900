"""
PII Tokenizer — Strips PII from text and replaces with {{TOKENS}}.

The Profile Analyst uses this to create the tokenized Experience Persona.
All downstream agents (running on remote LLMs) work only with
tokenized text — real values never leave the local machine.

Token format: {{TOKEN_NAME}} (double braces, NOT f-string interpolated)
"""

import logging
import re
from typing import Optional

from pii.vault import PIIVault
from pii.field_classifier import FieldClassifier

logger = logging.getLogger("job_finder.pii.tokenizer")


class PIITokenizer:
    """Tokenizes and detokenizes PII in text.

    Tokenization: Replaces real PII values with {{TOKENS}}.
    Detokenization: Replaces {{TOKENS}} with real values from the vault.
    """

    # Standard token pattern: {{ANYTHING_HERE}}
    TOKEN_PATTERN = re.compile(r"\{\{(\w+)\}\}")

    def __init__(self, vault: PIIVault):
        self.vault = vault
        self.field_classifier = FieldClassifier()

    def tokenize(self, text: str) -> str:
        """Replace known PII values in text with their token placeholders.

        Iterates through all stored tokens and replaces any occurrence
        of the real value with the token key.

        Args:
            text: Raw text potentially containing PII.

        Returns:
            Text with PII replaced by {{TOKEN}} placeholders.
        """
        all_tokens = self.vault.get_all_tokens_decrypted()

        # Sort by value length (longest first) to avoid partial replacements
        sorted_tokens = sorted(
            all_tokens.items(), key=lambda x: len(x[1]), reverse=True
        )

        tokenized = text
        for token_key, value in sorted_tokens:
            if value and value in tokenized:
                tokenized = tokenized.replace(value, token_key)
                logger.debug(f"Tokenized: <PII> → {token_key}")

        return tokenized

    def detokenize(self, text: str) -> str:
        """Replace {{TOKEN}} placeholders with real PII values.

        ⚠️ LOCAL USE ONLY — only called by the PII Injector (Ollama).

        Args:
            text: Tokenized text containing {{TOKEN}} placeholders.

        Returns:
            Text with all tokens resolved to real values.

        Raises:
            ValueError: If a token in the text has no value in the vault.
        """
        def _replace_token(match):
            token_key = f"{{{{{match.group(1)}}}}}"  # Reconstruct {{KEY}}
            value = self.vault.get_token(token_key)
            if value is None:
                logger.warning(f"Token {token_key} not found in vault")
                return match.group(0)  # Leave unresolved
            return value

        return self.TOKEN_PATTERN.sub(_replace_token, text)

    def extract_tokens(self, text: str) -> list[str]:
        """Find all {{TOKEN}} placeholders in text.

        Args:
            text: Text containing token placeholders.

        Returns:
            List of token keys found, e.g. ["{{FULL_NAME}}", "{{EMAIL}}"]
        """
        matches = self.TOKEN_PATTERN.findall(text)
        return [f"{{{{{m}}}}}" for m in matches]

    def has_pii(self, text: str) -> bool:
        """Check if text contains any known PII values.

        Used by the sanitizer middleware to detect PII leaks.

        Args:
            text: Text to scan.

        Returns:
            True if any stored PII value is found in the text.
        """
        all_tokens = self.vault.get_all_tokens_decrypted()
        for token_key, value in all_tokens.items():
            if value and len(value) > 2 and value in text:
                logger.warning(
                    f"PII leak detected: {token_key} value found in text"
                )
                return True
        return False

    def tokenize_dict(self, data: dict) -> dict:
        """Recursively tokenize all string values in a dict.

        Args:
            data: Dictionary with potentially PII-containing string values.

        Returns:
            New dict with all string values tokenized.
        """
        result = {}
        for key, value in data.items():
            if isinstance(value, str):
                result[key] = self.tokenize(value)
            elif isinstance(value, dict):
                result[key] = self.tokenize_dict(value)
            elif isinstance(value, list):
                result[key] = [
                    self.tokenize_dict(item) if isinstance(item, dict)
                    else self.tokenize(item) if isinstance(item, str)
                    else item
                    for item in value
                ]
            else:
                result[key] = value
        return result

    def detokenize_dict(self, data: dict) -> dict:
        """Recursively detokenize all string values in a dict.

        ⚠️ LOCAL USE ONLY.
        """
        result = {}
        for key, value in data.items():
            if isinstance(value, str):
                result[key] = self.detokenize(value)
            elif isinstance(value, dict):
                result[key] = self.detokenize_dict(value)
            elif isinstance(value, list):
                result[key] = [
                    self.detokenize_dict(item) if isinstance(item, dict)
                    else self.detokenize(item) if isinstance(item, str)
                    else item
                    for item in value
                ]
            else:
                result[key] = value
        return result
