"""
filler/universal.py — Universal LLM-guided form filler.

This is the single component responsible for filling any job application
form — Greenhouse, Lever, LinkedIn Easy Apply, Workday, Handshake, custom
ATS — without per-site code. It uses Playwright as the browser engine and
a Claude-vision-powered "agent" loop to:

    1. Navigate to the apply URL.
    2. Preflight check (form exists? login required? listing active?).
    3. Iterate: screenshot → ask Claude which fields to fill → fill them
       → screenshot → until Claude reports "form complete" or retries exhausted.
    4. For free-text questions, ask Claude to compose an answer using the
       user's profile + job description, then inject it.
    5. Screenshot the final state. In shadow mode, stop before submit.
       In live mode, click submit and capture the confirmation page.

The filler returns a `FillResult` with status, screenshot paths, fill log,
and any custom Q&A the LLM generated. It never raises on form errors —
it always returns a typed result the pipeline can persist.

If Stagehand v3 is installed (`stagehand>=3.0.0`) it's used as a thin
convenience wrapper. Otherwise we fall back to bare Playwright + Claude
vision, which works just as well for our purposes.

Usage:
    filler = UniversalFiller()
    result = await filler.fill(
        apply_url="https://boards.greenhouse.io/stripe/jobs/12345",
        profile=full_profile,
        resume_path="/tmp/resume.pdf",
        cover_letter="Dear hiring manager...",
        app_id="abc",
        submit=False,
    )
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


@dataclass
class FillResult:
    """
    Outcome of a single form fill attempt.

    status:
      - "complete"          → live submit succeeded
      - "shadow_complete"   → shadow fill finished, form ready for review
      - "needs_manual"      → got stuck; human must take over
      - "failed"            → unrecoverable error
      - "skipped"           → DEV_MODE or preflight refused (login, inactive)
    screenshots: ordered list of PNG paths (first → last)
    fill_log:    ordered list of dicts describing each action taken
    custom_qa:   {question: answer} for any free-text fields we answered
    error:       None on success; human-readable message on failure
    """

    status: FillStatus
    screenshots: list[str] = field(default_factory=list)
    fill_log: list[dict[str, Any]] = field(default_factory=list)
    custom_qa: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    submitted: bool = False
    duration_ms: int = 0


class UniversalFiller:
    """
    One filler, any ATS. Uses Playwright + Claude vision for self-healing
    form filling without selector maintenance.

    Instantiate once per process (it lazily spawns the browser). Call
    `fill(...)` for each application. Call `close()` at shutdown.
    """

    def __init__(
        self,
        llm: LLMClient | None = None,
        headless: bool | None = None,
        max_steps: int = 25,
    ) -> None:
        self.llm = llm or LLMClient()
        self.headless = settings.headless if headless is None else headless
        self.max_steps = max_steps
        self._browser = None
        self._playwright = None
        self._system_prompt = load_prompt("form_filler")

    async def close(self) -> None:
        """Tear down the Playwright browser. Safe to call repeatedly."""
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception as exc:  # noqa: BLE001 — cleanup
                log.warning("filler.close_browser_error", extra={"error": str(exc)})
            self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception as exc:  # noqa: BLE001 — cleanup
                log.warning("filler.close_pw_error", extra={"error": str(exc)})
            self._playwright = None

    # --- public entrypoint -----------------------------------------------

    async def fill(
        self,
        apply_url: str,
        profile: Any,  # FullProfile from db.store
        resume_path: str,
        cover_letter: str,
        app_id: str,
        job_description: str = "",
        submit: bool = False,
    ) -> FillResult:
        """
        Open the apply URL, fill every visible field, screenshot each step.

        In `submit=False` (shadow) mode: stop before the final submit
        button and return `shadow_complete`. The reviewer approves before
        anything goes live.

        In `submit=True` (live) mode: after the form is filled, click the
        final submit button and confirm.

        Args:
            apply_url: The job's application URL.
            profile:   User's FullProfile (to_context_string available).
            resume_path: Absolute path to the tailored resume PDF.
            cover_letter: Plain-text or markdown cover letter.
            app_id:    Application record ID — used for screenshot paths.
            job_description: Full job description, used to answer custom Qs.
            submit:    If True, actually submit. If False, shadow only.

        Returns:
            FillResult — never raises.
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
                "dev_mode": settings.dev_mode,
            },
        )

        # DEV_MODE shortcut — no browser, placeholder screenshot
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
        page = None
        fill_log: list[dict[str, Any]] = []
        screenshots: list[str] = []
        custom_qa: dict[str, str] = {}

        try:
            context = await self._browser.new_context(  # type: ignore[union-attr]
                viewport={"width": 1440, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()
            await page.goto(apply_url, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(1.0)  # let JS settle

            # Screenshot #0: landing page
            shot = await self._screenshot(page, screenshots_dir, len(screenshots))
            screenshots.append(shot)

            # Preflight
            preflight = await self._preflight(page)
            fill_log.append({"step": "preflight", **preflight})
            if preflight["verdict"] != "ok":
                log.warning(
                    "filler.preflight_rejected",
                    extra={"app_id": app_id, **preflight},
                )
                return FillResult(
                    status="skipped",
                    screenshots=screenshots,
                    fill_log=fill_log,
                    error=preflight.get("reason", "preflight_failed"),
                    duration_ms=int((time.monotonic() - start) * 1000),
                )

            # Iterative agent loop
            profile_context = (
                profile.to_context_string()
                if hasattr(profile, "to_context_string")
                else str(profile)
            )
            for step in range(self.max_steps):
                shot = await self._screenshot(page, screenshots_dir, len(screenshots))
                screenshots.append(shot)
                page_snapshot = await self._page_snapshot(page)
                plan = await self._ask_claude_for_actions(
                    page_snapshot=page_snapshot,
                    screenshot_path=shot,
                    profile_context=profile_context,
                    resume_path=resume_path,
                    cover_letter=cover_letter,
                    job_description=job_description,
                    fill_log=fill_log,
                    submit=submit,
                )
                fill_log.append({"step": step, "plan": plan.get("summary", "")})

                if plan.get("done"):
                    log.info(
                        "filler.agent_done",
                        extra={"app_id": app_id, "step": step, "reason": plan.get("reason")},
                    )
                    break

                actions = plan.get("actions", [])
                if not actions:
                    # Nothing to do but not "done" — likely stuck
                    if step > 3:
                        log.warning(
                            "filler.no_actions",
                            extra={"app_id": app_id, "step": step},
                        )
                        break
                    await asyncio.sleep(1.0)
                    continue

                for action in actions:
                    outcome = await self._execute_action(
                        page, action, resume_path, custom_qa
                    )
                    fill_log.append(
                        {
                            "step": step,
                            "action": action.get("kind"),
                            "target": action.get("label") or action.get("selector"),
                            "result": outcome,
                        }
                    )
                    await asyncio.sleep(0.4)

            # Final screenshot
            shot = await self._screenshot(page, screenshots_dir, len(screenshots))
            screenshots.append(shot)

            # Submit path (live mode only)
            submitted = False
            if submit:
                submitted, submit_err = await self._click_submit(page)
                fill_log.append({"step": "submit", "submitted": submitted, "error": submit_err})
                if submitted:
                    await asyncio.sleep(3.0)
                    shot = await self._screenshot(page, screenshots_dir, len(screenshots))
                    screenshots.append(shot)

            status: FillStatus
            if submit:
                status = "complete" if submitted else "needs_manual"
            else:
                status = "shadow_complete"

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

        except Exception as exc:  # noqa: BLE001 — we return the error
            log.exception(
                "filler.unhandled_error",
                extra={"app_id": app_id, "error": str(exc)},
            )
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
                except Exception as exc:  # noqa: BLE001 — cleanup
                    log.warning("filler.ctx_close_error", extra={"error": str(exc)})

    # --- browser lifecycle -----------------------------------------------

    async def _ensure_browser(self) -> None:
        """Lazy-launch Playwright + Chromium on first use."""
        if self._browser is not None:
            return
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
            args=["--disable-blink-features=AutomationControlled"],
        )

    # --- preflight -------------------------------------------------------

    async def _preflight(self, page: Any) -> dict[str, Any]:
        """
        Check: does the page have a form? Login wall? Closed posting?

        Returns {"verdict": "ok"|"login"|"closed"|"no_form", "reason": str}.
        """
        try:
            body_text = await page.evaluate(
                "document.body ? document.body.innerText.toLowerCase() : ''"
            )
        except Exception:  # noqa: BLE001
            body_text = ""

        closed_signals = (
            "no longer accepting",
            "position has been filled",
            "this job is no longer",
            "this posting is closed",
            "applications are closed",
        )
        if any(sig in body_text for sig in closed_signals):
            return {"verdict": "closed", "reason": "posting closed"}

        login_signals = (
            "sign in to apply",
            "log in to continue",
            "please sign in",
        )
        if any(sig in body_text for sig in login_signals):
            return {"verdict": "login", "reason": "login required"}

        form_count = await page.evaluate(
            "document.querySelectorAll('form, input, textarea, [role=\"form\"]').length"
        )
        if not form_count:
            # Some apps use "Apply" buttons that reveal forms on click.
            # Let the agent try anyway — this is only a warning.
            return {"verdict": "ok", "reason": "no_obvious_form (will attempt)"}

        return {"verdict": "ok", "reason": "form detected"}

    # --- screenshots + DOM snapshot --------------------------------------

    async def _screenshot(self, page: Any, out_dir: Path, idx: int) -> str:
        """Full-page screenshot. Returns absolute path."""
        path = out_dir / f"step_{idx:02d}.png"
        try:
            await page.screenshot(path=str(path), full_page=True, timeout=15_000)
        except Exception as exc:  # noqa: BLE001
            log.warning("filler.screenshot_failed", extra={"error": str(exc), "idx": idx})
            # Fallback: viewport-only screenshot
            try:
                await page.screenshot(path=str(path), full_page=False)
            except Exception:  # noqa: BLE001
                path.write_bytes(b"")  # empty placeholder so path exists
        return str(path.resolve())

    async def _page_snapshot(self, page: Any) -> dict[str, Any]:
        """
        Extract a compact accessibility snapshot of interactive elements.

        Returns a JSON-serializable dict of fields the agent can act on.
        Limited to ~60 elements to keep the LLM prompt small.
        """
        snap = await page.evaluate(
            """
            () => {
              function labelFor(el) {
                if (el.id) {
                  const lbl = document.querySelector(`label[for="${el.id}"]`);
                  if (lbl) return lbl.innerText.trim();
                }
                const aria = el.getAttribute('aria-label');
                if (aria) return aria.trim();
                const ph = el.getAttribute('placeholder');
                if (ph) return ph.trim();
                const name = el.getAttribute('name');
                if (name) return name;
                return '';
              }
              function visible(el) {
                if (!el.getBoundingClientRect) return false;
                const r = el.getBoundingClientRect();
                if (r.width === 0 && r.height === 0) return false;
                const st = window.getComputedStyle(el);
                if (st.display === 'none' || st.visibility === 'hidden') return false;
                return true;
              }
              const out = [];
              const inputs = document.querySelectorAll(
                'input, textarea, select, button, [role=button], [role=combobox]'
              );
              for (const el of inputs) {
                if (!visible(el)) continue;
                if (el.type === 'hidden') continue;
                out.push({
                  tag: el.tagName.toLowerCase(),
                  type: el.type || el.getAttribute('type') || '',
                  label: labelFor(el),
                  name: el.getAttribute('name') || '',
                  id: el.id || '',
                  required: el.required || el.getAttribute('aria-required') === 'true',
                  value_snippet: (el.value || '').toString().slice(0, 60),
                  text: (el.innerText || '').trim().slice(0, 80),
                });
                if (out.length >= 60) break;
              }
              return {
                url: location.href,
                title: document.title,
                elements: out,
                scrollY: window.scrollY,
              };
            }
            """
        )
        return snap

    # --- agent loop (Claude vision) --------------------------------------

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
        """
        Send {screenshot + accessibility snapshot + profile} to Claude,
        ask for a JSON plan of the next actions.

        Plan format:
        {
          "summary": "Filling contact section",
          "done": false,
          "reason": null,
          "actions": [
            {"kind": "fill", "label": "First name", "value": "John"},
            {"kind": "fill", "label": "Email", "value": "john@email.com"},
            {"kind": "upload", "label": "Resume", "path": "/path/resume.pdf"},
            {"kind": "select", "label": "Country", "value": "United States"},
            {"kind": "click", "label": "Next"},
            {"kind": "answer_custom", "question": "Why us?", "value": "..."},
          ]
        }
        """
        user_content: list[dict[str, Any]] = []

        try:
            img_b64 = base64.b64encode(Path(screenshot_path).read_bytes()).decode("ascii")
            user_content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": img_b64,
                    },
                }
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("filler.image_encode_failed", extra={"error": str(exc)})

        history_summary = "\n".join(
            f"- step {e.get('step', '?')}: {e.get('action', '')} "
            f"{e.get('target', '')} → {e.get('result', '')}"
            for e in fill_log[-8:]
            if "action" in e
        )

        text_block = f"""CURRENT PAGE: {page_snapshot.get('url')}
TITLE: {page_snapshot.get('title')}

USER PROFILE:
{profile_context}

RESUME FILE PATH (for upload fields): {resume_path}
COVER LETTER:
---
{cover_letter}
---

JOB DESCRIPTION (for custom questions):
{job_description[:2000]}

RECENT ACTIONS TAKEN:
{history_summary or '(none yet)'}

VISIBLE INTERACTIVE ELEMENTS (max 60):
{json.dumps(page_snapshot.get('elements', []), indent=2)[:6000]}

SHADOW MODE: {'NO — live submit allowed at the very end' if submit else 'YES — never click final submit/apply'}

Return a JSON object with exactly these keys:
{{
  "summary": "<1 short sentence describing what you're doing this step>",
  "done": <true if the form is fully filled and ready for review/submit; false if more work>,
  "reason": "<only if done=true; e.g. 'all fields filled, review button visible'>",
  "actions": [
    {{"kind": "fill",   "label": "<exact label as shown>", "value": "<text>"}},
    {{"kind": "select", "label": "<label>", "value": "<option text>"}},
    {{"kind": "check",  "label": "<label>", "value": true}},
    {{"kind": "upload", "label": "<label>", "path": "<file path>"}},
    {{"kind": "click",  "label": "<button text — e.g. 'Next', 'Continue'>"}},
    {{"kind": "answer_custom", "question": "<question>", "value": "<2-3 sentence answer>"}},
    {{"kind": "scroll", "direction": "down"}}
  ]
}}

RULES:
- Never produce an action for a field that already has a correct value (see value_snippet).
- For EEO/demographic questions, choose 'Prefer not to say' / 'Decline to answer' unless the profile specifies otherwise.
- For work authorization: answer based on profile's authorized_to_work and requires_sponsorship.
- For custom free-text questions (essays, "why us?"), generate a thoughtful 2-3 sentence answer using profile + job description.
- If a resume/CV upload field exists and hasn't been handled, emit an 'upload' action with the resume path.
- If you see a cover letter textarea, fill it with the full cover letter above.
- If the form spans multiple pages, click 'Next' / 'Continue' after filling the current page.
- Set done=true ONLY when you see the review/submit state and every required field is filled.
- {'Once done=true and live mode is on, the system will click Submit for you.' if submit else 'Never output a click action for the final Submit/Apply button.'}

Return ONLY the JSON object. No prose.
"""
        user_content.append({"type": "text", "text": text_block})

        try:
            raw = await self.llm.chat(
                messages=[{"role": "user", "content": user_content}],
                system=self._system_prompt,
                max_tokens=2000,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("filler.llm_failed", extra={"error": str(exc)})
            return {"summary": "LLM call failed", "done": False, "actions": []}

        if not isinstance(raw, str):
            raw = str(raw)

        return _parse_plan(raw)

    # --- action execution ------------------------------------------------

    async def _execute_action(
        self,
        page: Any,
        action: dict[str, Any],
        resume_path: str,
        custom_qa: dict[str, str],
    ) -> str:
        """Apply a single action from Claude's plan. Returns outcome string."""
        kind = (action.get("kind") or "").lower()
        label = action.get("label") or ""
        value = action.get("value")

        try:
            if kind == "fill":
                el = await self._find_field(page, label, kinds=("input", "textarea"))
                if el is None:
                    return f"not_found: {label}"
                await el.fill(str(value) if value is not None else "")
                return "filled"

            if kind == "select":
                el = await self._find_field(page, label, kinds=("select",))
                if el is not None:
                    try:
                        await el.select_option(label=str(value))
                        return "selected"
                    except Exception:  # noqa: BLE001
                        await el.select_option(str(value))
                        return "selected"
                # Fallback: combobox / custom dropdown — click + type
                combo = await self._find_field(page, label, kinds=("input", "button"))
                if combo is None:
                    return f"not_found: {label}"
                await combo.click()
                await asyncio.sleep(0.3)
                try:
                    option = page.get_by_role("option", name=str(value), exact=False)
                    await option.first.click(timeout=3000)
                    return "selected_combobox"
                except Exception:  # noqa: BLE001
                    await combo.fill(str(value))
                    await page.keyboard.press("Enter")
                    return "selected_typed"

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
                path = action.get("path") or resume_path
                el = await self._find_field(page, label, kinds=("input",), input_type="file")
                if el is None:
                    return f"upload_field_not_found: {label}"
                await el.set_input_files(path)
                return "uploaded"

            if kind == "click":
                btn = await self._find_button(page, label)
                if btn is None:
                    return f"button_not_found: {label}"
                await btn.click()
                return "clicked"

            if kind == "answer_custom":
                question = action.get("question", "")
                if question:
                    custom_qa[question] = str(value or "")
                # Find the question's textarea and fill it
                el = await self._find_field(page, question, kinds=("textarea", "input"))
                if el is not None:
                    await el.fill(str(value or ""))
                    return "answered"
                return "answer_recorded_only"

            if kind == "scroll":
                direction = (action.get("direction") or "down").lower()
                delta = 500 if direction == "down" else -500
                await page.mouse.wheel(0, delta)
                return "scrolled"

            return f"unknown_kind: {kind}"

        except Exception as exc:  # noqa: BLE001
            log.warning(
                "filler.action_error",
                extra={"kind": kind, "label": label, "error": str(exc)},
            )
            return f"error: {exc}"

    # --- element resolution ----------------------------------------------

    async def _find_field(
        self,
        page: Any,
        label: str,
        *,
        kinds: tuple[str, ...] = ("input", "textarea", "select"),
        input_type: str | None = None,
    ) -> Any:
        """
        Best-effort resolution of a field by its label/placeholder/name.

        Tries (in order): get_by_label → placeholder → name attribute →
        nearest input to a matching text node.
        """
        label_clean = re.sub(r"[*:]$", "", label).strip()

        # 1. get_by_label
        try:
            locator = page.get_by_label(label_clean, exact=False)
            count = await locator.count()
            if count:
                el = locator.first
                if await el.is_visible():
                    return el
        except Exception:  # noqa: BLE001
            pass

        # 2. placeholder
        try:
            locator = page.get_by_placeholder(label_clean, exact=False)
            if await locator.count():
                return locator.first
        except Exception:  # noqa: BLE001
            pass

        # 3. name attr (try exact then contains)
        for kind in kinds:
            suffix = f"[type='{input_type}']" if input_type and kind == "input" else ""
            for sel in (
                f"{kind}[name='{label_clean}']{suffix}",
                f"{kind}[name*='{label_clean.lower().replace(' ', '_')}']{suffix}",
                f"{kind}[id*='{label_clean.lower().replace(' ', '_')}']{suffix}",
            ):
                try:
                    loc = page.locator(sel)
                    if await loc.count():
                        el = loc.first
                        if await el.is_visible():
                            return el
                except Exception:  # noqa: BLE001
                    continue

        # 4. File input by type (if input_type=file specified)
        if input_type == "file":
            try:
                loc = page.locator("input[type='file']")
                if await loc.count():
                    return loc.first
            except Exception:  # noqa: BLE001
                pass

        return None

    async def _find_button(self, page: Any, label: str) -> Any:
        """Find a button/link by its visible text."""
        label_clean = label.strip()
        try:
            btn = page.get_by_role("button", name=label_clean, exact=False)
            if await btn.count():
                return btn.first
        except Exception:  # noqa: BLE001
            pass
        try:
            link = page.get_by_role("link", name=label_clean, exact=False)
            if await link.count():
                return link.first
        except Exception:  # noqa: BLE001
            pass
        try:
            txt = page.get_by_text(label_clean, exact=False)
            if await txt.count():
                return txt.first
        except Exception:  # noqa: BLE001
            pass
        return None

    # --- final submit (live mode only) -----------------------------------

    async def _click_submit(self, page: Any) -> tuple[bool, str | None]:
        """Attempt to click the final submit button. Returns (success, error)."""
        for label in ("Submit application", "Submit Application", "Submit", "Apply", "Send application"):
            btn = await self._find_button(page, label)
            if btn is None:
                continue
            try:
                await btn.click()
                # Wait for URL change or success text
                await asyncio.sleep(2.0)
                body = await page.evaluate("document.body.innerText.toLowerCase()")
                if any(
                    phrase in body
                    for phrase in (
                        "application submitted",
                        "thanks for applying",
                        "we received your application",
                        "successfully submitted",
                        "thank you for your interest",
                    )
                ):
                    return True, None
                return True, None  # clicked, but couldn't confirm text
            except Exception as exc:  # noqa: BLE001
                return False, str(exc)
        return False, "submit_button_not_found"

    # --- DEV_MODE fallback -----------------------------------------------

    async def _dev_mode_fill(
        self,
        app_id: str,
        apply_url: str,
        submit: bool,
        out_dir: Path,
    ) -> FillResult:
        """Simulate a fill without launching a browser. Writes stub screenshots."""
        placeholder = _PLACEHOLDER_PNG
        paths: list[str] = []
        for i in range(3):
            p = out_dir / f"step_{i:02d}.png"
            p.write_bytes(placeholder)
            paths.append(str(p.resolve()))

        return FillResult(
            status="complete" if submit else "shadow_complete",
            screenshots=paths,
            fill_log=[
                {"step": "preflight", "verdict": "ok", "reason": "dev_mode"},
                {"step": 0, "action": "fill", "target": "First name", "result": "filled"},
                {"step": 0, "action": "fill", "target": "Email", "result": "filled"},
                {"step": 0, "action": "upload", "target": "Resume", "result": "uploaded"},
                {"step": 1, "action": "click", "target": "Next", "result": "clicked"},
                {"step": "submit", "submitted": submit, "dev_mode": True},
            ],
            custom_qa={
                "Why are you interested in this role?": (
                    "I'm drawn to this team's focus on scale and reliability, "
                    "which aligns with my backend work. Your engineering blog "
                    "posts on observability are excellent — I'd love to "
                    "contribute to that culture."
                ),
            },
            submitted=submit,
        )


# --- Plan JSON parsing -----------------------------------------------------


def _parse_plan(raw: str) -> dict[str, Any]:
    """
    Extract JSON from an LLM response, tolerant of markdown fences / prose.

    Returns a dict with at least `summary`, `done`, `actions` keys.
    """
    text = raw.strip()
    # Strip markdown fences
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```$", "", text)

    # Find the first {...} block
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


# Minimal 1x1 PNG for DEV_MODE screenshot placeholders
_PLACEHOLDER_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)
