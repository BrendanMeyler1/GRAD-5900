"""
PII Vault — Encrypted local storage for personal data tokens.

Uses Fernet symmetric encryption (from the `cryptography` library)
to encrypt all PII values at rest in a SQLite database.

The vault is the ONLY place real PII values are stored. Remote LLMs
never see these values — only the local PII Injector reads from the vault.

Schema (from Appendix F.1):
    tokens:
        token_key    TEXT PRIMARY KEY   -- e.g. "{{FULL_NAME}}"
        value        TEXT NOT NULL      -- Fernet-encrypted actual value
        category     TEXT NOT NULL      -- LOW | MEDIUM | HIGH
        created_at   TEXT NOT NULL

    normalized_names:
        token_key    TEXT NOT NULL      -- e.g. "{{SCHOOL}}"
        form         TEXT NOT NULL      -- "canonical" | "variant"
        value        TEXT NOT NULL      -- Fernet-encrypted
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet
from dotenv import load_dotenv

from errors import VaultError

load_dotenv()

logger = logging.getLogger("job_finder.pii.vault")


class PIIVault:
    """Encrypted local storage for PII tokens.

    All values are encrypted with Fernet before writing to SQLite
    and decrypted on read. The encryption key comes from the
    PII_VAULT_KEY environment variable.
    """

    def __init__(self, db_path: str = "pii_vault.db", encryption_key: Optional[str] = None):
        self.db_path = db_path
        key = encryption_key or os.getenv("PII_VAULT_KEY")
        if not key:
            raise VaultError(
                "PII_VAULT_KEY not set. Generate one with: "
                "python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
            )
        try:
            self.fernet = Fernet(key.encode() if isinstance(key, str) else key)
        except Exception as e:
            raise VaultError(f"Invalid PII_VAULT_KEY: {e}") from e

        self._init_db()

    def _init_db(self):
        """Create tables if they don't exist."""
        from contextlib import closing
        with closing(self._connect()) as conn, conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS tokens (
                    token_key    TEXT PRIMARY KEY,
                    value        TEXT NOT NULL,
                    category     TEXT NOT NULL,
                    created_at   TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS normalized_names (
                    token_key    TEXT NOT NULL,
                    form         TEXT NOT NULL,
                    value        TEXT NOT NULL,
                    FOREIGN KEY (token_key) REFERENCES tokens(token_key)
                );
            """)

    def _connect(self) -> sqlite3.Connection:
        """Get a database connection."""
        return sqlite3.connect(self.db_path)

    def _encrypt(self, plaintext: str) -> str:
        """Encrypt a string value."""
        return self.fernet.encrypt(plaintext.encode()).decode()

    def _decrypt(self, ciphertext: str) -> str:
        """Decrypt an encrypted string value."""
        try:
            return self.fernet.decrypt(ciphertext.encode()).decode()
        except Exception as e:
            raise VaultError(f"Decryption failed — wrong key or corrupted data: {e}") from e

    def store_token(self, token_key: str, value: str, category: str = "LOW") -> None:
        """Store or update a PII token.

        Args:
            token_key: Token identifier, e.g. "{{FULL_NAME}}"
            value: The actual PII value (will be encrypted)
            category: Sensitivity level — LOW, MEDIUM, or HIGH
        """
        if category not in ("LOW", "MEDIUM", "HIGH"):
            raise ValueError(f"Invalid category '{category}'. Must be LOW, MEDIUM, or HIGH.")

        encrypted_value = self._encrypt(value)
        now = datetime.now(timezone.utc).isoformat()

        from contextlib import closing
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """INSERT OR REPLACE INTO tokens (token_key, value, category, created_at)
                   VALUES (?, ?, ?, ?)""",
                (token_key, encrypted_value, category, now),
            )
        logger.info(f"Stored token {token_key} (category={category})")

    def get_token(self, token_key: str) -> Optional[str]:
        """Retrieve and decrypt a PII token value.

        Args:
            token_key: Token identifier, e.g. "{{FULL_NAME}}"

        Returns:
            Decrypted value, or None if token doesn't exist.
        """
        from contextlib import closing
        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                "SELECT value FROM tokens WHERE token_key = ?", (token_key,)
            ).fetchone()

        if row is None:
            return None
        return self._decrypt(row[0])

    def get_token_category(self, token_key: str) -> Optional[str]:
        """Get the sensitivity category for a token.

        Returns:
            "LOW", "MEDIUM", or "HIGH", or None if token doesn't exist.
        """
        from contextlib import closing
        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                "SELECT category FROM tokens WHERE token_key = ?", (token_key,)
            ).fetchone()
        return row[0] if row else None

    def list_tokens(self) -> list[dict]:
        """List all token keys and their categories (no values).

        This is safe to expose to remote agents for field mapping planning.
        """
        from contextlib import closing
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(
                "SELECT token_key, category, created_at FROM tokens"
            ).fetchall()
        return [
            {"token_key": r[0], "category": r[1], "created_at": r[2]}
            for r in rows
        ]

    def delete_token(self, token_key: str) -> bool:
        """Delete a token and its normalized name variants.

        Returns:
            True if the token existed and was deleted.
        """
        from contextlib import closing
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "DELETE FROM normalized_names WHERE token_key = ?", (token_key,)
            )
            cursor = conn.execute(
                "DELETE FROM tokens WHERE token_key = ?", (token_key,)
            )
        deleted = cursor.rowcount > 0
        if deleted:
            logger.info(f"Deleted token {token_key}")
        return deleted

    def store_normalized_name(
        self, token_key: str, form: str, value: str
    ) -> None:
        """Store a normalized name variant for a token.

        Args:
            token_key: Parent token, e.g. "{{SCHOOL}}"
            form: "canonical" or "variant"
            value: The name form (will be encrypted)
        """
        encrypted = self._encrypt(value)
        from contextlib import closing
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """INSERT INTO normalized_names (token_key, form, value)
                   VALUES (?, ?, ?)""",
                (token_key, form, encrypted),
            )
        logger.debug(f"Stored normalized name for {token_key} (form={form})")

    def get_normalized_names(self, token_key: str) -> dict[str, list[str]]:
        """Get all normalized name forms for a token.

        Returns:
            Dict with "canonical" and "variants" keys. Example:
            {"canonical": "University of Connecticut", "variants": ["UConn", "UCONN"]}
        """
        from contextlib import closing
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(
                "SELECT form, value FROM normalized_names WHERE token_key = ?",
                (token_key,),
            ).fetchall()

        result: dict[str, list[str]] = {"canonical": [], "variants": []}
        for form, encrypted_value in rows:
            decrypted = self._decrypt(encrypted_value)
            if form == "canonical":
                result["canonical"].append(decrypted)
            else:
                result["variants"].append(decrypted)

        # Flatten canonical to single string if present
        return {
            "canonical": result["canonical"][0] if result["canonical"] else None,
            "variants": result["variants"],
        }

    def get_all_tokens_decrypted(self) -> dict[str, str]:
        """Decrypt and return ALL token values.

        ⚠️ LOCAL USE ONLY — this is for the PII Injector running on Ollama.
        Never expose this method via MCP or API.
        """
        from contextlib import closing
        with closing(self._connect()) as conn, conn:
            rows = conn.execute("SELECT token_key, value FROM tokens").fetchall()
        return {r[0]: self._decrypt(r[1]) for r in rows}
