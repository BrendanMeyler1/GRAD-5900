"""
Account Vault - encrypted storage for ATS credentials and session state.

Schema (implementation plan Appendix F.2):
    accounts (
        account_id        TEXT PRIMARY KEY,
        company           TEXT NOT NULL,
        ats_type          TEXT NOT NULL,
        username          TEXT NOT NULL,      -- encrypted
        password          TEXT NOT NULL,      -- encrypted
        session_cookies   TEXT,               -- encrypted JSON
        browser_context   TEXT,               -- context ID for Session Binder
        status            TEXT DEFAULT 'active',
        created_at        TEXT NOT NULL,
        last_used_at      TEXT
    )
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from cryptography.fernet import Fernet
from dotenv import load_dotenv

from errors import VaultError

load_dotenv()

_ALLOWED_STATUSES = {"active", "locked", "needs_verify"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AccountVault:
    """Encrypted credentials/session storage for ATS accounts."""

    def __init__(
        self,
        db_path: str = "account_vault.db",
        encryption_key: str | None = None,
    ) -> None:
        self.db_path = db_path
        key = (
            encryption_key
            or os.getenv("ACCOUNT_VAULT_KEY")
            or os.getenv("PII_VAULT_KEY")
        )
        if not key:
            raise VaultError(
                "ACCOUNT_VAULT_KEY not set. Provide ACCOUNT_VAULT_KEY "
                "(or fallback PII_VAULT_KEY) in environment."
            )
        try:
            self.fernet = Fernet(key.encode() if isinstance(key, str) else key)
        except Exception as exc:
            raise VaultError(f"Invalid ACCOUNT_VAULT_KEY: {exc}") from exc
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        from contextlib import closing
        with closing(self._connect()) as conn, conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    account_id        TEXT PRIMARY KEY,
                    company           TEXT NOT NULL,
                    ats_type          TEXT NOT NULL,
                    username          TEXT NOT NULL,
                    password          TEXT NOT NULL,
                    session_cookies   TEXT,
                    browser_context   TEXT,
                    status            TEXT DEFAULT 'active',
                    created_at        TEXT NOT NULL,
                    last_used_at      TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_accounts_company_ats
                    ON accounts(company, ats_type);
                """
            )

    def _encrypt(self, value: str) -> str:
        return self.fernet.encrypt(value.encode("utf-8")).decode("utf-8")

    def _decrypt(self, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return self.fernet.decrypt(value.encode("utf-8")).decode("utf-8")
        except Exception as exc:
            raise VaultError(f"Account vault decryption failed: {exc}") from exc

    def store_account(
        self,
        company: str,
        ats_type: str,
        username: str,
        password: str,
        account_id: str | None = None,
        session_cookies: dict[str, Any] | list[Any] | None = None,
        browser_context: str | None = None,
        status: str = "active",
    ) -> str:
        """
        Insert or replace an encrypted account record.
        """
        if status not in _ALLOWED_STATUSES:
            raise ValueError(f"Invalid status '{status}'. Must be one of {_ALLOWED_STATUSES}.")
        record_id = account_id or str(uuid4())
        now = _utc_now()

        cookies_blob: str | None = None
        if session_cookies is not None:
            cookies_blob = self._encrypt(json.dumps(session_cookies))

        from contextlib import closing
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO accounts (
                    account_id, company, ats_type, username, password,
                    session_cookies, browser_context, status, created_at, last_used_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    company,
                    ats_type,
                    self._encrypt(username),
                    self._encrypt(password),
                    cookies_blob,
                    browser_context,
                    status,
                    now,
                    now,
                ),
            )
        return record_id

    def get_account(
        self,
        company: str,
        ats_type: str,
    ) -> dict[str, Any] | None:
        """
        Fetch latest account for (company, ats_type) with decrypted credentials.
        """
        from contextlib import closing
        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                """
                SELECT account_id, company, ats_type, username, password,
                       session_cookies, browser_context, status, created_at, last_used_at
                FROM accounts
                WHERE company = ? AND ats_type = ?
                ORDER BY last_used_at DESC
                LIMIT 1
                """,
                (company, ats_type),
            ).fetchone()
        if row is None:
            return None

        cookies = None
        decrypted_cookies = self._decrypt(row[5])
        if decrypted_cookies:
            try:
                cookies = json.loads(decrypted_cookies)
            except json.JSONDecodeError:
                cookies = None

        return {
            "account_id": row[0],
            "company": row[1],
            "ats_type": row[2],
            "username": self._decrypt(row[3]),
            "password": self._decrypt(row[4]),
            "session_cookies": cookies,
            "browser_context": row[6],
            "status": row[7],
            "created_at": row[8],
            "last_used_at": row[9],
        }

    def update_session(
        self,
        account_id: str,
        session_cookies: dict[str, Any] | list[Any],
        browser_context: str | None = None,
    ) -> bool:
        """
        Update session cookies + optional browser context.
        """
        cookies_blob = self._encrypt(json.dumps(session_cookies))
        now = _utc_now()
        from contextlib import closing
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                """
                UPDATE accounts
                SET session_cookies = ?,
                    browser_context = COALESCE(?, browser_context),
                    last_used_at = ?
                WHERE account_id = ?
                """,
                (cookies_blob, browser_context, now, account_id),
            )
        return cursor.rowcount > 0

    def update_status(self, account_id: str, status: str) -> bool:
        """
        Update account status: active | locked | needs_verify.
        """
        if status not in _ALLOWED_STATUSES:
            raise ValueError(f"Invalid status '{status}'. Must be one of {_ALLOWED_STATUSES}.")
        from contextlib import closing
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                """
                UPDATE accounts
                SET status = ?, last_used_at = ?
                WHERE account_id = ?
                """,
                (status, _utc_now(), account_id),
            )
        return cursor.rowcount > 0

    def touch_last_used(self, account_id: str) -> bool:
        from contextlib import closing
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                "UPDATE accounts SET last_used_at = ? WHERE account_id = ?",
                (_utc_now(), account_id),
            )
        return cursor.rowcount > 0

    def list_accounts(self) -> list[dict[str, Any]]:
        """
        List account metadata only (no decrypted secrets).
        """
        from contextlib import closing
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(
                """
                SELECT account_id, company, ats_type, status, created_at, last_used_at
                FROM accounts
                ORDER BY last_used_at DESC
                """
            ).fetchall()
        return [
            {
                "account_id": row[0],
                "company": row[1],
                "ats_type": row[2],
                "status": row[3],
                "created_at": row[4],
                "last_used_at": row[5],
            }
            for row in rows
        ]

    def delete_account(self, account_id: str) -> bool:
        from contextlib import closing
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                "DELETE FROM accounts WHERE account_id = ?",
                (account_id,),
            )
        return cursor.rowcount > 0
