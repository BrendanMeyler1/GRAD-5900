"""
Interactive helper for Outlook OAuth2 IMAP setup.

Usage:
    python -m setup.outlook_oauth_helper --client-id <APP_CLIENT_ID> --imap-user <you@outlook.com>

The helper will:
1. Open the Microsoft authorization URL.
2. Ask you to paste the redirected URL (or auth code).
3. Exchange code for access/refresh token.
4. Upsert IMAP OAuth settings in .env.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import webbrowser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

DEFAULT_TENANT = "consumers"
DEFAULT_SCOPE = "https://outlook.office.com/IMAP.AccessAsUser.All offline_access"
DEFAULT_REDIRECT_URI = "http://localhost"
DEFAULT_IMAP_HOST = "outlook.office365.com"
DEFAULT_IMAP_PORT = "993"

ENV_KEY_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _extract_auth_code(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    if text.startswith("http://") or text.startswith("https://"):
        parsed = urlparse(text)
        query = parse_qs(parsed.query)
        code_values = query.get("code") or []
        if code_values and str(code_values[0]).strip():
            return str(code_values[0]).strip()
        return ""

    if "code=" in text and "&" in text:
        query = parse_qs(text)
        code_values = query.get("code") or []
        if code_values and str(code_values[0]).strip():
            return str(code_values[0]).strip()

    return text


def _build_authorize_url(
    *,
    client_id: str,
    tenant: str,
    redirect_uri: str,
    scope: str,
) -> str:
    base = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": scope,
        "prompt": "consent",
    }
    return f"{base}?{urlencode(params)}"


def _exchange_auth_code_for_tokens(
    *,
    client_id: str,
    code: str,
    tenant: str,
    redirect_uri: str,
    scope: str,
    client_secret: str | None = None,
) -> dict[str, str]:
    token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    payload = {
        "client_id": client_id,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "scope": scope,
    }
    if client_secret:
        payload["client_secret"] = client_secret

    body = urlencode(payload).encode("utf-8")
    request = Request(
        token_url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Token exchange failed ({exc.code}): {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Token exchange failed: {exc}") from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Token exchange response was not JSON: {raw[:500]}") from exc

    access_token = str(parsed.get("access_token") or "").strip()
    refresh_token = str(parsed.get("refresh_token") or "").strip()
    if not access_token:
        raise RuntimeError(f"Token exchange returned no access_token: {raw[:500]}")

    result = {"access_token": access_token}
    if refresh_token:
        result["refresh_token"] = refresh_token
    return result


def _upsert_env_file(env_path: Path, updates: dict[str, str]) -> None:
    env_path.parent.mkdir(parents=True, exist_ok=True)
    existing_lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []

    written_keys: set[str] = set()
    output_lines: list[str] = []
    for line in existing_lines:
        match = ENV_KEY_RE.match(line)
        if not match:
            output_lines.append(line)
            continue
        key = match.group(1)
        if key in updates:
            output_lines.append(f"{key}={updates[key]}")
            written_keys.add(key)
            continue
        output_lines.append(line)

    for key, value in updates.items():
        if key in written_keys:
            continue
        output_lines.append(f"{key}={value}")

    env_path.write_text("\n".join(output_lines).rstrip() + "\n", encoding="utf-8")


def _mask_secret(value: str) -> str:
    text = str(value or "")
    if len(text) <= 10:
        return "*" * len(text)
    return f"{text[:6]}...{text[-4:]}"


def _non_empty(value: str | None, prompt: str) -> str:
    if value and value.strip():
        return value.strip()
    typed = input(prompt).strip()
    if not typed:
        raise SystemExit("Required value not provided.")
    return typed


def main() -> None:
    parser = argparse.ArgumentParser(description="Outlook OAuth2 helper for IMAP status tracking.")
    parser.add_argument("--env-file", default=".env", help="Path to .env file (default: .env)")
    parser.add_argument("--client-id", default=os.getenv("IMAP_OAUTH2_CLIENT_ID"))
    parser.add_argument("--client-secret", default=os.getenv("IMAP_OAUTH2_CLIENT_SECRET"))
    parser.add_argument("--tenant", default=os.getenv("IMAP_OAUTH2_TENANT", DEFAULT_TENANT))
    parser.add_argument("--scope", default=os.getenv("IMAP_OAUTH2_SCOPE", DEFAULT_SCOPE))
    parser.add_argument("--redirect-uri", default=DEFAULT_REDIRECT_URI)
    parser.add_argument("--imap-user", default=os.getenv("IMAP_USER"))
    parser.add_argument("--imap-host", default=os.getenv("IMAP_HOST", DEFAULT_IMAP_HOST))
    parser.add_argument("--imap-port", default=os.getenv("IMAP_PORT", DEFAULT_IMAP_PORT))
    parser.add_argument("--code", default=None, help="Authorization code (optional).")
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser automatically.")
    args = parser.parse_args()

    client_id = _non_empty(args.client_id, "Enter Azure app client_id: ")
    imap_user = _non_empty(args.imap_user, "Enter Outlook email (IMAP_USER): ")
    tenant = str(args.tenant or DEFAULT_TENANT).strip()
    scope = str(args.scope or DEFAULT_SCOPE).strip()
    redirect_uri = str(args.redirect_uri or DEFAULT_REDIRECT_URI).strip()

    code = _extract_auth_code(args.code or "")
    if not code:
        auth_url = _build_authorize_url(
            client_id=client_id,
            tenant=tenant,
            redirect_uri=redirect_uri,
            scope=scope,
        )
        print("\nOpen this URL, sign in, and grant consent:")
        print(auth_url)
        if not args.no_browser:
            try:
                webbrowser.open(auth_url)
            except Exception:
                pass
        pasted = input("\nPaste redirected URL or authorization code: ").strip()
        code = _extract_auth_code(pasted)
        if not code:
            raise SystemExit("Could not extract authorization code from input.")

    tokens = _exchange_auth_code_for_tokens(
        client_id=client_id,
        code=code,
        tenant=tenant,
        redirect_uri=redirect_uri,
        scope=scope,
        client_secret=(args.client_secret or "").strip() or None,
    )

    updates = {
        "IMAP_HOST": str(args.imap_host or DEFAULT_IMAP_HOST).strip(),
        "IMAP_PORT": str(args.imap_port or DEFAULT_IMAP_PORT).strip(),
        "IMAP_USER": imap_user,
        "IMAP_AUTH_MODE": "oauth2",
        "IMAP_OAUTH2_CLIENT_ID": client_id,
        "IMAP_OAUTH2_TENANT": tenant,
        "IMAP_OAUTH2_SCOPE": scope,
        "IMAP_OAUTH2_ACCESS_TOKEN": tokens["access_token"],
    }
    refresh_token = tokens.get("refresh_token")
    if refresh_token:
        updates["IMAP_OAUTH2_REFRESH_TOKEN"] = refresh_token
    if args.client_secret:
        updates["IMAP_OAUTH2_CLIENT_SECRET"] = str(args.client_secret).strip()

    env_path = Path(args.env_file).resolve()
    _upsert_env_file(env_path, updates)

    print("\nOAuth setup complete.")
    print(f"Updated: {env_path}")
    print(f"IMAP user: {imap_user}")
    print(f"Access token: {_mask_secret(tokens['access_token'])}")
    if refresh_token:
        print(f"Refresh token: {_mask_secret(refresh_token)}")
    else:
        print("Refresh token was not returned. Ensure scope includes offline_access.")
    print("\nRestart the API server to load updated .env values.")


if __name__ == "__main__":
    main()

