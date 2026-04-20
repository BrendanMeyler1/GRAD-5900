"""Unit tests for db.encryption.FieldEncryptor — Fernet round-trips and dict helpers."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from db.encryption import FieldEncryptor


def test_encrypt_decrypt_round_trip() -> None:
    enc = FieldEncryptor(Fernet.generate_key())
    original = "user@example.com"
    token = enc.encrypt(original)
    assert token is not None
    assert token != original
    assert enc.decrypt(token) == original


def test_encrypt_none_returns_none() -> None:
    enc = FieldEncryptor(Fernet.generate_key())
    assert enc.encrypt(None) is None
    assert enc.decrypt(None) is None


def test_wrong_key_raises_value_error() -> None:
    a = FieldEncryptor(Fernet.generate_key())
    b = FieldEncryptor(Fernet.generate_key())
    token = a.encrypt("secret@example.com")
    with pytest.raises(ValueError, match="Failed to decrypt"):
        b.decrypt(token)


def test_encrypt_dict_default_fields() -> None:
    enc = FieldEncryptor(Fernet.generate_key())
    data = {
        "email": "e@x.com",
        "phone": "555-1234",
        "address": "1 Main St",
        "city": "Boston",  # not sensitive
    }
    encrypted = enc.encrypt_dict(data)
    assert encrypted["email"] != data["email"]
    assert encrypted["phone"] != data["phone"]
    assert encrypted["address"] != data["address"]
    assert encrypted["city"] == data["city"]  # untouched


def test_decrypt_dict_round_trip() -> None:
    enc = FieldEncryptor(Fernet.generate_key())
    original = {"email": "e@x.com", "phone": "555-1234", "address": "1 Main"}
    encrypted = enc.encrypt_dict(original)
    decrypted = enc.decrypt_dict(encrypted)
    assert decrypted == original


def test_decrypt_dict_tolerates_plaintext_field() -> None:
    """If a field value isn't a valid Fernet token, decrypt_dict leaves it alone."""
    enc = FieldEncryptor(Fernet.generate_key())
    data = {"email": "not-encrypted-plaintext"}
    result = enc.decrypt_dict(data)
    assert result["email"] == "not-encrypted-plaintext"
