"""
Tests for PII Vault — encrypted storage for personal data.

All tests use synthetic data. No real PII values.
"""

import os
import pytest
from cryptography.fernet import Fernet

from pii.vault import PIIVault
from errors import VaultError


class TestPIIVaultInitialization:
    """Test vault creation and configuration."""

    def test_creates_db_file(self, temp_dir, vault_key):
        db_path = os.path.join(temp_dir, "test.db")
        vault = PIIVault(db_path=db_path, encryption_key=vault_key)
        assert os.path.exists(db_path)

    def test_raises_without_key(self, temp_dir):
        # Clear the env var if set
        old_val = os.environ.pop("PII_VAULT_KEY", None)
        try:
            with pytest.raises(VaultError, match="PII_VAULT_KEY not set"):
                PIIVault(
                    db_path=os.path.join(temp_dir, "test.db"),
                    encryption_key=None,
                )
        finally:
            if old_val:
                os.environ["PII_VAULT_KEY"] = old_val

    def test_raises_with_invalid_key(self, temp_dir):
        with pytest.raises(VaultError, match="Invalid PII_VAULT_KEY"):
            PIIVault(
                db_path=os.path.join(temp_dir, "test.db"),
                encryption_key="not-a-valid-fernet-key",
            )


class TestTokenStorage:
    """Test storing and retrieving PII tokens."""

    def test_store_and_retrieve(self, vault):
        vault.store_token("{{FULL_NAME}}", "Jane Doe", "LOW")
        assert vault.get_token("{{FULL_NAME}}") == "Jane Doe"

    def test_encryption_at_rest(self, vault):
        """Verify values are encrypted in the database, not plaintext."""
        vault.store_token("{{EMAIL}}", "secret@test.com", "LOW")

        import sqlite3
        conn = sqlite3.connect(vault.db_path)
        row = conn.execute(
            "SELECT value FROM tokens WHERE token_key = '{{EMAIL}}'"
        ).fetchone()
        conn.close()

        # The stored value should NOT be the plaintext
        assert row[0] != "secret@test.com"
        # But decryption should work
        assert vault.get_token("{{EMAIL}}") == "secret@test.com"

    def test_get_nonexistent_token(self, vault):
        assert vault.get_token("{{NONEXISTENT}}") is None

    def test_update_existing_token(self, vault):
        vault.store_token("{{PHONE}}", "111-111-1111", "MEDIUM")
        vault.store_token("{{PHONE}}", "222-222-2222", "MEDIUM")
        assert vault.get_token("{{PHONE}}") == "222-222-2222"

    def test_category_storage(self, vault):
        vault.store_token("{{SSN}}", "000-00-0000", "HIGH")
        assert vault.get_token_category("{{SSN}}") == "HIGH"

    def test_invalid_category_rejected(self, vault):
        with pytest.raises(ValueError, match="Invalid category"):
            vault.store_token("{{TEST}}", "value", "INVALID")

    def test_list_tokens_no_values(self, vault):
        """list_tokens should return keys and categories but NOT values."""
        vault.store_token("{{NAME}}", "Secret Name", "LOW")
        vault.store_token("{{SSN}}", "000-00-0000", "HIGH")

        tokens = vault.list_tokens()
        assert len(tokens) == 2

        keys = {t["token_key"] for t in tokens}
        assert "{{NAME}}" in keys
        assert "{{SSN}}" in keys

        # Values should not be in the response
        for t in tokens:
            assert "Secret Name" not in str(t)
            assert "000-00-0000" not in str(t)

    def test_delete_token(self, vault):
        vault.store_token("{{TEMP}}", "temp_value", "LOW")
        assert vault.get_token("{{TEMP}}") == "temp_value"

        deleted = vault.delete_token("{{TEMP}}")
        assert deleted is True
        assert vault.get_token("{{TEMP}}") is None

    def test_delete_nonexistent_returns_false(self, vault):
        assert vault.delete_token("{{NOPE}}") is False


class TestNormalizedNames:
    """Test canonical + variant name storage."""

    def test_store_and_retrieve_normalized(self, vault):
        vault.store_token("{{SCHOOL}}", "University of Testing", "LOW")
        vault.store_normalized_name("{{SCHOOL}}", "canonical", "University of Testing")
        vault.store_normalized_name("{{SCHOOL}}", "variant", "UTest")
        vault.store_normalized_name("{{SCHOOL}}", "variant", "UTEST")

        names = vault.get_normalized_names("{{SCHOOL}}")
        assert names["canonical"] == "University of Testing"
        assert "UTest" in names["variants"]
        assert "UTEST" in names["variants"]

    def test_empty_normalized_names(self, vault):
        names = vault.get_normalized_names("{{NONEXISTENT}}")
        assert names["canonical"] is None
        assert names["variants"] == []


class TestBulkDecryption:
    """Test the local-only bulk decryption method."""

    def test_get_all_decrypted(self, populated_vault):
        all_tokens = populated_vault.get_all_tokens_decrypted()
        assert all_tokens["{{FULL_NAME}}"] == "Jane TestPerson"
        assert all_tokens["{{EMAIL}}"] == "jane.test@example.com"
        assert all_tokens["{{PHONE}}"] == "555-000-1234"

    def test_wrong_key_cannot_decrypt(self, temp_dir):
        """Verify that a different key cannot read the vault."""
        key1 = Fernet.generate_key().decode()
        key2 = Fernet.generate_key().decode()

        db_path = os.path.join(temp_dir, "cross_key.db")
        vault1 = PIIVault(db_path=db_path, encryption_key=key1)
        vault1.store_token("{{TEST}}", "secret", "LOW")

        vault2 = PIIVault(db_path=db_path, encryption_key=key2)
        with pytest.raises(VaultError, match="Decryption failed"):
            vault2.get_token("{{TEST}}")
