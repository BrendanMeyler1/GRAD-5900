"""
Status Tracker agent (Phase 3 Step 1).

Responsibilities:
- classify application-related inbox emails into canonical statuses
- detect stale applications with NO_RESPONSE_30D
- persist status updates into outcomes.db status_history
"""

from __future__ import annotations

import imaplib
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from llm_router.router import LLMRouter
from setup.init_db import init_outcomes_db

logger = logging.getLogger("job_finder.agents.status_tracker")

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "status_classifier.md"
DEFAULT_OUTCOMES_DB_PATH = str(Path(__file__).resolve().parents[1] / "data" / "outcomes.db")
ALLOWED_STATUSES = {
    "RECEIVED",
    "REJECTED",
    "INTERVIEW_SCHEDULED",
    "FOLLOW_UP_NEEDED",
    "OFFER",
    "NO_RESPONSE_30D",
}
_TERMINAL_OR_FINAL_STATUSES = {"REJECTED", "INTERVIEW_SCHEDULED", "OFFER", "NO_RESPONSE_30D"}
_GENERIC_JOB_BOARD_DOMAINS = (
    "linkedin.com",
    "indeed.com",
    "ziprecruiter.com",
    "glassdoor.com",
    "monster.com",
    "dice.com",
)
_COMPANY_TOKEN_STOPWORDS = {
    "inc",
    "llc",
    "ltd",
    "corp",
    "corporation",
    "company",
    "co",
    "group",
    "holdings",
    "technologies",
    "technology",
    "systems",
    "solutions",
    "services",
}


class InboxClient(Protocol):
    """Protocol for pluggable inbox backends (raw IMAP now, MCP server later)."""

    def search_inbox(
        self,
        since: datetime | None = None,
        folder: str = "INBOX",
        limit: int = 100,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return email summaries with keys like subject/body/date/message_id."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_prompt() -> str:
    raw = PROMPT_PATH.read_text(encoding="utf-8")
    if "## System Prompt" not in raw:
        return raw.strip()
    start = raw.index("## System Prompt") + len("## System Prompt")
    next_header = raw.find("\n## ", start)
    if next_header == -1:
        return raw[start:].strip()
    return raw[start:next_header].strip()


def _decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def _parse_iso_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _extract_plain_text(message_obj) -> str:
    payloads: list[str] = []
    if message_obj.is_multipart():
        for part in message_obj.walk():
            content_type = part.get_content_type()
            if content_type != "text/plain":
                continue
            raw = part.get_payload(decode=True)
            if raw is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                payloads.append(raw.decode(charset, errors="replace"))
            except LookupError:
                payloads.append(raw.decode("utf-8", errors="replace"))
    else:
        raw = message_obj.get_payload(decode=True)
        if raw:
            charset = message_obj.get_content_charset() or "utf-8"
            try:
                payloads.append(raw.decode(charset, errors="replace"))
            except LookupError:
                payloads.append(raw.decode("utf-8", errors="replace"))
    return "\n".join(payloads).strip()


def _normalize_status(raw_status: Any, email_record: dict[str, Any] | None = None) -> str:
    value = str(raw_status or "").strip().upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "INTERVIEW": "INTERVIEW_SCHEDULED",
        "INTERVIEW_INVITE": "INTERVIEW_SCHEDULED",
        "APPLICATION_RECEIVED": "RECEIVED",
        "RECEIPT": "RECEIVED",
        "FOLLOWUP_NEEDED": "FOLLOW_UP_NEEDED",
        "FOLLOW_UP": "FOLLOW_UP_NEEDED",
        "NO_RESPONSE_30": "NO_RESPONSE_30D",
        "NO_RESPONSE": "NO_RESPONSE_30D",
    }
    normalized = aliases.get(value, value)
    if normalized in ALLOWED_STATUSES:
        return normalized
    return _keyword_status_fallback(email_record)


def _keyword_status_fallback(email_record: dict[str, Any] | None = None) -> str:
    record = email_record or {}
    haystack = " ".join(
        [
            str(record.get("subject", "")),
            str(record.get("body", "")),
            str(record.get("body_excerpt", "")),
        ]
    ).lower()
    if any(term in haystack for term in ["unfortunately", "not moving forward", "rejected", "decline"]):
        return "REJECTED"
    if any(term in haystack for term in ["interview", "schedule", "calendar", "availability"]):
        return "INTERVIEW_SCHEDULED"
    if any(term in haystack for term in ["offer", "compensation", "salary package"]):
        return "OFFER"
    if any(term in haystack for term in ["application received", "thanks for applying", "thank you for applying"]):
        return "RECEIVED"
    return "FOLLOW_UP_NEEDED"


