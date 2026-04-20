"""
filler/universal.py — Universal Playwright + Claude-vision form filler.

Critical flows:
  1. Land on apply_url  →  detect if job-listing page  →  click Apply/CTA  →
     handle new-tab OR same-page navigation  →  reach actual form.
  2. Fill every visible field (vision-guided agent loop with Claude).
  3. Multi-page forms: click Next/Continue between pages.
  4. File upload (resume PDF via input[type=file]).
  5. Free-text custom questions: Claude generates answers from profile + JD.
  6. Shadow mode : fill everything, stop BEFORE final Submit.
  7. Live   mode : fill everything, click Submit, confirm success page.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from config import settings
from llm.client import LLMClient, load_prompt
from scrapers.base import detect_ats_type

log = logging.getLogger(__name__)

FillStatus = Literal["complete", "shadow_complete", "needs_manual", "failed", "skipped"]

# Ordered list of CTA button texts that mean "start the application".
# Checked case-insensitively; first match wins.
_APPLY_CTA_TEXTS = [
    "Apply for this job",
    "Apply for this position",
    "Apply for position",
    "Apply Now",
    "Apply Online",
    "Apply Today",
    "Apply to this job",
    "I'm Interested",
    "I am interested",
    "Quick Apply",
    "Easy Apply",
    "Start Application",
    "Begin Application",
    "Apply",
]

# Submit button labels (live mode).
_SUBMIT_TEXTS = [
    "Submit Application",
    "Submit application",
    "Submit my application",
    "Submit",
    "Send Application",
    "Complete Application",
    "Finish Application",
    "Apply Now",
    "Apply",
]


@dataclass
class FillResult:
    status: FillStatus
    screenshots: list[str] = field(default_factory=list)
    fill_log: list[dict[str, Any]] = field(default_factory=list)
    custom_qa: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    submitted: bool = False
    duration_ms: int = 0


class UniversalFiller:
    """
    One filler, any ATS.

    Instantiate once per process (lazy browser spawn).  Call `fill()` for
    each application.  Call `close()` at shutdown.
    """

    def __init__(
        self,
        llm: LLMClient | None = None,
        headless: bool | None = None,
        max_steps: int = 30,
    ) -> None:
        self.llm = llm or LLMClient()
        self.headless = settings.headless if headless is None else headless
        self.max_steps = max_steps
        self._browser = None
        self._playwright = None
        self._system_prompt = load_prompt("form_filler")

    async def close(self) -> None:
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:  # noqa: BLE001
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:  # noqa: BLE001
                pass
            self._playwright = None

    # ──────────────────────────────────────────────────────────────────────
    # Public entrypoint
    # ──────────────────────────────────────────────────────────────────────

    async def fill(
        self,
        apply_url: str,
        profile: Any,
        resume_path: str,
        cover_letter: str,
        app_id: str,
        job_description: str = "",
        submit: bool = False,
    ) -> FillResult:
        """
        Navigate to apply_url, click through to the actual form, fill it,
        and (in live mode) submit it.

        Never raises — always returns a FillResult.
        """
        start = time.monotonic()
        screenshots_dir = Path(settings.screenshots_dir) / app_id
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        ats_type = detect_ats_type(apply_url)

        log.info(
            "filler.start",
            extra={
                "app_id": app_id,
                "apply_url": apply_url,
                "ats_type": ats_type,
                "submit": submit,
            },
        )

        if settings.dev_mode:
            result = await self._dev_mode_fill(app_id, apply_url, submit, screenshots_dir)
            result.duration_ms = int((time.monotonic() - start) * 1000)
            return result

        try:
            await self._ensure_browser()
        except Exception as exc:  # noqa: BLE001
            log.exception("filler.browser_launch_failed", extra={"error": str(exc)})
            return FillResult(
                status="failed",
                error=f"Browser failed to launch: {exc}",
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        context = None
        fill_log: list[dict[str, Any]] = []
        screenshots: list[str] = []
        custom_qa: dict[str, str] = {}
        page = None

        try:
            context = await self._browser.new_context(  # type: ignore[union-attr]
                viewport={"width": 1440, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                accept_downloads=True,
            )
            page = await context.new_page()

            # ── Step 1: Navigate to the apply URL ────────────────────────
            log.info("filler.navigating", extra={"app_id": app_id, "url": apply_url})
            await page.goto(apply_url, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(2.0)

            shot = await self._screenshot(page, screenshots_dir, len(screenshots))
            screenshots.append(shot)
            fill_log.append({"step": "navigate", "url": apply_url})

            # ── Step 1b: Early login-wall check (before clicking anything) ─
            early_check = await self._preflight(page)
            if early_check["verdict"] == "login_required":
                log.warning(
                    "filler.login_wall_on_landing",
                    extra={"app_id": app_id, "url": page.url},
                )
                return FillResult(
                    status="skipped",
                    screenshots=screenshots,
                    fill_log=fill_log,
                    error=early_check["reason"],
                    duration_ms=int((time.monotonic() - start) * 1000),
                )

            # ── Step 2: Detect job-listing page → click Apply CTA ────────
            page = await self._navigate_to_form(
                page, context, fill_log, screenshots, screenshots_dir
            )

            # Screenshot the form page
            shot = await self._screenshot(page, screenshots_dir, len(screenshots))
            screenshots.append(shot)

            # ── Step 3: Preflight on the form page ───────────────────────
            preflight = await self._preflight(page)
            fill_log.append({"step": "preflight", **preflight})
            if preflight["verdict"] == "indeed_login":
                # Handle Indeed email + OTP authentication inline
                log.info("filler.indeed_login_page", extra={"app_id": app_id, "url": page.url})
                fill_log.append({"step": "indeed_login", "url": page.url})
                auth_ok = await self._handle_indeed_login(page, profile, fill_log)
                if auth_ok:
                    # Reload snapshot after auth
                    shot = await self._screenshot(page, screenshots_dir, len(screenshots))
                    screenshots.append(shot)
                    preflight = await self._preflight(page)
                    fill_log.append({"step": "preflight_post_auth", **preflight})
                else:
                    return FillResult(
                        status="needs_manual",
                        screenshots=screenshots,
                        fill_log=fill_log,
                        error=(
                            "Indeed login required. Enter your email in the browser and "
                            "configure OUTLOOK_EMAIL + OUTLOOK_APP_PASSWORD in .env so the "
                            "app can fetch the one-time verification code automatically."
                        ),
                        duration_ms=int((time.monotonic() - start) * 1000),
                    )

            if preflight["verdict"] != "ok":
                status_map = {
                    "closed": ("skipped", "Listing closed"),
                    "login_required": ("skipped", preflight["reason"]),
                    "unsupported": ("needs_manual", preflight["reason"]),
                }
                final_status, error_msg = status_map.get(
                    preflight["verdict"], ("failed", preflight.get("reason", "Unknown preflight failure"))
                )
                log.warning(
                    "filler.preflight_blocked",
                    extra={
                        "app_id": app_id,
                        "verdict": preflight["verdict"],
                        "url": page.url,
                    },
                )
                return FillResult(
                    status=final_status,
                    screenshots=screenshots,
                    fill_log=fill_log,
                    error=error_msg,
                    duration_ms=int((time.monotonic() - start) * 1000),
                )

            # ── Step 4: Build profile string for Claude ───────────────────
            profile_context = (
                profile.to_context_string()
                if hasattr(profile, "to_context_string")
                else str(profile)
            )

            # ── Step 5: Agent fill loop ───────────────────────────────────
            stalled_count = 0
            for step in range(self.max_steps):
                shot = await self._screenshot(page, screenshots_dir, len(screenshots))
                screenshots.append(shot)

                snap = await self._page_snapshot(page)
                plan = await self._ask_claude_for_actions(
                    page_snapshot=snap,
                    screenshot_path=shot,
                    profile_context=profile_context,
                    resume_path=resume_path,
                    cover_letter=cover_letter,
                    job_description=job_description,
                    fill_log=fill_log,
                    submit=submit,
                )
                fill_log.append({"step": step, "summary": plan.get("summary", "")})

                if plan.get("done"):
                    log.info(
                        "filler.agent_done",
                        extra={"app_id": app_id, "step": step, "reason": plan.get("reason")},
                    )
                    break

                actions = plan.get("actions", [])
                if not actions:
                    stalled_count += 1
                    if stalled_count >= 3:
                        log.warning("filler.stalled", extra={"app_id": app_id, "step": step})
                        break
                    await asyncio.sleep(1.0)
                    continue
                else:
                    stalled_count = 0

                for action in actions:
                    outcome = await self._execute_action(
                        page, action, resume_path, custom_qa, context
                    )
                    fill_log.append(
                        {
                            "step": step,
                            "action": action.get("kind"),
                            "target": action.get("label") or action.get("selector"),
                            "result": outcome,
                        }
                    )
                    await asyncio.sleep(0.5)

                # After each batch, check if we hit an auth wall mid-form
                mid_check = await self._preflight(page)
                if mid_check["verdict"] == "indeed_login":
                    log.info(
                        "filler.indeed_login_mid_form",
                        extra={"app_id": app_id, "step": step},
                    )
                    fill_log.append({"step": "indeed_login_mid_form", "url": page.url})
                    auth_ok = await self._handle_indeed_login(page, profile, fill_log)
                    if not auth_ok:
                        log.warning("filler.indeed_login_failed", extra={"app_id": app_id})
                        break  # Exit fill loop — leave as needs_manual
                    shot = await self._screenshot(page, screenshots_dir, len(screenshots))
                    screenshots.append(shot)

                # Wait a moment for page updates
                await asyncio.sleep(0.3)

            # Final screenshot before submit decision
            shot = await self._screenshot(page, screenshots_dir, len(screenshots))
            screenshots.append(shot)

            # ── Step 6: Submit (live mode only) ──────────────────────────
            submitted = False
            if submit:
                submitted, submit_err = await self._click_submit(page)
                fill_log.append(
                    {"step": "submit", "submitted": submitted, "error": submit_err}
                )
                if submitted:
                    await asyncio.sleep(3.0)
                    shot = await self._screenshot(page, screenshots_dir, len(screenshots))
                    screenshots.append(shot)
                    log.info("filler.submitted", extra={"app_id": app_id})

            status: FillStatus = (
                ("complete" if submitted else "needs_manual") if submit else "shadow_complete"
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            log.info(
                "filler.complete",
                extra={
                    "app_id": app_id,
                    "status": status,
                    "steps": len(fill_log),
                    "screenshots": len(screenshots),
                    "duration_ms": duration_ms,
                },
            )
            return FillResult(
                status=status,
                screenshots=screenshots,
                fill_log=fill_log,
                custom_qa=custom_qa,
                submitted=submitted,
                duration_ms=duration_ms,
            )

        except Exception as exc:  # noqa: BLE001
            log.exception("filler.unhandled_error", extra={"app_id": app_id, "error": str(exc)})
            return FillResult(
                status="failed",
                screenshots=screenshots,
                fill_log=fill_log,
                custom_qa=custom_qa,
                error=str(exc),
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:  # noqa: BLE001
                    pass

    # ──────────────────────────────────────────────────────────────────────
    # Step 2 — Navigate from job listing to actual form
    # ──────────────────────────────────────────────────────────────────────

    async def _navigate_to_form(
        self,
        page: Any,
        context: Any,
        fill_log: list,
        screenshots: list,
        screenshots_dir: Path,
    ) -> Any:
        """
        Detect if the current page is a job listing (not a form) and click
        the "Apply" / "I'm Interested" CTA to reach the actual application form.

        Handles:
          - Same-page navigation (most common — Greenhouse, Lever, Jobvite)
          - New-tab/window opening (some ATSes)
          - Greenhouse /apply URL shortcut
        """
        current_url = page.url
        log.info("filler.checking_for_apply_cta", extra={"url": current_url})

        # Fast path: URL already looks like a form page
        form_url_signals = ["/apply", "/application", "/job-application", "apply?"]
        if any(sig in current_url.lower() for sig in form_url_signals):
            # May already be on the form
            form_count = await page.evaluate(
                "document.querySelectorAll('form, input:not([type=hidden])').length"
            )
            if form_count > 2:
                fill_log.append({"step": "navigate", "action": "already_on_form"})
                return page

        # Check form elements count — if already has plenty, we're done
        form_elements = await page.evaluate(
            """
            () => document.querySelectorAll(
                'input:not([type=hidden]):not([type=submit]), textarea, select'
            ).length
            """
        )
        if form_elements >= 3:
            fill_log.append(
                {"step": "navigate", "action": "form_already_present", "fields": form_elements}
            )
            return page

        # Look for Apply CTA button/link
        for cta_text in _APPLY_CTA_TEXTS:
            found_page = await self._try_click_apply_cta(
                page, context, cta_text, fill_log
            )
            if found_page is not None:
                await asyncio.sleep(1.0)

                # Check if CTA click landed on a login wall — bail out immediately
                # instead of trying to click more things
                landed_url = found_page.url.lower()
                login_url_signals = (
                    "accounts.google.com",
                    "login.microsoftonline.com",
                    "linkedin.com/login",
                    "linkedin.com/checkpoint",
                    "indeed.com/account",
                    "indeed.com/auth",
                    "secure.indeed.com",
                    "auth.indeed.com",
                    "login.indeed.com",
                    "/sso/",
                    "/oauth/",
                    "/signin",
                    "/sign-in",
                    "/login",
                )
                if any(sig in landed_url for sig in login_url_signals):
                    log.warning(
                        "filler.cta_led_to_login_wall",
                        extra={"cta": cta_text, "url": found_page.url},
                    )
                    fill_log.append({
                        "step": "navigate",
                        "action": f"cta '{cta_text}' led to login/auth page",
                        "url": found_page.url,
                    })
                    # Return the page — fill() preflight will classify and
                    # dispatch to the appropriate handler (Indeed OTP or hard skip)
                    return found_page

                # Take screenshot of resulting page
                shot = await self._screenshot(found_page, screenshots_dir, len(screenshots))
                screenshots.append(shot)
                log.info(
                    "filler.reached_form",
                    extra={"via": cta_text, "url": found_page.url},
                )
                return found_page

        # Greenhouse fallback: try appending /apply to URL
        if "boards.greenhouse.io" in current_url and "/apply" not in current_url:
            try:
                form_url = current_url.rstrip("/") + "/apply"
                await page.goto(form_url, wait_until="domcontentloaded", timeout=15_000)
                await asyncio.sleep(1.5)
                fill_log.append({"step": "navigate", "action": "greenhouse_append_apply"})
                return page
            except Exception:  # noqa: BLE001
                pass

        # No CTA found — already on form or no apply button
        log.warning(
            "filler.no_apply_cta_found",
            extra={"url": current_url, "visible_fields": form_elements},
        )
        fill_log.append({"step": "navigate", "action": "no_cta_found", "fields": form_elements})
        return page

    async def _try_click_apply_cta(
        self,
        page: Any,
        context: Any,
        cta_text: str,
        fill_log: list,
    ) -> Any | None:
        """
        Try to find and click a CTA button/link with the given text.

        Returns the page that has the form (may be a new tab), or None if
        the CTA was not found.
        """
        # Try button first, then link, then any clickable element
        locator_fns = [
            lambda p, t: p.get_by_role("button", name=t, exact=False),
            lambda p, t: p.get_by_role("link", name=t, exact=False),
            lambda p, t: p.locator(f'a:has-text("{t}")'),
            lambda p, t: p.locator("[data-qa*='apply']") if "apply" in t.lower() else None,
        ]
        for fn in locator_fns:
            try:
                loc = fn(page, cta_text)
                if loc is None:
                    continue
                count = await loc.count()
                if not count:
                    continue
                el = loc.first
                if not await el.is_visible():
                    continue

                log.info("filler.clicking_cta", extra={"text": cta_text})
                url_before = page.url

                # Set up new-page listener BEFORE clicking (avoids race condition),
                # then click and check for new tab vs same-page navigation.
                new_page: Any = None
                try:
                    async with context.expect_page(timeout=3000) as page_info:
                        await el.click()
                    new_page = await page_info.value
                    await new_page.wait_for_load_state("domcontentloaded", timeout=15_000)
                    await asyncio.sleep(1.0)
                    fill_log.append(
                        {"step": "navigate", "action": f"clicked {cta_text}", "result": "new_tab"}
                    )
                    return new_page
                except Exception as click_exc:
                    # Two possibilities:
                    # A) The click itself failed → re-raise to outer loop so we try next locator
                    # B) Timeout waiting for new tab → same-page navigation
                    # Distinguish by checking if the URL changed or form appeared
                    url_after = page.url
                    form_after = await page.evaluate(
                        "() => document.querySelectorAll('input:not([type=hidden]):not([type=submit]), textarea').length"
                    )
                    if url_after == url_before and form_after < 2:
                        # Click likely failed — re-raise so outer loop tries next locator
                        raise

                    # Same-page navigation happened — wait for it to settle
                    try:
                        # Poll for URL change (up to 5 seconds)
                        for _ in range(10):
                            await asyncio.sleep(0.5)
                            if page.url != url_before:
                                break
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        await page.wait_for_load_state("networkidle", timeout=10_000)
                    except Exception:  # noqa: BLE001
                        await asyncio.sleep(2.0)  # fallback wait
                    fill_log.append(
                        {
                            "step": "navigate",
                            "action": f"clicked {cta_text}",
                            "result": "same_page",
                            "url_after": page.url,
                        }
                    )
                    return page
            except Exception:  # noqa: BLE001
                continue
        return None

    # ──────────────────────────────────────────────────────────────────────
    # Browser lifecycle
    # ──────────────────────────────────────────────────────────────────────

    async def _ensure_browser(self) -> None:
        if self._browser is not None:
            # Check browser is still alive
            try:
                _ = self._browser.contexts
                return
            except Exception:  # noqa: BLE001
                self._browser = None
                self._playwright = None

        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError(
                "playwright is required. Run: pip install playwright && "
                "playwright install chromium"
            ) from exc

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            slow_mo=settings.slow_mo,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                # Suppress Chrome first-run / sync dialogs
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-sync",
                "--disable-features=ChromeWhatsNew,Translate",
                "--disable-extensions",
                # Suppress the "Sign in to Chrome" OS-level dialog
                "--disable-background-networking",
                "--disable-client-side-phishing-detection",
                "--disable-default-apps",
                "--disable-hang-monitor",
                "--disable-popup-blocking",
                "--disable-prompt-on-repost",
                "--metrics-recording-only",
                "--password-store=basic",
                "--use-mock-keychain",
                "--disable-component-update",
            ],
        )
        log.info(
            "filler.browser_launched",
            extra={"headless": self.headless, "slow_mo": settings.slow_mo},
        )

    # ──────────────────────────────────────────────────────────────────────
    # Preflight
    # ──────────────────────────────────────────────────────────────────────

    async def _preflight(self, page: Any) -> dict[str, Any]:
        """
        Check for blockers before filling the form:
          - Login/auth walls (Google SSO, Indeed account, LinkedIn login, etc.)
          - Closed/inactive listings
          - Non-job pages (404, error pages)

        Returns a dict with keys:
          verdict: "ok" | "closed" | "login_required" | "unsupported"
          reason:  human-readable reason string
        """
        current_url = page.url.lower()

        # ── Indeed auth page — handle with email OTP (not a hard skip) ──────
        indeed_auth_signals = (
            "indeed.com/account",
            "indeed.com/auth",
            "secure.indeed.com",
            "auth.indeed.com",
            "login.indeed.com",
            "indeed.com/signin",
            "indeed.com/login",
        )
        if any(sig in current_url for sig in indeed_auth_signals):
            return {"verdict": "indeed_login", "reason": "indeed_auth_page"}

        # ── Other hard login walls (non-Indeed) — skip ─────────────────────
        auth_url_signals = (
            "accounts.google.com",
            "login.microsoftonline.com",
            "linkedin.com/login",
            "linkedin.com/checkpoint",
            "smartrecruiters.com/candidate/login",
            "workday.com/login",
            "okta.com/login",
            "auth0.com",
        )
        if any(sig in current_url for sig in auth_url_signals):
            return {
                "verdict": "login_required",
                "reason": (
                    f"Page redirected to a login wall: {page.url}. "
                    "This job requires account authentication before applying. "
                    "Try finding a direct application link (Greenhouse/Lever board) instead."
                ),
            }

        try:
            body_text = await page.evaluate(
                "document.body ? document.body.innerText.toLowerCase() : ''"
            )
        except Exception:  # noqa: BLE001
            body_text = ""

        # ── Indeed account page detected via content ──────────────────────
        indeed_content_signals = (
            "create an indeed account",
            "sign in to indeed",
            "ready to take the next step",
            "continue with google",   # indeed's "or" separator with google login
        )
        # Only flag as indeed_login if indeed.com is somewhere in the URL OR
        # the page has the Indeed logo/brand (most reliable is URL check above,
        # but some ATSes open Indeed in an iframe)
        if any(sig in body_text for sig in indeed_content_signals) and "indeed" in current_url:
            return {"verdict": "indeed_login", "reason": "indeed_account_page_content"}

        # ── Other login wall detection (page content) ─────────────────────
        login_signals = (
            "sign in with google",
            "sign in with linkedin",
            "sign in with facebook",
            "sign in with apple",
            "sign in to continue",
            "log in to continue",
            "continue with linkedin",
            "sign in to apply",
            "log in to apply",
            "you must be logged in",
            "please sign in",
        )
        if any(sig in body_text for sig in login_signals):
            return {
                "verdict": "login_required",
                "reason": (
                    f"Application requires login at {page.url}. "
                    "This job uses a platform (LinkedIn/Google) that requires "
                    "an existing account to apply. Try a direct company board link instead."
                ),
            }

        # ── Closed listing detection ───────────────────────────────────────
        closed_signals = (
            "no longer accepting",
            "position has been filled",
            "this job is no longer",
            "this posting is closed",
            "applications are closed",
            "job is closed",
            "this job has expired",
            "listing has been removed",
        )
        if any(sig in body_text for sig in closed_signals):
            return {"verdict": "closed", "reason": "posting_closed"}

        return {"verdict": "ok", "reason": "proceed"}

    # ──────────────────────────────────────────────────────────────────────
    # Screenshots + DOM snapshot
    # ──────────────────────────────────────────────────────────────────────

    async def _screenshot(self, page: Any, out_dir: Path, idx: int) -> str:
        path = out_dir / f"step_{idx:02d}.png"
        try:
            await page.screenshot(path=str(path), full_page=True, timeout=15_000)
        except Exception:  # noqa: BLE001
            try:
                await page.screenshot(path=str(path), full_page=False, timeout=10_000)
            except Exception:  # noqa: BLE001
                path.write_bytes(b"")
        return str(path.resolve())

    async def _page_snapshot(self, page: Any) -> dict[str, Any]:
        """Return compact accessibility info for the current page."""
        try:
            snap = await page.evaluate(
                """
                () => {
                  function labelFor(el) {
                    if (el.id) {
                      const lbl = document.querySelector('label[for="' + el.id + '"]');
                      if (lbl) return lbl.innerText.trim();
                    }
                    const aria = el.getAttribute('aria-label') || '';
                    if (aria) return aria.trim();
                    const ph = el.getAttribute('placeholder') || '';
                    if (ph) return ph.trim();
                    return el.getAttribute('name') || el.getAttribute('id') || '';
                  }
                  function vis(el) {
                    const r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
                    if (!r || (r.width === 0 && r.height === 0)) return false;
                    const s = window.getComputedStyle(el);
                    return s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
                  }
                  const out = [];
                  const sel = 'input, textarea, select, button, [role=button], [role=combobox], [role=radio], [role=checkbox]';
                  for (const el of document.querySelectorAll(sel)) {
                    if (!vis(el)) continue;
                    if (el.type === 'hidden') continue;
                    out.push({
                      tag: el.tagName.toLowerCase(),
                      type: el.type || el.getAttribute('type') || '',
                      label: labelFor(el),
                      name: el.getAttribute('name') || '',
                      id: el.id || '',
                      required: el.required || el.getAttribute('aria-required') === 'true',
                      value_snippet: String(el.value || el.getAttribute('value') || '').slice(0,80),
                      text: (el.innerText || el.textContent || '').trim().slice(0,100),
                    });
                    if (out.length >= 80) break;
                  }
                  return {
                    url: location.href,
                    title: document.title,
                    elements: out,
                  };
                }
                """
            )
        except Exception:  # noqa: BLE001
            snap = {"url": page.url, "title": "", "elements": []}
        return snap

    # ──────────────────────────────────────────────────────────────────────
    # Claude vision agent loop
    # ──────────────────────────────────────────────────────────────────────

    async def _ask_claude_for_actions(
        self,
        page_snapshot: dict[str, Any],
        screenshot_path: str,
        profile_context: str,
        resume_path: str,
        cover_letter: str,
        job_description: str,
        fill_log: list[dict[str, Any]],
        submit: bool,
    ) -> dict[str, Any]:
        user_content: list[dict[str, Any]] = []

        # Attach screenshot
        try:
            img_b64 = base64.b64encode(Path(screenshot_path).read_bytes()).decode("ascii")
            user_content.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": img_b64},
                }
            )
        except Exception:  # noqa: BLE001
            pass

        recent = "\n".join(
            f"  step {e.get('step','?')}: {e.get('action','')} "
            f"'{e.get('target','')}' → {e.get('result','')}"
            for e in fill_log[-10:]
            if "action" in e
        )

        elements_json = json.dumps(page_snapshot.get("elements", []), indent=2)[:8000]

        text_block = f"""CURRENT PAGE URL : {page_snapshot.get('url','')}
