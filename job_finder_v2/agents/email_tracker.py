"""
agents/email_tracker.py — Worker: Outlook IMAP sync + LLM classification.

Connects to Outlook (or any IMAP server — configurable) using an App
Password, scans the inbox for recent emails, matches each email to an
application in the DB, classifies it via Claude, and updates application
status accordingly.

Scheduled every 30 minutes while the server runs (api/main.py lifespan),
and callable on-demand from /api/email/sync.

Category-to-status map:
    interview_request → application.status = "interview_scheduled"
    rejection         → application.status = "rejected"
    offer             → application.status = "offer_received"
    followup_needed   → application stays as-is; action_needed flag set
    auto_reply        → application stays as-is; event stored for audit
"""

from __future__ import annotations

import email
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any

from config import settings
from db.store import Application, Store
from llm.client import LLMClient, load_prompt

log = logging.getLogger(__name__)


@dataclass
class EmailEventResult:
    """One classified email event returned from a sync run."""

    app_id: str | None
    company: str
    subject: str
    sender: str
    received_at: str
    category: str
    summary: str
    action_needed: bool
    urgency: str
    key_details: str | None
    raw_snippet: str


_STATUS_MAP = {
    "interview_request": "interview_scheduled",
    "rejection": "rejected",
    "offer": "offer_received",
}