def _coerce_confidence(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, numeric))


def _normalize_signals(raw: Any) -> list[str]:
    signals: list[str] = []
    for item in raw or []:
        text = str(item).strip()
        if text:
            signals.append(text)
    return signals


def _app_identifier(app: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(app.get("application_id", "")),
        str(app.get("company", "")).strip(),
        str(app.get("role_title", "")).strip(),
    )


def _sender_domain(email_record: dict[str, Any]) -> str:
    sender_raw = str(email_record.get("from", "") or "").strip()
    _, parsed_email = parseaddr(sender_raw)
    source = (parsed_email or sender_raw).strip().lower()
    if "@" in source:
        source = source.rsplit("@", 1)[1]
    source = source.strip("<>\"' ,;")
    return source


def _company_tokens(company: str) -> list[str]:
    tokens: list[str] = []
    for token in re.split(r"[^a-z0-9]+", str(company or "").lower()):
        if len(token) < 3:
            continue
        if token in _COMPANY_TOKEN_STOPWORDS:
            continue
        tokens.append(token)
    return tokens


def _sender_domain_supports_company(email_record: dict[str, Any], company: str) -> bool:
    domain = _sender_domain(email_record)
    if not domain:
        return False
    flattened = re.sub(r"[^a-z0-9]", "", domain)
    for token in _company_tokens(company):
        if token in domain or token in flattened:
            return True
    return False


def _sender_is_generic_job_board(email_record: dict[str, Any]) -> bool:
    domain = _sender_domain(email_record)
    return any(domain.endswith(suffix) for suffix in _GENERIC_JOB_BOARD_DOMAINS)


def _email_matches_application(email_record: dict[str, Any], application: dict[str, Any]) -> bool:
    app_id, company, _ = _app_identifier(application)
    listing_id = str(application.get("listing_id", "")).strip()
    subject = str(email_record.get("subject", "")).lower()
    haystack = " ".join(
        [
            subject,
            str(email_record.get("body", "")),
            str(email_record.get("body_excerpt", "")),
        ]
    ).lower()
    if not haystack.strip():
        return False

    # Only treat strong identifiers as a match.
    # Role-title-only messages are too noisy (e.g., generic job digests).
    if app_id and app_id.lower() in haystack:
        return True
    if listing_id and listing_id.lower() in haystack:
        return True
    if not company:
        return False

    company_text = company.lower()
    company_in_haystack = company_text in haystack
    company_in_subject = company_text in subject
    sender_supports_company = _sender_domain_supports_company(email_record, company)
    sender_is_job_board = _sender_is_generic_job_board(email_record)

    score = 0
    if company_in_haystack:
        score += 1
    if company_in_subject:
        score += 1
    if sender_supports_company:
        score += 2
    if sender_is_job_board and not sender_supports_company:
        score -= 2

    return score >= 2


def _submitted_at(application: dict[str, Any]) -> datetime | None:
    submitted = _parse_iso_datetime(application.get("submitted_at"))
    if submitted:
        return submitted

    history = application.get("status_history", []) or []
    for item in reversed(history):
        if str(item.get("status", "")).upper() != "SUBMITTED":
            continue
        timestamp = _parse_iso_datetime(item.get("timestamp"))
        if timestamp:
            return timestamp
    return None


def _email_record_datetime(email_record: dict[str, Any]) -> datetime | None:
    """Parse an email record date value into a timezone-aware datetime when possible."""
    value = email_record.get("date")
    parsed = _parse_iso_datetime(value)
    if parsed is not None:
        return parsed

    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed_email_date = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if parsed_email_date.tzinfo is None:
        return parsed_email_date.replace(tzinfo=timezone.utc)
    return parsed_email_date.astimezone(timezone.utc)


def _email_is_on_or_after_submitted(
    email_record: dict[str, Any],
    submitted_at: datetime | None,
) -> bool:
    if submitted_at is None:
        return True
    email_dt = _email_record_datetime(email_record)
    if email_dt is None:
        # Keep unknown-date emails rather than dropping potentially relevant updates.
        return True
    return email_dt >= submitted_at