PAGE TITLE       : {page_snapshot.get('title','')}

═══════════════════════════════════
CANDIDATE PROFILE
═══════════════════════════════════
{profile_context}

Resume PDF path (for upload fields): {resume_path}

Cover letter (paste verbatim into cover-letter textarea, or upload):
---
{cover_letter[:3000]}
---

Job description (for custom questions):
{job_description[:1500]}

═══════════════════════════════════
RECENT ACTIONS (last 10)
═══════════════════════════════════
{recent or '(none yet)'}

═══════════════════════════════════
VISIBLE INTERACTIVE ELEMENTS
═══════════════════════════════════
{elements_json}

═══════════════════════════════════
INSTRUCTIONS
═══════════════════════════════════
You are filling a job application form on behalf of the candidate.
Shadow mode: {not submit}  (if true → NEVER produce a click action for the final Submit/Apply button)

For EACH visible, unfilled required field produce an action.
For optional fields: fill them if you have the data; skip if not.
For EEO/demographic questions: use "Prefer not to say" unless the profile explicitly sets a value.
For "How did you hear about us?": answer "Company website".
For custom essay questions: write 2-4 specific, honest sentences using profile + job description.
If a "Next" / "Continue" / "Save and Continue" button is visible AND unfilled required fields remain on this page: fill them first, then click Next.
If the page shows a review/confirm screen with all fields visible: set done=true.
If the page shows a success/confirmation message: set done=true.

