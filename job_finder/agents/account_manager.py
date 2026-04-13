"""
Account Manager agent.

Decides ATS account action (reuse/login/create/escalate), persists encrypted
credentials in AccountVault, and binds verification sessions.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from errors import AccountError
from llm_router.router import LLMRouter
from pii.account_vault import AccountVault

logger = logging.getLogger("job_finder.agents.account_manager")

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "account_manager.md"


def _load_prompt() -> str:
    raw = PROMPT_PATH.read_text(encoding="utf-8")
    if "## System Prompt" not in raw:
        return raw.strip()
    start = raw.index("## System Prompt") + len("## System Prompt")
    next_header = raw.find("\n## ", start)
    if next_header == -1:
        return raw[start:].strip()
    return raw[start:next_header].strip()


def _safe_signals(signals: dict[str, Any] | None) -> dict[str, bool]:
    payload = signals or {}
    return {
        "captcha_detected": bool(payload.get("captcha_detected", False)),
        "requires_2fa": bool(payload.get("requires_2fa", False)),
        "verification_required": bool(payload.get("verification_required", False)),
    }


def _llm_decide_action(
    company: str,
    ats_type: str,
    account_state: dict[str, Any] | None,
    signals: dict[str, bool],
    router: LLMRouter,
) -> dict[str, Any] | None:
    """
    Optional LLM policy assist for action choice.
    """
    try:
        response = router.route_json(
            task_type="account_management",
            system_prompt=_load_prompt(),
            user_prompt=json.dumps(
                {
                    "company": company,
                    "ats_type": ats_type,
                    "account_state": account_state or {},
                    "signals": signals,
                },
                indent=2,
            ),
        )
        action = str(response.get("action", "")).strip()
        if action not in {"use_existing", "login", "create_account", "escalate"}:
            return None
        return response
    except Exception as exc:
        logger.warning("Account Manager LLM decision unavailable: %s", exc)
        return None


def manage_account(
    company: str,
    ats_type: str,
    username: str | None = None,
    password: str | None = None,
    session_cookies: dict[str, Any] | list[Any] | None = None,
    browser_context: str | None = None,
    vault: AccountVault | None = None,
    signals: dict[str, Any] | None = None,
    router: LLMRouter | None = None,
    allow_llm_assist: bool = False,
) -> dict[str, Any]:
    """
    Decide and execute account/session handling for a listing's ATS.

    Returns:
      {
        "account_status": "existing|created|failed",
        "action": "use_existing|login|create_account|escalate",
        "account_id": "...",
        "session_context_id": "...",
        "requires_human": bool,
        "reason": "..."
      }
    """
    vault = vault or AccountVault()
    normalized_signals = _safe_signals(signals)
    existing = vault.get_account(company=company, ats_type=ats_type)

    # Hard blockers always escalate
    if normalized_signals["captcha_detected"] or normalized_signals["requires_2fa"]:
        return {
            "account_status": "failed",
            "action": "escalate",
            "account_id": existing["account_id"] if existing else None,
            "session_context_id": existing.get("browser_context") if existing else None,
            "requires_human": True,
            "reason": "Automation blocked by CAPTCHA or 2FA.",
        }

    llm_decision = None
    if allow_llm_assist and router is not None:
        llm_decision = _llm_decide_action(
            company=company,
            ats_type=ats_type,
            account_state=existing,
            signals=normalized_signals,
            router=router,
        )

    # Existing account path
    if existing:
        status = str(existing.get("status", "active"))
        if status == "active":
            vault.touch_last_used(existing["account_id"])
            if session_cookies:
                vault.update_session(
                    account_id=existing["account_id"],
                    session_cookies=session_cookies,
                    browser_context=browser_context,
                )
            return {
                "account_status": "existing",
                "action": "use_existing",
                "account_id": existing["account_id"],
                "session_context_id": browser_context or existing.get("browser_context"),
                "requires_human": False,
                "reason": "Reusing existing active account/session.",
            }

        # Locked / verification-limbo states
        return {
            "account_status": "failed",
            "action": "escalate",
            "account_id": existing["account_id"],
            "session_context_id": existing.get("browser_context"),
            "requires_human": True,
            "reason": f"Account status is '{status}' and needs manual intervention.",
        }

    # ATS platforms that don't typically use accounts for applications
    if ats_type in {"greenhouse", "lever", "ashby", "unknown", "smartrecruiters", "jobvite"}:
        return {
            "account_status": "bypassed",
            "action": "use_existing",
            "account_id": None,
            "session_context_id": None,
            "requires_human": False,
            "reason": f"{ats_type.capitalize()} does not require an account.",
        }

    # New account path for ATS that do require accounts (Workday, iCIMS, etc.)
    if not username or not password:
        raise AccountError(
            f"No existing account found for {company} ({ats_type}) and missing username/password for account creation."
        )

    # If LLM explicitly requests escalation, honor it
    if llm_decision and llm_decision.get("action") == "escalate":
        return {
            "account_status": "failed",
            "action": "escalate",
            "account_id": None,
            "session_context_id": None,
            "requires_human": True,
            "reason": str(llm_decision.get("reason") or "Escalated by policy."),
        }

    account_id = vault.store_account(
        company=company,
        ats_type=ats_type,
        username=username,
        password=password,
        session_cookies=session_cookies,
        browser_context=browser_context,
        status="active",
    )
    return {
        "account_status": "created",
        "action": "create_account",
        "account_id": account_id,
        "session_context_id": browser_context,
        "requires_human": normalized_signals["verification_required"],
        "reason": "Created new ATS account record in encrypted vault.",
    }


def bind_verification_session(
    account_id: str,
    session_cookies: dict[str, Any] | list[Any],
    browser_context: str | None = None,
    vault: AccountVault | None = None,
) -> bool:
    """
    Verification Session Binder:
    Persist cookies/context so verification links reopen the same authenticated session.
    """
    vault = vault or AccountVault()
    return vault.update_session(
        account_id=account_id,
        session_cookies=session_cookies,
        browser_context=browser_context,
    )