class IMAPInboxClient:
    """Direct IMAP inbox reader for status tracking."""

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        username: str | None = None,
        password: str | None = None,
        ssl: bool = True,
        auth_mode: str | None = None,
        oauth2_access_token: str | None = None,
        oauth2_refresh_token: str | None = None,
        oauth2_client_id: str | None = None,
        oauth2_client_secret: str | None = None,
        oauth2_tenant: str | None = None,
        oauth2_scope: str | None = None,
    ) -> None:
        self.host = host or os.getenv("IMAP_HOST")
        self.port = int(port or os.getenv("IMAP_PORT", "993"))
        self.username = username or os.getenv("IMAP_USER")
        self.password = password or os.getenv("IMAP_PASSWORD")
        self.ssl = ssl
        self.auth_mode = str(auth_mode or os.getenv("IMAP_AUTH_MODE", "basic")).strip().lower()
        self.oauth2_access_token = oauth2_access_token or os.getenv("IMAP_OAUTH2_ACCESS_TOKEN")
        self.oauth2_refresh_token = oauth2_refresh_token or os.getenv("IMAP_OAUTH2_REFRESH_TOKEN")
        self.oauth2_client_id = oauth2_client_id or os.getenv("IMAP_OAUTH2_CLIENT_ID")
        self.oauth2_client_secret = oauth2_client_secret or os.getenv("IMAP_OAUTH2_CLIENT_SECRET")
        self.oauth2_tenant = oauth2_tenant or os.getenv("IMAP_OAUTH2_TENANT", "consumers")
        self.oauth2_scope = oauth2_scope or os.getenv(
            "IMAP_OAUTH2_SCOPE",
            "https://outlook.office.com/IMAP.AccessAsUser.All offline_access",
        )

    def _build_xoauth2_string(self, access_token: str) -> bytes:
        return f"user={self.username}\x01auth=Bearer {access_token}\x01\x01".encode("utf-8")

    def _refresh_access_token(self) -> str | None:
        if not self.oauth2_refresh_token or not self.oauth2_client_id:
            return None

        token_url = (
            f"https://login.microsoftonline.com/{self.oauth2_tenant}/oauth2/v2.0/token"
        )
        payload = {
            "client_id": self.oauth2_client_id,
            "grant_type": "refresh_token",
            "refresh_token": self.oauth2_refresh_token,
            "scope": self.oauth2_scope,
        }
        if self.oauth2_client_secret:
            payload["client_secret"] = self.oauth2_client_secret

        encoded = urlencode(payload).encode("utf-8")
        request = Request(
            token_url,
            data=encoded,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=20) as response:
                raw = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw)
            token = str(parsed.get("access_token") or "").strip()
            if not token:
                logger.warning("OAuth2 token refresh response did not include access_token.")
                return None
            return token
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("OAuth2 token refresh failed: %s", exc)
            return None

    def _resolve_access_token(self) -> str | None:
        refreshed = self._refresh_access_token()
        if refreshed:
            self.oauth2_access_token = refreshed
            return refreshed

        inline = str(self.oauth2_access_token or "").strip()
        if inline:
            return inline
        return None

    def search_inbox(
        self,
        since: datetime | None = None,
        folder: str = "INBOX",
        limit: int = 100,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        if not self.host or not self.username:
            logger.warning("IMAP host/username missing; returning no inbox results.")
            return []

        since = since or (datetime.now(timezone.utc) - timedelta(days=30))
        since_key = since.strftime("%d-%b-%Y")

        client = imaplib.IMAP4_SSL(self.host, self.port) if self.ssl else imaplib.IMAP4(self.host, self.port)
        mode = self.auth_mode
        try:
            if mode in {"oauth2", "xoauth2"}:
                access_token = self._resolve_access_token()
                if not access_token:
                    logger.warning(
                        "OAuth2 mode enabled but no usable access token is available. "
                        "Set IMAP_OAUTH2_ACCESS_TOKEN or refresh-token settings."
                    )
                    return []
                xoauth2 = self._build_xoauth2_string(access_token)
                client.authenticate("XOAUTH2", lambda _: xoauth2)
            else:
                if not self.password:
                    logger.warning("IMAP password missing for basic auth; returning no inbox results.")
                    return []
                client.login(self.username, self.password)
            client.select(folder)
            if query:
                status, search_data = client.search(None, "SINCE", since_key, "TEXT", f'"{query}"')
            else:
                status, search_data = client.search(None, "SINCE", since_key)
            if status != "OK" or not search_data:
                return []

            ids = search_data[0].split()
            ids = ids[-max(1, int(limit)) :]
            emails: list[dict[str, Any]] = []
            for msg_id in ids:
                fetch_status, message_parts = client.fetch(msg_id, "(RFC822)")
                if fetch_status != "OK" or not message_parts:
                    continue
                raw_bytes = None
                for part in message_parts:
                    if isinstance(part, tuple) and len(part) == 2:
                        raw_bytes = part[1]
                        break
                if not raw_bytes:
                    continue

                msg = message_from_bytes(raw_bytes)
                subject = _decode_header_value(msg.get("Subject"))
                sender = _decode_header_value(msg.get("From"))
                message_id = _decode_header_value(msg.get("Message-ID")) or msg_id.decode(errors="ignore")
                date_raw = _decode_header_value(msg.get("Date"))
                body = _extract_plain_text(msg)
                emails.append(
                    {
                        "message_id": message_id,
                        "subject": subject,
                        "from": sender,
                        "date": date_raw,
                        "body": body[:20000],
                        "body_excerpt": body[:4000],
                    }
                )
            return emails
        except Exception as exc:
            if mode not in {"oauth2", "xoauth2"} and "BasicAuthBlocked" in str(exc):
                logger.warning(
                    "IMAP basic auth appears blocked by provider policy. "
                    "Set IMAP_AUTH_MODE=oauth2 and configure OAuth token settings."
                )
            logger.exception("IMAP inbox search failed.")
            return []
        finally:
            try:
                client.logout()
            except Exception:
                pass


class StatusTracker:
    """Classify inbox messages into application status updates."""

    def __init__(
        self,
        router: LLMRouter | None = None,
        inbox_client: InboxClient | None = None,
    ) -> None:
        self.router = router or LLMRouter()
        self.inbox_client = inbox_client
        self.system_prompt = _load_prompt()

    def classify_email(
        self,
        application: dict[str, Any],
        email_record: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "application": {
                "application_id": application.get("application_id"),
                "company": application.get("company"),
                "role_title": application.get("role_title"),
                "submitted_at": application.get("submitted_at"),
            },
            "email": {
                "message_id": email_record.get("message_id"),
                "subject": email_record.get("subject"),
                "from": email_record.get("from"),
                "date": email_record.get("date"),
                "body_excerpt": str(
                    email_record.get("body_excerpt") or email_record.get("body") or ""
                )[:4000],
            },
        }

        try:
            raw = self.router.route_json(
                task_type="status_classification",
                system_prompt=self.system_prompt,
                user_prompt=json.dumps(payload, indent=2),
            )
            status = _normalize_status(raw.get("status") or raw.get("outcome"), email_record=email_record)
            return {
                "application_id": application.get("application_id"),
                "listing_id": application.get("listing_id"),
                "status": status,
                "confidence": _coerce_confidence(raw.get("confidence")),
                "reason": str(raw.get("reason", "")).strip(),
                "matched_signals": _normalize_signals(raw.get("matched_signals")),
                "source_message_id": email_record.get("message_id"),
                "source_subject": email_record.get("subject"),
                "detected_at": _utc_now(),
                "classification_source": "llm",
            }
        except Exception as exc:
            logger.warning(
                "Status classification LLM call failed; using heuristic fallback. app=%s msg=%s err=%s",
                application.get("application_id"),
                email_record.get("message_id"),
                exc,
            )
            heuristic = _keyword_status_fallback(email_record)
            return {
                "application_id": application.get("application_id"),
                "listing_id": application.get("listing_id"),
                "status": heuristic,
                "confidence": 0.55,
                "reason": "LLM classification unavailable; used keyword heuristic fallback.",
                "matched_signals": ["heuristic_fallback"],
                "source_message_id": email_record.get("message_id"),
                "source_subject": email_record.get("subject"),
                "detected_at": _utc_now(),
                "classification_source": "heuristic",
            }

    def scan_status_updates(
        self,
        applications: list[dict[str, Any]],
        emails: list[dict[str, Any]] | None = None,
        since: datetime | None = None,
        query: str | None = None,
        include_no_response: bool = True,
        no_response_days: int = 30,
    ) -> list[dict[str, Any]]:
        if emails is None:
            if self.inbox_client is None:
                self.inbox_client = IMAPInboxClient()
            emails = self.inbox_client.search_inbox(since=since, query=query)

        updates: list[dict[str, Any]] = []
        classified_apps: set[str] = set()
        for app in applications:
            app_id = str(app.get("application_id", "")).strip()
            if not app_id:
                continue
            submitted_at = _submitted_at(app)
            matches = [
                email
                for email in emails
                if _email_matches_application(email, app)
                and _email_is_on_or_after_submitted(email, submitted_at)
            ]
            if not matches:
                continue
            update = self.classify_email(app, matches[-1])
            updates.append(update)
            classified_apps.add(app_id)

        if include_no_response:
            updates.extend(
                find_no_response_updates(
                    applications=applications,
                    threshold_days=no_response_days,
                    skip_application_ids=classified_apps,
                )
            )
        return updates


def find_no_response_updates(
    applications: list[dict[str, Any]],
    threshold_days: int = 30,
    as_of: datetime | None = None,
    skip_application_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    now = as_of or datetime.now(timezone.utc)
    threshold = max(1, int(threshold_days))
    skip_application_ids = skip_application_ids or set()

    updates: list[dict[str, Any]] = []
    for app in applications:
        app_id = str(app.get("application_id", "")).strip()
        if not app_id or app_id in skip_application_ids:
            continue

        status = str(app.get("status", "")).upper().strip()
        if status in _TERMINAL_OR_FINAL_STATUSES:
            continue

        submitted = _submitted_at(app)
        if submitted is None:
            continue

        age_days = (now - submitted).days
        if age_days < threshold:
            continue

        updates.append(
            {
                "application_id": app_id,
                "listing_id": app.get("listing_id"),
                "status": "NO_RESPONSE_30D",
                "confidence": 1.0,
                "reason": f"No response detected for {age_days} days since submission.",
                "matched_signals": ["time_since_submission"],
                "source_message_id": None,
                "source_subject": None,
                "detected_at": _utc_now(),
            }
        )
    return updates


def persist_status_updates(
    updates: list[dict[str, Any]],
    db_path: str = DEFAULT_OUTCOMES_DB_PATH,
) -> int:
    """
    Persist status updates into applications + status_history tables.

    Returns number of application rows updated.
    """
    init_outcomes_db(db_path=db_path)
    updated_rows = 0
    with sqlite3.connect(db_path) as conn:
        for update in updates:
            app_id = str(update.get("application_id", "")).strip()
            status = _normalize_status(update.get("status"))
            timestamp = str(update.get("detected_at") or _utc_now())
            if not app_id:
                continue

            row = conn.execute(
                "SELECT status FROM applications WHERE application_id = ?",
                (app_id,),
            ).fetchone()
            if not row:
                continue

            current_status = str(row[0] or "").upper()
            if current_status != status:
                conn.execute(
                    "UPDATE applications SET status = ? WHERE application_id = ?",
                    (status, app_id),
                )
                updated_rows += 1

            last_history = conn.execute(
                """
                SELECT status FROM status_history
                WHERE application_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (app_id,),
            ).fetchone()
            if last_history and str(last_history[0] or "").upper() == status:
                continue

            conn.execute(
                """
                INSERT INTO status_history (application_id, status, timestamp)
                VALUES (?, ?, ?)
                """,
                (app_id, status, timestamp),
            )
    return updated_rows


def track_status_updates(
    applications: list[dict[str, Any]],
    router: LLMRouter | None = None,
    inbox_client: InboxClient | None = None,
    emails: list[dict[str, Any]] | None = None,
    since: datetime | None = None,
    query: str | None = None,
    include_no_response: bool = True,
    no_response_days: int = 30,
    persist: bool = False,
    outcomes_db_path: str = DEFAULT_OUTCOMES_DB_PATH,
) -> list[dict[str, Any]]:
    """Convenience entry-point for one-shot status scans."""
    tracker = StatusTracker(router=router, inbox_client=inbox_client)
    updates = tracker.scan_status_updates(
        applications=applications,
        emails=emails,
        since=since,
        query=query,
        include_no_response=include_no_response,
        no_response_days=no_response_days,
    )
    if persist and updates:
        persist_status_updates(updates=updates, db_path=outcomes_db_path)
    return updates
