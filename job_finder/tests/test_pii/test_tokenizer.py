"""
Tests for PII Tokenizer — tokenization and detokenization of PII.

All tests use synthetic data from conftest fixtures.
"""

import pytest

from pii.tokenizer import PIITokenizer


class TestTokenization:
    """Test replacing PII values with {{TOKEN}} placeholders."""

    def test_tokenize_simple_text(self, tokenizer):
        text = "My name is Jane TestPerson and my email is jane.test@example.com"
        result = tokenizer.tokenize(text)

        assert "Jane TestPerson" not in result
        assert "jane.test@example.com" not in result
        assert "{{FULL_NAME}}" in result
        assert "{{EMAIL}}" in result

    def test_tokenize_preserves_non_pii(self, tokenizer):
        text = "I have 8 years of experience with Python and Go"
        result = tokenizer.tokenize(text)
        assert result == text  # No PII to replace

    def test_tokenize_multiple_occurrences(self, tokenizer):
        text = "Jane TestPerson is great. Contact Jane TestPerson at jane.test@example.com"
        result = tokenizer.tokenize(text)
        assert result.count("{{FULL_NAME}}") == 2
        assert "Jane TestPerson" not in result

    def test_tokenize_longest_first(self, tokenizer):
        """Ensure longer values are replaced before shorter ones."""
        # "Jane TestPerson" should be replaced before "Jane"
        text = "Jane TestPerson works here. Jane is great."
        result = tokenizer.tokenize(text)
        assert "{{FULL_NAME}}" in result


class TestDetokenization:
    """Test replacing {{TOKEN}} placeholders with real PII values."""

    def test_detokenize_simple(self, tokenizer):
        text = "Contact {{FULL_NAME}} at {{EMAIL}}"
        result = tokenizer.detokenize(text)
        assert result == "Contact Jane TestPerson at jane.test@example.com"

    def test_detokenize_unknown_token_preserved(self, tokenizer):
        text = "Hello {{UNKNOWN_TOKEN}}"
        result = tokenizer.detokenize(text)
        assert result == "Hello {{UNKNOWN_TOKEN}}"

    def test_roundtrip(self, tokenizer):
        """Tokenize then detokenize should recover original text."""
        original = "Jane TestPerson works at TestCorp International"
        tokenized = tokenizer.tokenize(original)
        restored = tokenizer.detokenize(tokenized)
        assert restored == original


class TestTokenExtraction:
    """Test finding tokens in text."""

    def test_extract_tokens(self, tokenizer):
        text = "Name: {{FULL_NAME}}, Email: {{EMAIL}}, SSN: {{SSN}}"
        tokens = tokenizer.extract_tokens(text)
        assert "{{FULL_NAME}}" in tokens
        assert "{{EMAIL}}" in tokens
        assert "{{SSN}}" in tokens

    def test_extract_no_tokens(self, tokenizer):
        text = "No tokens here, just plain text."
        tokens = tokenizer.extract_tokens(text)
        assert tokens == []


class TestPIIDetection:
    """Test scanning text for PII leaks."""

    def test_has_pii_detects_name(self, tokenizer):
        text = "The candidate is Jane TestPerson"
        assert tokenizer.has_pii(text) is True

    def test_has_pii_clean_text(self, tokenizer):
        text = "The candidate has {{FULL_NAME}} as their name"
        assert tokenizer.has_pii(text) is False

    def test_has_pii_ignores_short_values(self, tokenizer):
        """Very short values (<=2 chars) should not trigger detection."""
        # This prevents false positives from common strings
        text = "We use AI and ML"
        assert tokenizer.has_pii(text) is False


class TestDictTokenization:
    """Test recursive dict tokenization/detokenization."""

    def test_tokenize_dict(self, tokenizer):
        data = {
            "name": "Jane TestPerson",
            "email": "jane.test@example.com",
            "skills": ["Python", "Go"],
            "nested": {
                "address": "456 Test Ave, TestCity, TS 00000",
            },
        }
        result = tokenizer.tokenize_dict(data)

        assert result["name"] == "{{FULL_NAME}}"
        assert result["email"] == "{{EMAIL}}"
        assert result["skills"] == ["Python", "Go"]  # non-PII preserved
        assert result["nested"]["address"] == "{{ADDRESS}}"

    def test_detokenize_dict(self, tokenizer):
        data = {
            "name": "{{FULL_NAME}}",
            "contact": {"email": "{{EMAIL}}"},
        }
        result = tokenizer.detokenize_dict(data)
        assert result["name"] == "Jane TestPerson"
        assert result["contact"]["email"] == "jane.test@example.com"

    def test_dict_roundtrip(self, tokenizer):
        original = {
            "name": "Jane TestPerson",
            "phone": "555-000-1234",
            "skills": ["Python"],
        }
        tokenized = tokenizer.tokenize_dict(original)
        restored = tokenizer.detokenize_dict(tokenized)
        assert restored == original
