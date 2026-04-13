"""
Tests for PII Normalizer — canonical + variant name resolution.

All tests use synthetic data from conftest fixtures.
"""

import pytest

from pii.normalizer import Normalizer


class TestNameRegistration:
    """Test registering canonical + variant forms."""

    def test_register_school(self, normalizer, vault):
        vault.store_token("{{SCHOOL}}", "University of Testing", "LOW")
        normalizer.register(
            "{{SCHOOL}}",
            canonical="University of Testing",
            variants=["UTest", "UTEST"],
        )
        names = vault.get_normalized_names("{{SCHOOL}}")
        assert names["canonical"] == "University of Testing"
        assert "UTest" in names["variants"]
        assert "UTEST" in names["variants"]

    def test_register_replaces_existing(self, normalizer, vault):
        vault.store_token("{{SCHOOL}}", "Old University", "LOW")
        normalizer.register("{{SCHOOL}}", "Old University", ["Old U"])
        normalizer.register("{{SCHOOL}}", "New University", ["New U"])

        names = vault.get_normalized_names("{{SCHOOL}}")
        assert names["canonical"] == "New University"
        assert "New U" in names["variants"]
        assert "Old U" not in names["variants"]


class TestResolution:
    """Test resolving tokens to appropriate name forms."""

    def test_resolve_canonical(self, populated_normalizer):
        result = populated_normalizer.resolve("{{SCHOOL}}", context="canonical")
        assert result == "University of Testing"

    def test_resolve_default_is_canonical(self, populated_normalizer):
        result = populated_normalizer.resolve("{{SCHOOL}}")
        assert result == "University of Testing"

    def test_resolve_abbreviation(self, populated_normalizer):
        result = populated_normalizer.resolve("{{SCHOOL}}", context="abbreviation")
        # Should return shortest variant
        assert result in ["UTest", "UTEST"]

    def test_resolve_short_is_abbreviation(self, populated_normalizer):
        result = populated_normalizer.resolve("{{SCHOOL}}", context="short")
        assert result in ["UTest", "UTEST"]

    def test_resolve_fallback_to_vault(self, normalizer, vault):
        """Tokens without normalization fall back to raw vault value."""
        vault.store_token("{{EMAIL}}", "test@example.com", "LOW")
        result = normalizer.resolve("{{EMAIL}}")
        assert result == "test@example.com"

    def test_resolve_nonexistent_token(self, normalizer):
        result = normalizer.resolve("{{NONEXISTENT}}")
        assert result is None

    def test_resolve_employer(self, populated_normalizer):
        result = populated_normalizer.resolve("{{EMPLOYER_1}}")
        assert result == "TestCorp International"

        result = populated_normalizer.resolve("{{EMPLOYER_1}}", context="abbreviation")
        assert result == "TCI"  # shortest variant


class TestBestMatch:
    """Test finding the best matching name form for a target."""

    def test_exact_match(self, populated_normalizer):
        result = populated_normalizer.find_best_match("{{SCHOOL}}", "University of Testing")
        assert result == "University of Testing"

    def test_abbreviation_match(self, populated_normalizer):
        result = populated_normalizer.find_best_match("{{SCHOOL}}", "UTest")
        assert result == "UTest"

    def test_substring_match(self, populated_normalizer):
        result = populated_normalizer.find_best_match(
            "{{SCHOOL}}", "Enter your school: University of Testing please"
        )
        assert result == "University of Testing"

    def test_no_match_returns_canonical(self, populated_normalizer):
        result = populated_normalizer.find_best_match(
            "{{SCHOOL}}", "Completely Unrelated Text"
        )
        assert result == "University of Testing"


class TestCache:
    """Test the normalization cache."""

    def test_cache_populated_on_resolve(self, populated_normalizer):
        assert len(populated_normalizer._cache) == 0  # noqa: SLF001
        populated_normalizer.resolve("{{SCHOOL}}")
        assert "{{SCHOOL}}" in populated_normalizer._cache  # noqa: SLF001

    def test_clear_cache(self, populated_normalizer):
        populated_normalizer.resolve("{{SCHOOL}}")
        assert len(populated_normalizer._cache) > 0  # noqa: SLF001
        populated_normalizer.clear_cache()
        assert len(populated_normalizer._cache) == 0  # noqa: SLF001