class EmailTracker:
    """
    IMAP email scanner + LLM classifier for application replies.

    Usage:
        tracker = EmailTracker(store, llm)
        events = await tracker.sync(since_days=7)
    """

    def __init__(self, store: Store, llm: LLMClient | None = None) -> None:
        self.store = store
        self.llm = llm or LLMClient()
        self._prompt = load_prompt("email_classifier")

    async def sync(self, since_days: int = 7) -> list[EmailEventResult]:
        """
        Full sync: connect, fetch recent messages, classify, persist.

        Returns events created in this sync. Never raises — logs and
        returns [] on failure.
        """
        if not settings.email_configured:
            log.info("email_tracker.not_configured")
            return []

        try:
            from imapclient import IMAPClient  # type: ignore[import-not-found]
        except ImportError:
            log.error("email_tracker.imapclient_missing")
            return []

        since = datetime.now(timezone.utc) - timedelta(days=since_days)
        apps = self.store.list_applications()
        if not apps:
            log.info("email_tracker.no_apps")
            return []

        company_index = self._build_company_index(apps)

        log.info(
            "email_tracker.start",
            extra={
                "since_days": since_days,
                "apps": len(apps),
                "companies": len(company_index),
            },
        )

        raw_messages: list[dict[str, Any]] = []
        try:
            with IMAPClient(settings.imap_host, port=settings.imap_port, ssl=True) as client:
                try:
                    client.login(settings.outlook_email, settings.outlook_app_password)
                except Exception as login_exc:  # noqa: BLE001
                    err = str(login_exc)
                    # Microsoft has disabled IMAP Basic Auth for personal Outlook.com
                    # accounts — a proper fix requires OAuth2 (MSAL). Log a concise
                    # warning instead of a full traceback; the app keeps running.
                    if "BasicAuthBlocked" in err or "AuthFailed" in err:
                        log.warning(
                            "email_tracker.basic_auth_blocked",
                            extra={
                                "host": settings.imap_host,
                                "hint": "Microsoft blocks IMAP Basic Auth for personal Outlook accounts. Unset OUTLOOK_EMAIL in .env to silence, or switch to an OAuth2-enabled mailbox.",
                            },
                        )
                    else:
                        log.warning(
                            "email_tracker.login_failed",
                            extra={"host": settings.imap_host, "error": err[:200]},
                        )
                    return []
                client.select_folder("INBOX", readonly=True)
                uids = client.search(["SINCE", since.date()])
                if not uids:
                    log.info("email_tracker.no_recent_mail")
                    return []
                # Fetch at most 200 most recent to bound runtime
                for uid in uids[-200:]:
                    fetched = client.fetch([uid], ["RFC822", "INTERNALDATE"])
                    data = fetched.get(uid)
                    if not data:
                        continue
                    msg = email.message_from_bytes(data[b"RFC822"])
                    raw_messages.append(
                        {
                            "uid": uid,
                            "msg": msg,
                            "internal_date": data.get(b"INTERNALDATE"),
                        }
                    )
        except Exception as exc:  # noqa: BLE001 — network/other issues
            log.warning(
                "email_tracker.imap_error",
                extra={"host": settings.imap_host, "error": str(exc)[:200]},
            )
            return []

        results: list[EmailEventResult] = []
        for entry in raw_messages:
            msg = entry["msg"]
            event = await self._process_message(msg, entry["internal_date"], company_index)
            if event:
                results.append(event)

        log.info("email_tracker.complete", extra={"events": len(results)})
        return results

    # --- internal ---------------------------------------------------------

    async def _process_message(
        self,
        msg: email.message.Message,
        internal_date: Any,
        company_index: dict[str, Application],
    ) -> EmailEventResult | None:
        """Classify one message and persist the event + status update."""
        subject = _decode_header(msg.get("Subject", ""))
        from_raw = _decode_header(msg.get("From", ""))
        sender_name, sender_addr = parseaddr(from_raw)

        app = self._match_application(sender_addr, subject, company_index)
        if app is None:
            return None  # unrelated email

        body = _extract_body(msg)
        body_snippet = body[:2000]

        received_at = (
            parsedate_to_datetime(msg.get("Date", "")).isoformat()
            if msg.get("Date")
            else (internal_date.isoformat() if hasattr(internal_date, "isoformat") else None)
        )

        classified = await self._classify(subject, body_snippet, (app.job.company if app.job else ""))
        if not classified:
            return None

        event_data = {
            "app_id": app.id,
            "company": app.job.company if app.job else None,
            "subject": subject,
            "sender": sender_addr,
            "received_at": received_at,
            "category": classified.get("category", "unknown"),
            "summary": classified.get("summary", ""),
            "action_needed": bool(classified.get("action_needed", False)),
            "urgency": classified.get("urgency", "low"),
            "key_details": classified.get("key_details"),
            "raw_snippet": body_snippet[:500],
        }

        try:
            self.store.add_email_event(event_data)
        except Exception as exc:  # noqa: BLE001
            log.warning("email_tracker.store_error", extra={"error": str(exc)})

        # Update application status where categorical
        new_status = _STATUS_MAP.get(event_data["category"])
        if new_status and app.status not in {"submitted", "rejected", "offer_received"}:
            try:
                self.store.update_application(app.id, status=new_status)
                log.info(
                    "email_tracker.status_updated",
                    extra={
                        "app_id": app.id,
                        "old_status": app.status,
                        "new_status": new_status,
                        "category": event_data["category"],
                    },
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "email_tracker.status_update_failed",
                    extra={"app_id": app.id, "error": str(exc)},
                )

        return EmailEventResult(
            app_id=app.id,
            company=event_data["company"] or "",
            subject=subject,
            sender=sender_addr,
            received_at=received_at or "",
            category=event_data["category"],
            summary=event_data["summary"],
            action_needed=event_data["action_needed"],
            urgency=event_data["urgency"],
            key_details=event_data["key_details"],
            raw_snippet=event_data["raw_snippet"],
        )

    async def _classify(
        self, subject: str, body_snippet: str, company: str
    ) -> dict[str, Any] | None:
        """Call Claude Haiku to classify this email."""
        user_content = f"""COMPANY: {company}
SUBJECT: {subject}

BODY SNIPPET:
{body_snippet[:1800]}

Return the JSON described in your instructions. No prose.
"""
        try:
            text = await self.llm.classify(
                prompt_name="email_classifier",
                user_content=user_content,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("email_tracker.classify_failed", extra={"error": str(exc)})
            return None

        return _parse_json(text)

    # --- company/application matching ------------------------------------

    @staticmethod
    def _build_company_index(apps: list[Application]) -> dict[str, Application]:
        """
        Build a lookup dict keyed on company name tokens for fuzzy matching.

        The index is case-insensitive and includes both the full name and
        a simplified slug (no spaces, no common suffixes) so "Stripe" and
        "stripe.com" and "stripe, inc." all resolve to the same app.
        """
        index: dict[str, Application] = {}
        for app in apps:
            if not app.job or not app.job.company:
                continue
            name = app.job.company.strip().lower()
            index[name] = app
            slug = re.sub(r"[^a-z0-9]", "", name)
            if slug:
                index[slug] = app
            # Strip common suffixes
            cleaned = re.sub(r"\b(inc|corp|llc|ltd|co)\.?\b", "", name).strip()
            if cleaned and cleaned != name:
                index[cleaned] = app
                cleaned_slug = re.sub(r"[^a-z0-9]", "", cleaned)
                if cleaned_slug:
                    index[cleaned_slug] = app
        return index

    @staticmethod
    def _match_application(
        sender_addr: str,
        subject: str,
        company_index: dict[str, Application],
    ) -> Application | None:
        """Find an Application matching this email by domain or subject."""
        if not sender_addr or "@" not in sender_addr:
            return None
        domain = sender_addr.split("@", 1)[1].lower()
        domain_root = domain.split(".")[0]

        # 1. Exact domain root match against index slugs
        if domain_root in company_index:
            return company_index[domain_root]

        # 2. Subject contains a known company name
        subject_l = subject.lower()
        for key, app in company_index.items():
            if len(key) < 3:
                continue
            if key in subject_l:
                return app

        # 3. Full domain (e.g. "stripe.com") without suffix
        domain_slug = re.sub(r"[^a-z0-9]", "", domain_root)
        if domain_slug in company_index:
            return company_index[domain_slug]

        return None


def _decode_header(value: str) -> str:
    """Decode an RFC 2047 encoded email header to plain text."""
    if not value:
        return ""
    try:
        from email.header import decode_header

        parts = decode_header(value)
        out: list[str] = []
        for text, charset in parts:
            if isinstance(text, bytes):
                try:
                    out.append(text.decode(charset or "utf-8", errors="replace"))
                except LookupError:
                    out.append(text.decode("utf-8", errors="replace"))
            else:
                out.append(text)
        return "".join(out).strip()
    except Exception:  # noqa: BLE001
        return str(value)


def _extract_body(msg: email.message.Message) -> str:
    """Extract plain-text body from an email.Message. Prefers text/plain."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            disp = (part.get("Content-Disposition") or "").lower()
            if ct == "text/plain" and "attachment" not in disp:
                try:
                    payload = part.get_payload(decode=True) or b""
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
                except Exception:  # noqa: BLE001
                    continue
        # Fallback: first text/html, stripped
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    payload = part.get_payload(decode=True) or b""
                    charset = part.get_content_charset() or "utf-8"
                    html = payload.decode(charset, errors="replace")
                    return re.sub(r"<[^>]+>", " ", html)
                except Exception:  # noqa: BLE001
                    continue
        return ""
    try:
        payload = msg.get_payload(decode=True) or b""
        charset = msg.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")
    except Exception:  # noqa: BLE001
        return ""


def _parse_json(text: str) -> dict[str, Any] | None:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n", "", t)
        t = re.sub(r"\n```$", "", t)
    m = re.search(r"\{.*\}", t, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