Return a single JSON object — no prose, no markdown fences:
{{
  "summary": "<one sentence what you are doing>",
  "done": false,
  "reason": null,
  "actions": [
    {{"kind": "fill",          "label": "<exact field label>",     "value": "<text>"}},
    {{"kind": "select",        "label": "<label>",                 "value": "<option text>"}},
    {{"kind": "check",         "label": "<label>",                 "value": true}},
    {{"kind": "upload",        "label": "<resume/CV label>",       "path": "{resume_path}"}},
    {{"kind": "click",         "label": "<Next | Continue | ...>"}},
    {{"kind": "answer_custom", "question": "<q text>",             "value": "<answer>"}},
    {{"kind": "scroll",        "direction": "down"}}
  ]
}}
"""
        user_content.append({"type": "text", "text": text_block})

        try:
            raw = await self.llm.chat(
                messages=[{"role": "user", "content": user_content}],
                system=self._system_prompt,
                max_tokens=2500,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("filler.llm_failed", extra={"error": str(exc)})
            return {"summary": "LLM call failed", "done": False, "actions": []}

        return _parse_plan(raw if isinstance(raw, str) else str(raw))

    # ──────────────────────────────────────────────────────────────────────
    # Action execution
    # ──────────────────────────────────────────────────────────────────────

    async def _execute_action(
        self,
        page: Any,
        action: dict[str, Any],
        resume_path: str,
        custom_qa: dict[str, str],
        context: Any,
    ) -> str:
        kind = (action.get("kind") or "").lower()
        label = action.get("label") or ""
        value = action.get("value")

        try:
            if kind == "fill":
                el = await self._find_field(page, label)
                if el is None:
                    return f"not_found: {label}"
                current_val = await el.evaluate("el => el.value || ''")
                if current_val and len(current_val) > 2:
                    return f"already_filled: {label}"
                await el.click()
                await el.fill(str(value) if value is not None else "")
                return "filled"

            if kind == "select":
                return await self._handle_select(page, label, str(value or ""))

            if kind == "check":
                el = await self._find_field(page, label, kinds=("input",))
                if el is None:
                    return f"not_found: {label}"
                if value:
                    await el.check()
                else:
                    await el.uncheck()
                return "checked" if value else "unchecked"

            if kind == "upload":
                path = str(action.get("path") or resume_path)
                # Prefer explicit file inputs
                el = await self._find_field(page, label, kinds=("input",), input_type="file")
                if el is None:
                    # Fallback: any visible file input
                    loc = page.locator("input[type='file']")
                    if await loc.count():
                        el = loc.first
                if el is None:
                    return f"upload_field_not_found: {label}"
                await el.set_input_files(path)
                await asyncio.sleep(1.5)  # let upload UI settle
                return f"uploaded: {Path(path).name}"

            if kind == "click":
                btn = await self._find_button(page, label)
                if btn is None:
                    return f"button_not_found: {label}"
                await btn.click()
                await asyncio.sleep(1.5)  # wait for page change after Next/Continue
                return f"clicked: {label}"

            if kind == "answer_custom":
                question = action.get("question", label)
                if question:
                    custom_qa[question] = str(value or "")
                el = await self._find_field(page, question, kinds=("textarea", "input"))
                if el is None:
                    el = await self._find_field(page, label, kinds=("textarea", "input"))
                if el is not None:
                    await el.click()
                    await el.fill(str(value or ""))
                    return "answered"
                return "answer_recorded_only"

            if kind == "scroll":
                direction = (action.get("direction") or "down").lower()
                await page.mouse.wheel(0, 600 if direction == "down" else -600)
                return "scrolled"

            return f"unknown_kind: {kind}"

        except Exception as exc:  # noqa: BLE001
            log.warning(
                "filler.action_error",
                extra={"kind": kind, "label": label, "error": str(exc)},
            )
            return f"error: {exc}"

    # ──────────────────────────────────────────────────────────────────────
    # Element resolution helpers
    # ──────────────────────────────────────────────────────────────────────

    async def _find_field(
        self,
        page: Any,
        label: str,
        *,
        kinds: tuple[str, ...] = ("input", "textarea", "select"),
        input_type: str | None = None,
    ) -> Any:
        label_clean = re.sub(r"[*:\s]+$", "", label).strip()
        if not label_clean:
            return None

        # 1. get_by_label (handles <label for=…>)
        try:
            loc = page.get_by_label(label_clean, exact=False)
            if await loc.count():
                el = loc.first
                if await el.is_visible():
                    return el
        except Exception:  # noqa: BLE001
            pass

        # 2. placeholder
        try:
            loc = page.get_by_placeholder(label_clean, exact=False)
            if await loc.count():
                el = loc.first
                if await el.is_visible():
                    return el
        except Exception:  # noqa: BLE001
            pass

        # 3. aria-label contains
        for kind in kinds:
            type_suffix = f"[type='{input_type}']" if input_type and kind == "input" else ""
            selectors = [
                f"{kind}[aria-label*='{label_clean}']{type_suffix}",
                f"{kind}[name*='{label_clean.lower().replace(' ', '_')}']{type_suffix}",
                f"{kind}[id*='{label_clean.lower().replace(' ', '-')}']{type_suffix}",
                f"{kind}[placeholder*='{label_clean}']{type_suffix}",
            ]
            for sel in selectors:
                try:
                    loc = page.locator(sel)
                    if await loc.count():
                        el = loc.first
                        if await el.is_visible():
                            return el
                except Exception:  # noqa: BLE001
                    continue

        # 4. Any visible file input (when input_type='file')
        if input_type == "file":
            try:
                loc = page.locator("input[type='file']")
                if await loc.count():
                    return loc.first
            except Exception:  # noqa: BLE001
                pass

        return None

    async def _handle_select(self, page: Any, label: str, value: str) -> str:
        """Handle both native <select> and custom dropdowns."""
        # Try native select first
        el = await self._find_field(page, label, kinds=("select",))
        if el is not None:
            try:
                await el.select_option(label=value)
                return f"selected: {value}"
            except Exception:  # noqa: BLE001
                try:
                    await el.select_option(value=value)
                    return f"selected_by_value: {value}"
                except Exception:  # noqa: BLE001
                    pass

        # Custom combobox / react-select style
        combo = await self._find_field(page, label, kinds=("input", "button"))
        if combo is not None:
            await combo.click()
            await asyncio.sleep(0.4)
            try:
                option = page.get_by_role("option", name=value, exact=False)
                if await option.count():
                    await option.first.click()
                    return f"selected_combobox: {value}"
            except Exception:  # noqa: BLE001
                pass
            # Type and press Enter
            try:
                await combo.fill(value)
                await asyncio.sleep(0.3)
                await page.keyboard.press("Enter")
                return f"selected_typed: {value}"
            except Exception:  # noqa: BLE001
                pass

        return f"select_not_found: {label}"

    async def _find_button(self, page: Any, label: str) -> Any:
        label_clean = label.strip()
        strategies = [
            lambda: page.get_by_role("button", name=label_clean, exact=False),
            lambda: page.get_by_role("link", name=label_clean, exact=False),
            lambda: page.locator(f"button:has-text('{label_clean}')"),
            lambda: page.locator(f"[type='submit']:has-text('{label_clean}')"),
            lambda: page.get_by_text(label_clean, exact=False),
        ]
        for fn in strategies:
            try:
                loc = fn()
                if await loc.count():
                    el = loc.first
                    if await el.is_visible():
                        return el
            except Exception:  # noqa: BLE001
                continue
        return None

    # ──────────────────────────────────────────────────────────────────────
    # Indeed email + OTP authentication
    # ──────────────────────────────────────────────────────────────────────

    async def _handle_indeed_login(
        self,
        page: Any,
        profile: Any,
        fill_log: list,
    ) -> bool:
        """
        Handle Indeed's "Create an account or sign in" page.

        Flow:
          1. Enter the candidate's email → click Continue
          2. If an OTP/verification-code input appears, fetch the code from
             the configured Outlook inbox and enter it → click Continue
          3. Return True if we successfully passed auth, False otherwise.
        """
        email = getattr(getattr(profile, "profile", profile), "email", None)
        if not email:
            log.warning("filler.indeed_login.no_email")
            return False

        log.info("filler.indeed_login.entering_email", extra={"email": email})

        # ── Step 1: Fill email field ──────────────────────────────────────
        try:
            email_input = page.get_by_label("Email address", exact=False)
            if not await email_input.count():
                email_input = page.locator("input[type='email'], input[name*='email'], input[id*='email']")
            if not await email_input.count():
                log.warning("filler.indeed_login.email_input_not_found")
                return False

            await email_input.first.fill(email)
            await asyncio.sleep(0.5)

            # Click Continue / Next / Sign in
            for btn_text in ("Continue", "Sign in", "Next", "Log in"):
                btn = await self._find_button(page, btn_text)
                if btn is not None and await btn.is_visible():
                    await btn.click()
                    fill_log.append({"step": "indeed_login", "action": f"clicked {btn_text}"})
                    break

            # Wait for Indeed to process the email and load the OTP page
            await asyncio.sleep(2.0)
            try:
                await page.wait_for_load_state("networkidle", timeout=8_000)
            except Exception:  # noqa: BLE001
                await asyncio.sleep(3.0)

        except Exception as exc:  # noqa: BLE001
            log.warning("filler.indeed_login.email_step_failed", extra={"error": str(exc)})
            return False

        # ── Step 2: Detect OTP / verification code field ─────────────────
        # Indeed's OTP page HTML uses several possible field labels/attributes.
        # Poll for up to 15 seconds — the OTP page loads after a network round-trip.
        otp_input = None
        for _attempt in range(6):  # 6 × 2.5s = 15s max
            otp_locators = [
                page.locator("input[autocomplete='one-time-code']"),
                page.get_by_label("Verification code", exact=False),
                page.get_by_label("Enter the code", exact=False),
                page.get_by_label("Enter code", exact=False),
                page.get_by_placeholder("Enter code"),
                page.get_by_placeholder("6-digit code"),
                page.locator("input[name='code']"),
                page.locator("input[id*='verification']"),
                page.locator("input[id*='code']"),
                page.locator("input[data-testid*='code']"),
                # Generic: any single-line input on a page that mentions "code"
            ]
            for loc in otp_locators:
                try:
                    if await loc.count() and await loc.first.is_visible():
                        otp_input = loc.first
                        break
                except Exception:  # noqa: BLE001
                    continue
            if otp_input is not None:
                break
            await asyncio.sleep(2.5)

        if otp_input is None:
            # Check if login succeeded without OTP (existing session)
            body = await page.evaluate(
                "document.body ? document.body.innerText.toLowerCase() : ''"
            )
            url_now = page.url.lower()
            auth_still_present = any(
                sig in url_now or sig in body
                for sig in ("sign in", "verify", "create an account", "indeed.com/account")
            )
            if not auth_still_present:
                log.info("filler.indeed_login.no_otp_needed")
                fill_log.append({"step": "indeed_login", "action": "no_otp_required"})
                return True
            log.warning(
                "filler.indeed_login.otp_input_not_found",
                extra={"url": page.url, "body_snippet": body[:200]},
            )
            return False

        log.info("filler.indeed_login.waiting_for_otp_email")
        fill_log.append({"step": "indeed_login", "action": "otp_field_detected_polling_email"})

        # ── Step 3: Fetch OTP from email inbox ────────────────────────────
        otp_code = await self._fetch_otp_from_email(
            sender_hint="indeed",
            subject_hint="verification",
            timeout_seconds=90,
        )

        if not otp_code:
            log.warning("filler.indeed_login.otp_not_found_in_email")
            fill_log.append({"step": "indeed_login", "action": "otp_not_found_in_email"})
            return False

        log.info("filler.indeed_login.otp_found", extra={"code": otp_code})
        fill_log.append({"step": "indeed_login", "action": "otp_found", "code": otp_code})

        # ── Step 4: Enter OTP ─────────────────────────────────────────────
        try:
            await otp_input.fill(otp_code)
            await asyncio.sleep(0.5)
            for btn_text in ("Continue", "Verify", "Sign in", "Submit", "Next"):
                btn = await self._find_button(page, btn_text)
                if btn is not None and await btn.is_visible():
                    await btn.click()
                    fill_log.append({"step": "indeed_login", "action": f"submitted_otp_{btn_text}"})
                    break
            await asyncio.sleep(3.0)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("filler.indeed_login.otp_entry_failed", extra={"error": str(exc)})
            return False

    async def _fetch_otp_from_email(
        self,
        sender_hint: str = "indeed",
        subject_hint: str = "verification",
        timeout_seconds: int = 90,
    ) -> str | None:
        """
        Poll the configured Outlook IMAP inbox for an OTP / verification-code email.

        Looks for a recent email whose sender/subject matches the hints, then
        extracts the first 4-8 digit number from the body.

        Returns the code string, or None if not found within timeout_seconds.
        """
        if not settings.email_configured:
            log.warning(
                "filler.otp_email.not_configured",
                extra={"hint": "Set OUTLOOK_EMAIL and OUTLOOK_APP_PASSWORD in .env"},
            )
            return None

        import re as _re

        loop = asyncio.get_event_loop()

        def _imap_search() -> str | None:
            """Blocking IMAP call — runs in a thread executor."""
            try:
                import imapclient  # type: ignore[import-untyped]
                import email as _email_lib
                import datetime

                def _decode(val: bytes | str) -> str:
                    if isinstance(val, bytes):
                        return val.decode("utf-8", errors="replace")
                    return val

                with imapclient.IMAPClient(
                    settings.imap_host, port=settings.imap_port, ssl=True
                ) as server:
                    server.login(settings.outlook_email, settings.outlook_app_password)
                    server.select_folder("INBOX")

                    # Search emails from today — IMAP SINCE only has day granularity
                    today_str = datetime.datetime.utcnow().strftime("%d-%b-%Y")

                    # Try unseen first (most likely), fall back to all today
                    for criteria in (
                        ["SINCE", today_str, "UNSEEN"],
                        ["SINCE", today_str],
                    ):
                        uids = server.search(criteria)
                        if uids:
                            break

                    if not uids:
                        return None

                    # Sort newest first, check up to 15 most recent emails
                    for uid in sorted(uids, reverse=True)[:15]:
                        try:
                            data = server.fetch([uid], ["RFC822"])
                            raw = data[uid].get(b"RFC822", b"")
                            if not raw:
                                continue
                            msg = _email_lib.message_from_bytes(raw)
                            sender = msg.get("From", "").lower()
                            subject = msg.get("Subject", "").lower()

                            # Must match sender OR subject hint
                            hint_lower = sender_hint.lower()
                            subj_hint = subject_hint.lower()
                            if hint_lower not in sender and hint_lower not in subject:
                                continue
                            if subj_hint and subj_hint not in subject and subj_hint not in sender:
                                # Also check if the body mentions it
                                pass  # will check body below

                            # Extract plain text body
                            body = ""
                            if msg.is_multipart():
                                for part in msg.walk():
                                    ct = part.get_content_type()
                                    if ct == "text/plain":
                                        body += _decode(part.get_payload(decode=True) or b"")
                                    elif ct == "text/html" and not body:
                                        # Fallback to HTML if no plain text
                                        import html as _html_mod
                                        raw_html = _decode(part.get_payload(decode=True) or b"")
                                        # Strip tags crudely
                                        body += _re.sub(r"<[^>]+>", " ", raw_html)
                            else:
                                body = _decode(msg.get_payload(decode=True) or b"")

                            # Look for standalone 4-8 digit OTP code
                            # Prefer codes that appear near keywords like "code", "verify"
                            code_context = _re.findall(
                                r"(?:code|verify|verification|otp)[^\d]{0,30}(\d{4,8})",
                                body.lower(),
                            )
                            if code_context:
                                return code_context[0]

                            # Fallback: any 4-8 digit number in the body
                            all_codes = _re.findall(r"\b(\d{4,8})\b", body)
                            if all_codes:
                                return all_codes[0]

                        except Exception:  # noqa: BLE001
                            continue

            except Exception as exc:  # noqa: BLE001
                log.warning("filler.otp_imap_error", extra={"error": str(exc)})
            return None

        # Poll for up to timeout_seconds
        deadline = time.monotonic() + timeout_seconds
        poll_interval = 5  # seconds between IMAP checks
        while time.monotonic() < deadline:
            code = await loop.run_in_executor(None, _imap_search)
            if code:
                return code
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(poll_interval, remaining))

        return None

    # ──────────────────────────────────────────────────────────────────────
    # Final submit (live mode)
    # ──────────────────────────────────────────────────────────────────────

    async def _click_submit(self, page: Any) -> tuple[bool, str | None]:
        for label in _SUBMIT_TEXTS:
            btn = await self._find_button(page, label)
            if btn is None:
                continue
            try:
                await btn.click()
                await asyncio.sleep(3.0)
                body = await page.evaluate(
                    "document.body ? document.body.innerText.toLowerCase() : ''"
                )
                success_phrases = (
                    "application submitted",
                    "thanks for applying",
                    "we received your application",
                    "successfully submitted",
                    "thank you for your interest",
                    "your application has been",
                    "application complete",
                )
                if any(p in body for p in success_phrases):
                    return True, None
                # Clicked but no confirmation text — still treat as submitted
                return True, "clicked_submit_no_confirmation"
            except Exception as exc:  # noqa: BLE001
                return False, str(exc)
        return False, "submit_button_not_found"

    # ──────────────────────────────────────────────────────────────────────
    # DEV_MODE stub
    # ──────────────────────────────────────────────────────────────────────

    async def _dev_mode_fill(
        self,
        app_id: str,
        apply_url: str,
        submit: bool,
        out_dir: Path,
    ) -> FillResult:
        paths: list[str] = []
        for i in range(3):
            p = out_dir / f"step_{i:02d}.png"
            p.write_bytes(_PLACEHOLDER_PNG)
            paths.append(str(p.resolve()))

        return FillResult(
            status="complete" if submit else "shadow_complete",
            screenshots=paths,
            fill_log=[
                {"step": "navigate", "url": apply_url},
                {"step": "navigate", "action": "clicked Apply for this job", "result": "same_page"},
                {"step": 0, "action": "fill", "target": "First name", "result": "filled"},
                {"step": 0, "action": "fill", "target": "Last name", "result": "filled"},
                {"step": 0, "action": "fill", "target": "Email", "result": "filled"},
                {"step": 0, "action": "fill", "target": "Phone", "result": "filled"},
                {"step": 0, "action": "upload", "target": "Resume", "result": "uploaded"},
                {"step": 1, "action": "click", "target": "Next", "result": "clicked"},
                {"step": "submit", "submitted": submit, "dev_mode": True},
            ],
            custom_qa={
                "Why are you interested in this role?": (
                    "This role aligns closely with my background in data analysis "
                    "and process improvement. I'm drawn to the team's focus on "
                    "measurable outcomes and would enjoy contributing my SQL and "
                    "Python skills to the mission."
                )
            },
            submitted=submit,
        )


# ──────────────────────────────────────────────────────────────────────────────
# JSON plan parsing
# ──────────────────────────────────────────────────────────────────────────────


def _parse_plan(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {"summary": "unparseable", "done": False, "actions": []}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"summary": "invalid_json", "done": False, "actions": []}
    data.setdefault("summary", "")
    data.setdefault("done", False)
    data.setdefault("actions", [])
    if not isinstance(data["actions"], list):
        data["actions"] = []
    return data


# Minimal 1×1 transparent PNG for DEV_MODE screenshot stubs
_PLACEHOLDER_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)
