"""Tests for setup.outlook_oauth_helper."""

from pathlib import Path
from uuid import uuid4

from setup.outlook_oauth_helper import (
    _build_authorize_url,
    _exchange_auth_code_for_tokens,
    _extract_auth_code,
    _upsert_env_file,
)


def test_extract_auth_code_from_url_and_raw():
    url = "http://localhost/?code=abc123xyz&state=test"
    assert _extract_auth_code(url) == "abc123xyz"
    assert _extract_auth_code("raw-code-123") == "raw-code-123"


def test_build_authorize_url_contains_required_params():
    url = _build_authorize_url(
        client_id="client-id-1",
        tenant="consumers",
        redirect_uri="http://localhost",
        scope="https://outlook.office.com/IMAP.AccessAsUser.All offline_access",
    )
    assert "login.microsoftonline.com/consumers/oauth2/v2.0/authorize" in url
    assert "client_id=client-id-1" in url
    assert "response_type=code" in url
    assert "scope=" in url


def test_upsert_env_file_updates_existing_and_appends_new():
    tmp_dir = Path(".tmp")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    env_path = tmp_dir / f"oauth_helper_{uuid4().hex}.env"
    env_path.write_text(
        "IMAP_HOST=old.example\n# keep this comment\nEXISTING_KEY=1\n",
        encoding="utf-8",
    )

    _upsert_env_file(
        env_path,
        {
            "IMAP_HOST": "outlook.office365.com",
            "IMAP_AUTH_MODE": "oauth2",
        },
    )

    text = env_path.read_text(encoding="utf-8")
    assert "IMAP_HOST=outlook.office365.com" in text
    assert "IMAP_AUTH_MODE=oauth2" in text
    assert "# keep this comment" in text
    assert "EXISTING_KEY=1" in text


def test_exchange_auth_code_for_tokens_posts_expected_payload(monkeypatch):
    class _FakeHTTPResponse:
        def __init__(self, body: bytes):
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return self._body

    captured = {}

    def fake_urlopen(request, timeout=30):
        captured["url"] = request.full_url
        captured["body"] = request.data.decode("utf-8", errors="replace")
        return _FakeHTTPResponse(
            b'{"access_token":"access-token-123","refresh_token":"refresh-token-456"}'
        )

    monkeypatch.setattr("setup.outlook_oauth_helper.urlopen", fake_urlopen)

    tokens = _exchange_auth_code_for_tokens(
        client_id="client-id-123",
        code="auth-code-xyz",
        tenant="consumers",
        redirect_uri="http://localhost",
        scope="https://outlook.office.com/IMAP.AccessAsUser.All offline_access",
    )

    assert "oauth2/v2.0/token" in captured["url"]
    assert "grant_type=authorization_code" in captured["body"]
    assert "client_id=client-id-123" in captured["body"]
    assert "code=auth-code-xyz" in captured["body"]
    assert tokens["access_token"] == "access-token-123"
    assert tokens["refresh_token"] == "refresh-token-456"

