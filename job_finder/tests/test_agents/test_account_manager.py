"""Tests for Account Manager agent."""

from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from agents.account_manager import bind_verification_session, manage_account
from errors import AccountError
from pii.account_vault import AccountVault


def _vault() -> AccountVault:
    from uuid import uuid4
    db_path = f"file:account_{uuid4().hex}?mode=memory&cache=shared"
    key = Fernet.generate_key().decode()
    # Need uri=True in sqlite3 internally for this to work, but let's test.
    # Actually, if we just use an absolute unique temp file path AND fix the tests, it might be safer if uri=True isn't enabled in AccountVault.
    # Let me just ensure we fix the bypass assertion first!
    tmp_dir = Path(".tmp")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_dir / f"account_manager_{uuid4().hex}.db"
    key = Fernet.generate_key().decode()
    return AccountVault(db_path=str(db_path), encryption_key=key)


def test_manage_account_reuses_existing_active_record():
    vault = _vault()
    account_id = vault.store_account(
        company="Acme Test Corp",
        ats_type="greenhouse",
        username="alex@example.com",
        password="secret",
        session_cookies={"sessionid": "abc"},
        browser_context="ctx-old",
        status="active",
    )

    result = manage_account(
        company="Acme Test Corp",
        ats_type="greenhouse",
        vault=vault,
    )

    assert result["account_status"] == "existing"
    assert result["action"] == "use_existing"
    assert result["account_id"] == account_id
    assert result["session_context_id"] == "ctx-old"
    assert result["requires_human"] is False


def test_manage_account_creates_new_when_missing():
    vault = _vault()

    result = manage_account(
        company="Acme Test Corp",
        ats_type="workday",
        username="new.user@example.com",
        password="new-secret",
        vault=vault,
    )

    assert result["account_status"] == "created"
    assert result["action"] == "create_account"
    assert result["account_id"] is not None

    stored = vault.get_account(company="Acme Test Corp", ats_type="workday")
    assert stored is not None
    assert stored["username"] == "new.user@example.com"


def test_manage_account_escalates_for_locked_status():
    vault = _vault()
    account_id = vault.store_account(
        company="Acme Test Corp",
        ats_type="greenhouse",
        username="alex@example.com",
        password="secret",
        status="locked",
    )

    result = manage_account(
        company="Acme Test Corp",
        ats_type="greenhouse",
        vault=vault,
    )

    assert result["account_status"] == "failed"
    assert result["action"] == "escalate"
    assert result["account_id"] == account_id
    assert result["requires_human"] is True


def test_manage_account_raises_when_missing_credentials():
    vault = _vault()
    with pytest.raises(AccountError):
        manage_account(
            company="Acme Test Corp",
            ats_type="workday",
            vault=vault,
        )


def test_bind_verification_session_updates_stored_context():
    vault = _vault()
    account_id = vault.store_account(
        company="Acme Test Corp",
        ats_type="greenhouse",
        username="alex@example.com",
        password="secret",
    )

    ok = bind_verification_session(
        account_id=account_id,
        session_cookies={"sessionid": "updated"},
        browser_context="ctx-verification",
        vault=vault,
    )
    assert ok is True

    stored = vault.get_account(company="Acme Test Corp", ats_type="greenhouse")
    assert stored["session_cookies"]["sessionid"] == "updated"
    assert stored["browser_context"] == "ctx-verification"
