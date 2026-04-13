"""
Playwright driver wrapper for ATS automation.

This module keeps browser interactions behind a small interface so workflow
and MCP layers can call it without depending directly on Playwright details.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from browser.humanizer import Humanizer
from errors import ATSFormError

logger = logging.getLogger("job_finder.browser.playwright_driver")


class PlaywrightDriver:
    """Async wrapper around Playwright browser/page operations."""

    def __init__(
        self,
        headless: bool = True,
        slow_mo_ms: int = 0,
        timeout_ms: int = 15_000,
        humanizer: Humanizer | None = None,
    ) -> None:
        self.headless = headless
        self.slow_mo_ms = slow_mo_ms
        self.timeout_ms = timeout_ms
        self.humanizer = humanizer

        self._playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self._started = False

    async def start(self) -> None:
        """Start Playwright, browser, context, and page."""
        if self._started:
            return

        try:
            from playwright.async_api import async_playwright
        except Exception as exc:
            raise RuntimeError(
                "Playwright is not available. Install dependency and browser binaries."
            ) from exc

        self._playwright = await async_playwright().start()
        self.browser = await self._playwright.chromium.launch(
            headless=self.headless,
            slow_mo=self.slow_mo_ms,
        )
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        self.page.set_default_timeout(self.timeout_ms)
        self._started = True
        logger.info("Playwright driver started (headless=%s)", self.headless)

    async def stop(self) -> None:
        """Shutdown page/context/browser and Playwright runtime."""
        if self.page is not None:
            await self.page.close()
        if self.context is not None:
            await self.context.close()
        if self.browser is not None:
            await self.browser.close()
        if self._playwright is not None:
            await self._playwright.stop()

        self.page = None
        self.context = None
        self.browser = None
        self._playwright = None
        self._started = False
        logger.info("Playwright driver stopped")

    def _require_page(self) -> Any:
        if not self._started or self.page is None:
            raise RuntimeError("Playwright driver has not started.")
        return self.page

    @staticmethod
    def _build_select_variants(value_text: str) -> list[str]:
        """
        Build robust search variants for custom dropdown selection.
        """
        base = (value_text or "").strip()
        if not base:
            return [""]
        normalized = " ".join(base.lower().split())

        variants: list[str] = []
        seen: set[str] = set()

        def _add(term: str) -> None:
            key = " ".join((term or "").lower().split())
            if not key or key in seen:
                return
            seen.add(key)
            variants.append(term)

        if normalized in {"united states", "united states of america", "us", "usa"}:
            for term in ["US", "USA", "United States", "United States of America"]:
                _add(term)
        else:
            _add(base)
            if len(base) > 5:
                first_word = base.split()[0]
                if first_word.lower() not in {"united"}:
                    _add(first_word)
        return variants

    @staticmethod
    def _is_us_country_value(value_text: str) -> bool:
        normalized = " ".join((value_text or "").strip().lower().split())
        return normalized in {"united states", "united states of america", "us", "usa"}

    @staticmethod
    def _prepare_fill_value(selector: str, value_text: str) -> str:
        """
        Normalize high-variance text values before typing.

        Phone inputs are frequently masked in Greenhouse/Brex; typing canonical
        digits is more reliable than injecting formatted punctuation.
        """
        selector_norm = (selector or "").strip().lower()
        if not value_text:
            return value_text

        if not any(token in selector_norm for token in ("phone", "mobile", "tel")):
            return value_text

        digits = "".join(ch for ch in value_text if ch.isdigit())
        if not digits:
            return value_text

        # US-friendly normalization.
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        elif len(digits) > 10:
            digits = digits[-10:]
        return digits

    @staticmethod
    def _looks_like_phone_field(selector: str, value_text: str) -> bool:
        selector_norm = (selector or "").strip().lower()
        if any(token in selector_norm for token in ("phone", "mobile", "tel")):
            return True
        value_digits = "".join(ch for ch in (value_text or "") if ch.isdigit())
        return len(value_digits) >= 10

    @staticmethod
    def _phone_value_matches_expected(observed_value: str, expected_digits: str) -> bool:
        observed_digits = "".join(ch for ch in (observed_value or "") if ch.isdigit())
        if not observed_digits or not expected_digits:
            return False
        if observed_digits == expected_digits:
            return True
        if len(observed_digits) == 11 and observed_digits.startswith("1"):
            return observed_digits[1:] == expected_digits
        return False

    @staticmethod
    def detect_ats_from_url(url: str) -> str:
        """
        Detect ATS type from the live page URL.

        This is more reliable than trusting listing.ats_type, which is set
        from job board metadata and may be wrong or missing.
        """
        url_lower = (url or "").lower()
        if "greenhouse.io" in url_lower:
            return "greenhouse"
        if "lever.co" in url_lower:
            return "lever"
        if "ashbyhq.com" in url_lower or "jobs.ashby" in url_lower:
            return "ashby"
        if "myworkdayjobs.com" in url_lower or "workday.com" in url_lower:
            return "workday"
        if "icims.com" in url_lower:
            return "icims"
        if "smartrecruiters.com" in url_lower:
            return "smartrecruiters"
        if "jobvite.com" in url_lower:
            return "jobvite"
        if "taleo.net" in url_lower:
            return "taleo"
        return "unknown"

    @staticmethod
    def _is_application_form_page(url: str) -> bool:
        """Return True if the URL looks like the actual application form (not a job description)."""
        url_lower = (url or "").lower()
        form_indicators = [
            "/application",
            "/apply",
            "job_app",
            "/jobs/",
            "embed/job",
            "/careers/",
        ]
        # Ashby, Greenhouse embed, Lever job pages are always form pages
        if any(ats in url_lower for ats in ["greenhouse.io", "lever.co", "ashbyhq.com"]):
            return True
        return any(ind in url_lower for ind in form_indicators)

    async def goto(self, url: str, wait_until: str = "domcontentloaded") -> dict[str, Any]:
        page = self._require_page()
        if self.humanizer:
            await self.humanizer.pause_action()
        await page.goto(url, wait_until=wait_until)

        # --- Step 1: Follow embedded ATS iframes (Greenhouse/Lever embed pattern) ---
        try:
            locator = page.locator("iframe#grnhse_iframe, iframe[src*='boards.greenhouse.io'], iframe[src*='jobs.lever.co']").first
            await locator.wait_for(state="attached", timeout=3000)
            src = await locator.get_attribute("src")
            if src:
                logger.info("ATS embedded iframe detected. Navigating directly to iframe source: %s", src)
                if src.startswith("//"):
                    src = "https:" + src
                await page.goto(src, wait_until=wait_until)
        except Exception:
            # Expected if there's no iframe
            pass

        # --- Step 2: If still on a job description page, click the Apply button ---
        current_url = page.url
        if not self._is_application_form_page(current_url):
            logger.info("Landed on a job description page (%s). Looking for Apply button...", current_url)
            apply_selectors = [
                "a:has-text('Apply for this Job')",
                "a:has-text('Apply Now')",
                "a:has-text('Apply')",
                "button:has-text('Apply for this Job')",
                "button:has-text('Apply Now')",
                "button:has-text('Apply')",
            ]
            clicked = False
            for apply_sel in apply_selectors:
                try:
                    apply_btn = page.locator(apply_sel).first
                    is_visible = await apply_btn.is_visible(timeout=1500)
                    if is_visible:
                        logger.info("Found Apply button via '%s'. Clicking...", apply_sel)
                        async with page.expect_navigation(timeout=10000, wait_until=wait_until):
                            await apply_btn.click()
                        logger.info("Apply button clicked. New URL: %s", page.url)
                        clicked = True
                        break
                except Exception:
                    continue

            if not clicked:
                logger.info("No Apply button found or click navigation failed. Proceeding with current page.")

        # --- Step 3: Detect ATS from final live URL ---
        final_url = page.url
        detected_ats = self.detect_ats_from_url(final_url)
        logger.info("Final URL: %s — Detected ATS: %s", final_url, detected_ats)

        return {
            "url": final_url,
            "detected_ats": detected_ats,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


    async def get_dom_snapshot(self, form_selector: str = "form") -> dict[str, Any]:
        """Return a structured DOM snapshot with form HTML and field candidates."""
        page = self._require_page()
        title = await page.title()
        url = page.url

        form_html = await page.evaluate(
            """
            (selector) => {
              const form = document.querySelector(selector);
              return form ? form.outerHTML : "";
            }
            """,
            form_selector,
        )
        fields = await page.evaluate(
            """
            (selector) => {
              const form = document.querySelector(selector) || document;
              const nodes = Array.from(form.querySelectorAll("input, textarea, select"));
              return nodes.map((el, idx) => {
                const id = el.getAttribute("id");
                const name = el.getAttribute("name");
                let label = "";
                if (id) {
                  const linked = document.querySelector(`label[for="${id}"]`);
                  if (linked) label = (linked.textContent || "").trim();
                }
                if (!label) {
                  label = (
                    el.getAttribute("aria-label") ||
                    el.getAttribute("placeholder") ||
                    name ||
                    id ||
                    `field_${idx + 1}`
                  ).trim();
                }
                return {
                  index: idx + 1,
                  tag: el.tagName.toLowerCase(),
                  input_type: (el.getAttribute("type") || "").toLowerCase(),
                  id_attr: id || null,
                  name_attr: name || null,
                  label,
                  selector: id ? `#${id}` : name ? `[name="${name}"]` : `${el.tagName.toLowerCase()}:nth-of-type(${idx + 1})`,
                };
              });
            }
            """,
            form_selector,
        )

        return {
            "url": url,
            "title": title,
            "form_selector": form_selector,
            "form_html": form_html,
            "fields": fields,
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }

    async def fill_field(
        self,
        selector: str,
        value: Any,
        use_humanizer: bool = True,
    ) -> dict[str, Any]:
        """Fill text-like input fields. Auto-detects React Select widgets."""
        if not selector:
            raise ATSFormError("Cannot fill field without selector.")

        page = self._require_page()
        raw_value_text = "" if value is None else str(value)
        value_text = self._prepare_fill_value(selector=selector, value_text=raw_value_text)

        try:
            element = page.locator(selector).first
            # Quick check if this is a React Select custom widget (2s timeout)
            try:
                is_react_select = await element.evaluate(
                    """el => {
                        const cls = String(el.className || '').toLowerCase();
                        const id = String(el.id || '').toLowerCase();
                        const role = String(el.getAttribute('role') || '').toLowerCase();
                        const ariaAuto = String(el.getAttribute('aria-autocomplete') || '').toLowerCase();
                        return (
                          cls.includes('select__input') ||
                          id.includes('react-select') ||
                          role === 'combobox' ||
                          ariaAuto === 'list' ||
                          !!el.closest('[class*=select__container], [class*="-control"]')
                        );
                    }""",
                    timeout=2000,
                )
                if is_react_select:
                    # Delegate to select_option for React Select widgets
                    return await self.select_option(selector=selector, value=value)
            except Exception:
                pass  # Element may not exist yet, let fill() handle the error

            if use_humanizer and self.humanizer:
                await self.humanizer.pause_action()
                await self.humanizer.type_text(page=page, selector=selector, text=value_text)
            else:
                await page.fill(selector, value_text)

            if self._looks_like_phone_field(selector=selector, value_text=raw_value_text) and value_text:
                expected_digits = "".join(ch for ch in value_text if ch.isdigit())

                async def _read_observed_value() -> str:
                    try:
                        return await element.input_value()
                    except Exception:
                        try:
                            return await page.eval_on_selector(
                                selector,
                                "el => (el && 'value' in el) ? String(el.value || '') : ''",
                            )
                        except Exception:
                            return ""

                observed_value = await _read_observed_value()
                if not self._phone_value_matches_expected(observed_value, expected_digits):
                    # Retry once with deterministic typing to overcome mask edge-cases.
                    await page.fill(selector, "")
                    await page.type(selector, expected_digits, delay=35)
                    await page.wait_for_timeout(120)
                    observed_retry = await _read_observed_value()
                    if not self._phone_value_matches_expected(observed_retry, expected_digits):
                        raise ATSFormError(
                            "Phone verification failed after retry: "
                            f"expected digits '{expected_digits}', observed '{observed_retry}'."
                        )
        except Exception as exc:
            raise ATSFormError(f"Failed to fill '{selector}': {exc}") from exc

        return {"selector": selector, "value": value_text, "status": "filled"}

    async def select_option(
        self,
        selector: str,
        value: Any,
    ) -> dict[str, Any]:
        """Select an option from a dropdown (native <select> or custom React Select).

        For React Select, delegates to browser.react_select which properly
        clicks the rendered option elements to trigger React's onChange handler.
        """
        if not selector:
            raise ATSFormError("Cannot select option without selector.")

        from browser.react_select import (
            fill_react_select_from_input_with_variants,
            fill_react_select_with_variants,
        )

        page = self._require_page()
        value_text = "" if value is None else str(value)

        try:
            if self.humanizer:
                await self.humanizer.pause_action()

            element = page.locator(selector).first

            # Check if it's a native <select> element
            tag_name = await element.evaluate("el => el.tagName.toLowerCase()")

            if tag_name == "select":
                try:
                    await page.select_option(selector, label=value_text)
                except Exception:
                    await page.select_option(selector, value=value_text)
            else:
                # Custom React Select — find the container div for scoped interaction
                variants = self._build_select_variants(value_text)

                input_mode_result = await fill_react_select_from_input_with_variants(
                    page, selector, variants,
                )
                if input_mode_result["status"] == "filled":
                    return {"selector": selector, "value": value_text, "status": "selected"}

                container_selector = await page.evaluate("""(inputSelector) => {
                    const input = document.querySelector(inputSelector);
                    if (!input) return null;
                    // Walk up to find the react-select container
                    let el = input;
                    while (el && el.parentElement) {
                        el = el.parentElement;
                        const cls = el.className || '';
                        if (cls.includes('select__container') ||
                            cls.includes('select') && cls.includes('container')) {
                            // Return a unique selector for this container
                            if (el.id) return '#' + el.id;
                            // Find the parent field wrapper
                            const field = el.closest('.field, .select, [class*=question]');
                            if (field && field.id) return '#' + field.id;
                        }
                    }
                    return null;
                }""", selector)

                if not container_selector:
                    # Fallback: use the input's parent as container scope
                    container_selector = await page.evaluate("""(inputSelector) => {
                        const input = document.querySelector(inputSelector);
                        if (!input) return null;
                        // Go up to the closest field wrapper
                        const field = input.closest('.field, .select, [class*=question], [class*=application-field]');
                        if (field && field.id) return '#' + field.id;
                        return null;
                    }""", selector)

                if container_selector:
                    result = await fill_react_select_with_variants(
                        page, container_selector, variants,
                    )
                    if result["status"] != "filled":
                        raise ATSFormError(
                            f"React Select failed: {result.get('error', 'unknown')} "
                            f"(available: {result.get('available_options', [])})"
                        )
                else:
                    # Last resort: direct keyboard interaction.
                    if self._is_us_country_value(value_text):
                        raise ATSFormError(
                            "Could not find a reliable dropdown container for US country "
                            "selection; refusing keyboard fallback to avoid wrong-country picks."
                        )
                    logger.warning(
                        "Could not find React Select container for %s - using keyboard fallback",
                        selector,
                    )
                    await element.click()
                    await page.wait_for_timeout(180)
                    await page.keyboard.type(value_text, delay=40)
                    await page.wait_for_timeout(250)
                    await page.keyboard.press("ArrowDown")
                    await page.wait_for_timeout(75)
                    await page.keyboard.press("Enter")
                    await page.wait_for_timeout(180)

        except ATSFormError:
            raise
        except Exception as exc:
            raise ATSFormError(f"Failed to select option '{value_text}' in '{selector}': {exc}") from exc

        return {"selector": selector, "value": value_text, "status": "selected"}

    async def upload_file(self, selector: str, file_path: str) -> dict[str, Any]:
        """Upload a file to an <input type=file> field."""
        if not selector:
            raise ATSFormError("Cannot upload file without selector.")

        resolved = str(Path(file_path).resolve())
        page = self._require_page()
        selector_norm = selector.strip().lower()
        file_path_norm = resolved.lower()

        # Build fallback selectors for common ATS upload fields.
        selector_candidates: list[str] = [selector]
        if "resume" in selector_norm or "resume" in file_path_norm or "cv" in file_path_norm:
            selector_candidates.extend(
                [
                    "input#resume[type='file']",
                    "input#resume",
                    "input[type='file'][name='resume']",
                    "input[type='file'][name*='resume']",
                    "input[type='file'][id*='resume']",
                    "input[type='file'][aria-label*='resume' i]",
                    "input[type='file'][id*='cv']",
                    "input[type='file'][name*='cv']",
                ]
            )
            upload_kind = "resume"
        elif "cover" in selector_norm or "cover" in file_path_norm:
            selector_candidates.extend(
                [
                    "input#cover_letter[type='file']",
                    "input#cover_letter",
                    "input[type='file'][name='cover_letter']",
                    "input[type='file'][name*='cover']",
                    "input[type='file'][id*='cover']",
                    "input[type='file'][aria-label*='cover' i]",
                ]
            )
            upload_kind = "cover_letter"
        else:
            upload_kind = ""

        # Deduplicate while preserving order.
        deduped_candidates: list[str] = []
        seen: set[str] = set()
        for candidate in selector_candidates:
            key = candidate.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped_candidates.append(candidate)

        if self.humanizer:
            await self.humanizer.pause_action()

        expected_name = Path(resolved).name.lower()

        async def _selector_has_expected_file(selector_expr: str) -> bool:
            if not hasattr(page, "eval_on_selector_all"):
                # Lightweight test doubles may not expose full page APIs.
                return True
            try:
                return bool(
                    await page.eval_on_selector_all(
                        selector_expr,
                        """
                        (els, expected) => els.some((el) => {
                          if (!el || !el.files || !el.files.length) return false;
                          return Array.from(el.files).some((f) =>
                            String(f && f.name ? f.name : "").toLowerCase() === String(expected || "").toLowerCase()
                          );
                        })
                        """,
                        expected_name,
                    )
                )
            except Exception:
                return False

        errors: list[str] = []
        for candidate in deduped_candidates:
            try:
                await page.set_input_files(candidate, resolved)
                if await _selector_has_expected_file(candidate):
                    return {"selector": candidate, "file_path": resolved, "status": "uploaded"}
                errors.append(f"{candidate}: set_input_files completed but attachment verification failed")
            except Exception as exc:
                errors.append(f"{candidate}: {exc}")

        # Heuristic fallback for custom attach widgets where file input selectors differ.
        try:
            best_index = await page.evaluate(
                """(kind) => {
                    const inputs = Array.from(document.querySelectorAll("input[type='file']"));
                    if (!inputs.length) return -1;

                    const tokenMap = {
                        resume: ["resume", "cv"],
                        cover_letter: ["cover", "letter"],
                    };
                    const tokens = tokenMap[String(kind || "").toLowerCase()] || [];

                    let bestIndex = -1;
                    let bestScore = -1;
                    for (let i = 0; i < inputs.length; i += 1) {
                        const input = inputs[i];
                        const wrapper = input.closest(".field, [class*='application-field'], [class*='question']");
                        const label = wrapper?.querySelector("label")?.textContent || "";
                        const haystack = [
                            input.id || "",
                            input.name || "",
                            input.getAttribute("aria-label") || "",
                            input.className || "",
                            input.getAttribute("accept") || "",
                            label,
                        ].join(" ").toLowerCase();

                        let score = 0;
                        for (const token of tokens) {
                            if (haystack.includes(token)) score += 10;
                        }

                        if (!tokens.length && haystack.trim()) score = 1;
                        if (score > bestScore) {
                            bestScore = score;
                            bestIndex = i;
                        }
                    }

                    if (tokens.length && bestScore <= 0) return -1;
                    return bestIndex;
                }""",
                upload_kind,
            )
            if isinstance(best_index, int) and best_index >= 0:
                locator = page.locator("input[type='file']").nth(best_index)
                await locator.set_input_files(resolved)
                try:
                    has_expected = await locator.evaluate(
                        """
                        (el, expected) => {
                          if (!el || !el.files || !el.files.length) return false;
                          return Array.from(el.files).some((f) =>
                            String(f && f.name ? f.name : "").toLowerCase() === String(expected || "").toLowerCase()
                          );
                        }
                        """,
                        expected_name,
                    )
                except Exception:
                    has_expected = False
                if not has_expected:
                    raise ATSFormError("Heuristic file input selected but attachment verification failed.")
                return {
                    "selector": f"input[type='file']::nth({best_index})",
                    "file_path": resolved,
                    "status": "uploaded",
                }

            # FileChooser Fallback: For modern ATS (like Ashby) that omit <input type="file"> 
            # and rely exclusively on a generic button triggering a system dialog.
            try:
                # Find matching text for the button/container to click
                click_selectors = []
                if upload_kind == "resume":
                    click_selectors = ["text=Resume", "text=CV", "text=Attach", "text=Upload"]
                elif upload_kind == "cover_letter":
                    click_selectors = ["text=Cover", "text=Letter", "text=Attach", "text=Upload"]
                else:
                    click_selectors = ["text=Upload", "text=Attach"]

                file_chooser_caught = False
                for click_selector in click_selectors:
                    try:
                        # Attempt click, expect file_chooser to trigger. 
                        # Timeout fast since we're guessing buttons
                        async with page.expect_file_chooser(timeout=2000) as fc_info:
                            # We grab the first visible element matching the text
                            # and try to click it (often the wrapper or the label itself)
                            locator = page.locator(click_selector).first
                            await locator.click(timeout=1500)
                            
                        file_chooser = await fc_info.value
                        await file_chooser.set_files(resolved)
                        file_chooser_caught = True
                        break
                    except Exception as e:
                        errors.append(f"file_chooser fallback ({click_selector}): {e}")

                if file_chooser_caught:
                    return {
                        "selector": "file_chooser_fallback",
                        "file_path": resolved,
                        "status": "uploaded",
                    }
                    
            except Exception as e:
                 errors.append(f"file_chooser fallback overall failure: {e}")

        except Exception as exc:
            errors.append(f"heuristic_file_input_fallback: {exc}")

        detail = errors[-1] if errors else "unknown selector/file input error"
        raise ATSFormError(f"Failed to upload file via '{selector}' and all fallbacks failed: {detail}")

    async def click(self, selector: str) -> dict[str, Any]:
        """Click an element by selector."""
        if not selector:
            raise ATSFormError("Cannot click without selector.")

        page = self._require_page()
        try:
            if self.humanizer:
                await self.humanizer.pause_action()
            await page.click(selector)
        except Exception as exc:
            raise ATSFormError(f"Failed to click '{selector}': {exc}") from exc
        return {"selector": selector, "status": "clicked"}

    async def screenshot(self, path: str, full_page: bool = True) -> dict[str, Any]:
        """Capture a screenshot to disk."""
        page = self._require_page()
        resolved = str(Path(path).resolve())
        try:
            await page.screenshot(path=resolved, full_page=full_page)
        except Exception as exc:
            raise ATSFormError(f"Failed to take screenshot '{resolved}': {exc}") from exc
        return {"path": resolved, "status": "saved"}

    async def selector_exists(self, selector: str, timeout_ms: int = 1_200) -> bool:
        """Check whether a selector is present/attachable on the current page."""
        if not selector:
            return False
        page = self._require_page()
        try:
            await page.locator(selector).first.wait_for(state="attached", timeout=timeout_ms)
            return True
        except Exception:
            return False

    async def get_page_metadata(self, max_text_chars: int = 2_000) -> dict[str, Any]:
        """Return lightweight page metadata for diagnostics."""
        page = self._require_page()
        title = await page.title()
        url = page.url
        text_excerpt = await page.evaluate(
            """
            (maxChars) => {
              const text = (document?.body?.innerText || "").trim();
              return text.slice(0, Math.max(200, Number(maxChars || 2000)));
            }
            """,
            max_text_chars,
        )
        return {
            "url": url,
            "title": title,
            "text_excerpt": text_excerpt,
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }
