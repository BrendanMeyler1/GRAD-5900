"""
db/encryption.py — Field-level encryption for sensitive user data.

Uses Fernet symmetric encryption (AES-128-CBC + HMAC-SHA256).
Encrypts specific fields (email, phone, address) before writing to SQLite
and decrypts transparently on read.

The encryption key is:
  1. Loaded from DB_ENCRYPTION_KEY env var if present.
  2. Auto-generated and saved to data/encryption.key on first run.

This is intentionally simple — the app is local-only and single-user.
The goal is protecting sensitive data if the SQLite file is copied,
not hardening against an adversary with filesystem access.

Usage:
    from db.encryption import get_encryptor
    enc = get_encryptor()
    encrypted = enc.encrypt("user@example.com")
    plain     = enc.decrypt(encrypted)
"""

from __future__ import annotations

import logging
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)

_SENSITIVE_FIELDS = {"email", "phone", "address"}


class FieldEncryptor:
    """Encrypts and decrypts individual string values using Fernet."""

    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(key)

    def encrypt(self, value: str | None) -> str | None:
        """Encrypt a plaintext string. Returns None if value is None."""
        if value is None:
            return None
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, token: str | None) -> str | None:
        """
        Decrypt a Fernet token string. Returns None if token is None.

        Raises:
            ValueError: If the token is invalid or was encrypted with a different key.
        """
        if token is None:
            return None
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise ValueError(
                "Failed to decrypt field — key mismatch or corrupted data. "
                "If you changed DB_ENCRYPTION_KEY, existing data cannot be decrypted."
            ) from exc

    def encrypt_dict(self, data: dict, fields: set[str] | None = None) -> dict:
        """
        Return a copy of `data` with sensitive fields encrypted.

        Args:
            data: Dict of field names → values.
            fields: Set of field names to encrypt. Defaults to _SENSITIVE_FIELDS.
        """
        target = fields if fields is not None else _SENSITIVE_FIELDS
        result = dict(data)
        for field in target:
            if field in result and result[field] is not None:
                result[field] = self.encrypt(str(result[field]))
        return result

    def decrypt_dict(self, data: dict, fields: set[str] | None = None) -> dict:
        """
        Return a copy of `data` with sensitive fields decrypted.

        Args:
            data: Dict of field names → values (may be encrypted tokens).
            fields: Set of field names to decrypt. Defaults to _SENSITIVE_FIELDS.
        """
        target = fields if fields is not None else _SENSITIVE_FIELDS
        result = dict(data)
        for field in target:
            if field in result and result[field] is not None:
                try:
                    result[field] = self.decrypt(str(result[field]))
                except ValueError:
                    # Field may not be encrypted (e.g., seeded plaintext data).
                    log.debug("Field %s could not be decrypted — leaving as-is", field)
        return result


def _load_or_generate_key(key_env: str | None, key_file: Path) -> bytes:
    """
    Resolve the Fernet key from env → file → generate new.

    Args:
        key_env: Raw key string from DB_ENCRYPTION_KEY env var (or None).
        key_file: Path to auto-generated key file.

    Returns:
        URL-safe base64-encoded 32-byte Fernet key.
    """
    if key_env:
        return key_env.encode()

    if key_file.exists():
        return key_file.read_bytes().strip()

    # Generate a new key and persist it
    key = Fernet.generate_key()
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_bytes(key)
    log.info(
        "Generated new encryption key",
        extra={"key_file": str(key_file)},
    )
    return key


def get_encryptor(
    key_env: str | None = None,
    data_dir: str = "./data",
) -> FieldEncryptor:
    """
    Build a FieldEncryptor using the configured or auto-generated key.

    Intended to be called once and stored as a dependency.

    Args:
        key_env: Value of DB_ENCRYPTION_KEY env var.
        data_dir: Base data directory (key file saved here).

    Returns:
        Initialised FieldEncryptor ready for use.
    """
    key_file = Path(data_dir) / "encryption.key"
    key = _load_or_generate_key(key_env, key_file)
    return FieldEncryptor(key)
