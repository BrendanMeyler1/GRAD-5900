"""Tests for encrypted AccountVault storage."""

import sqlite3
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet

from pii.account_vault import AccountVault


def _vault() -> tuple[AccountVault, Path]:
    tmp_dir = Path(".tmp")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_dir / f"account_vault_{uuid4().hex}.db"
    key = Fernet.generate_key().decode()
    return AccountVault(db_path=str(db_path), encryption_key=key), db_path


def test_store_and_get_account_roundtrip():
    vault, _ = _vault()
    account_id = vault.store_account(
        company="Acme Test Corp",
        ats_type="greenhouse",
        username="alex@example.com",
        password="super-secret-password",
        session_cookies=[{"name": "sessionid", "value": "abc"}],
        browser_context="ctx-123",
    )

    record = vault.get_account(company="Acme Test Corp", ats_type="greenhouse")
    assert record is not None
    assert record["account_id"] == account_id
    assert record["username"] == "alex@example.com"
    assert record["password"] == "super-secret-password"
    assert record["browser_context"] == "ctx-123"
    assert record["session_cookies"][0]["name"] == "sessionid"


def test_account_values_encrypted_at_rest():
    vault, db_path = _vault()
    vault.store_account(
        company="Acme Test Corp",
        ats_type="greenhouse",
        username="encrypted.user@example.com",
        password="plain-password",
    )

    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT username, password FROM accounts LIMIT 1"
        ).fetchone()
    assert row is not None
    assert row[0] != "encrypted.user@example.com"
    assert row[1] != "plain-password"


def test_update_session_and_status():
    vault, _ = _vault()
    account_id = vault.store_account(
        company="Acme Test Corp",
        ats_type="greenhouse",
        username="alex@example.com",
        password="secret",
    )

    updated = vault.update_session(
        account_id=account_id,
        session_cookies={"cookie": "value"},
        browser_context="ctx-updated",
    )
    assert updated is True

    status_updated = vault.update_status(account_id=account_id, status="needs_verify")
    assert status_updated is True

    record = vault.get_account(company="Acme Test Corp", ats_type="greenhouse")
    assert record["status"] == "needs_verify"
    assert record["browser_context"] == "ctx-updated"
    assert record["session_cookies"]["cookie"] == "value"


def test_list_accounts_returns_metadata_only():
    vault, _ = _vault()
    vault.store_account(
        company="Acme Test Corp",
        ats_type="greenhouse",
        username="alex@example.com",
        password="secret",
    )
    records = vault.list_accounts()
    assert len(records) == 1
    assert records[0]["company"] == "Acme Test Corp"
    assert "username" not in records[0]
    assert "password" not in records[0]
