"""
Greenhouse-specific fill strategy.

Consumes Form Interpreter fill plans and executes them through PlaywrightDriver.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from browser.playwright_driver import PlaywrightDriver

logger = logging.getLogger("job_finder.browser.ats_strategies.greenhouse")

class GreenhouseStrategy:
    """Execution strategy for Greenhouse ATS forms."""

    ATS_TYPE = "greenhouse"

    @classmethod
    def supports(cls, ats_type: str | None) -> bool:
        return (ats_type or "").strip().lower() == cls.ATS_TYPE

    @staticmethod
    def _resolve_upload_path(
        field: dict[str, Any],
        artifact_paths: dict[str, str] | None = None,
    ) -> str:
        artifact_paths = artifact_paths or {}
        field_id = str(field.get("field_id") or "").lower()
        label = str(field.get("label") or "").strip().lower()

        if field_id and field_id in artifact_paths:
            return artifact_paths[field_id]

        if "resume" in label or "resume" in field_id or "cv" in label or "cv" in field_id:
            if "resume" in artifact_paths:
                return artifact_paths["resume"]
            elif "resume_upload" in artifact_paths:
                return artifact_paths["resume_upload"]
                
        if "cover" in label or "cover" in field_id or "letter" in label:
            if "cover_letter" in artifact_paths:
                return artifact_paths["cover_letter"]
            elif "cover_letter_upload" in artifact_paths:
                return artifact_paths["cover_letter_upload"]

        value = field.get("value")
        if isinstance(value, str) and value.strip():
            # Only use the LLM provided path IF it actually exists locally,
            # otherwise it might be hallucinated by the form interpreter.
            path_obj = Path(value.strip())
            if path_obj.exists() and path_obj.is_file():
                return value.strip()

        return ""

    def plan_actions(
        self,
        fill_plan: dict[str, Any],
        artifact_paths: dict[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """
        Convert fill plan into executable actions.

        Returns:
            actions, plan_failures
        """
        fields = list(fill_plan.get("fields", []))
        actions: list[dict[str, Any]] = []
        plan_failures: list[dict[str, Any]] = []

        text_actions: list[dict[str, Any]] = []
        upload_actions: list[dict[str, Any]] = []

        for field in fields:
            if not isinstance(field, dict):
                continue
            field_id = str(field.get("field_id") or "")
            selector = field.get("selector")
            field_type = str(field.get("type") or "").strip().lower()

            if not selector:
                # Try to recover file upload selectors using standard Greenhouse IDs
                if field_type == "file_upload":
                    field_id_lower = field_id.strip().lower()
                    label_lower = str(field.get("label") or "").strip().lower()
                    if "resume" in field_id_lower or "resume" in label_lower or "cv" in field_id_lower:
                        selector = "#resume"
                        logger.info("Recovered file upload selector for '%s': %s", field_id, selector)
                    elif "cover" in field_id_lower or "cover" in label_lower:
                        selector = "#cover_letter"
                        logger.info("Recovered file upload selector for '%s': %s", field_id, selector)
                    field["selector"] = selector

                if not selector:
                    logger.warning(
                        "Field '%s' (type=%s) skipped: no selector.",
                        field_id, field_type,
                    )
                    plan_failures.append(
                        {
                            "field_id": field_id,
                            "error_type": "missing_selector",
                            "error_message": "No selector available for field.",
                        }
                    )
                    continue

            if field_type == "file_upload":
                file_path = self._resolve_upload_path(field, artifact_paths=artifact_paths)
                logger.info(
                    "File upload field '%s': resolved path='%s' (artifact_paths keys=%s)",
                    field_id,
                    file_path or "<EMPTY>",
                    list((artifact_paths or {}).keys()),
                )
                if not file_path:
                    plan_failures.append(
                        {
                            "field_id": field_id,
                            "error_type": "missing_file_path",
                            "error_message": "No upload file path resolved for file_upload field.",
                        }
                    )
                    continue
                field_id_lower = field_id.strip().lower()
                label_lower = str(field.get("label") or "").strip().lower()
                inferred_cover_upload = "cover" in field_id_lower or "cover" in label_lower
                required_raw = field.get("required")
                required_value = (
                    bool(required_raw)
                    if required_raw is not None
                    else (not inferred_cover_upload)
                )
                upload_actions.append(
                    {
                        "field_id": field_id,
                        "action": "upload_file",
                        "selector": str(selector),
                        "value": file_path,
                        "confidence": field.get("confidence"),
                        "source": field.get("source"),
                        "required": required_value,
                    }
                )
                continue

            if field_type in {"checkbox", "radio"}:
                text_actions.append(
                    {
                        "field_id": field_id,
                        "label": field.get("label"),
                        "action": "click",
                        "selector": str(selector),
                        "value": field.get("value"),
                        "confidence": field.get("confidence"),
                        "source": field.get("source"),
                        "required": field.get("required", True),
                    }
                )
                continue

            if field_type == "select":
                text_actions.append(
                    {
                        "field_id": field_id,
                        "label": field.get("label"),
                        "action": "select_option",
                        "selector": str(selector),
                        "value": "" if field.get("value") is None else str(field.get("value")),
                        "confidence": field.get("confidence"),
                        "source": field.get("source"),
                        "required": field.get("required", True),
                    }
                )
                continue

            text_actions.append(
                {
                    "field_id": field_id,
                    "label": field.get("label"),
                    "action": "fill_text",
                    "selector": str(selector),
                    "value": "" if field.get("value") is None else str(field.get("value")),
                    "confidence": field.get("confidence"),
                    "source": field.get("source"),
                    "required": field.get("required", True),
                }
            )

        # Preserve original DOM order from fill plan, but ensure country/location
        # run first (phone widgets depend on country being set).
        priority_actions: list[dict[str, Any]] = []
        remaining_actions: list[dict[str, Any]] = []
        for a in text_actions:
            fid = str(a.get("field_id") or "").strip().lower()
            label = str(a.get("label") or "").strip().lower()
            if fid == "country" or "country" in label:
                priority_actions.insert(0, a)
            elif fid in {"candidate_location", "location"} or "location" in fid:
                priority_actions.append(a)
            else:
                remaining_actions.append(a)
        text_actions = priority_actions + remaining_actions
        actions.extend(text_actions)
        actions.extend(upload_actions)
        return actions, plan_failures

    @staticmethod
    def _action_priority(action: dict[str, Any]) -> tuple[int, str]:
        """
        Ensure dependent fields run in reliable order.

        Country selection should happen before phone so masked phone widgets are
        initialized correctly for US numbers.
        """
        field_id = str(action.get("field_id") or "").strip().lower()
        label = str(action.get("label") or "").strip().lower()
        action_type = str(action.get("action") or "").strip().lower()

        if action_type == "select_option" and (field_id == "country" or "country" in label):
            return (0, field_id)
        if action_type == "select_option" and (
            field_id in {"candidate_location", "location"} or "location" in field_id
        ):
            return (1, field_id)
        if "phone" in field_id or "phone" in label:
            return (2, field_id)
        return (10, field_id)

    @staticmethod
    def _classify_page_mismatch(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
        payload = metadata or {}
        title = str(payload.get("title") or "")
        url = str(payload.get("url") or "")
        text = str(payload.get("text_excerpt") or "")
        haystack = f"{title}\n{text}".lower()

        inactive_markers = (
            "no longer active",
            "no longer available",
            "has been filled",
            "position has been filled",
            "job is no longer accepting applications",
        )
        if any(marker in haystack for marker in inactive_markers):
            return {
                "field_id": "__listing__",
                "error_type": "listing_inactive",
                "error_message": (
                    "Listing appears inactive or unavailable on the ATS page. "
                    "Verify the posting is still live and the apply URL is current."
                ),
                "detected_url": url,
                "page_title": title,
            }
        return None

    @staticmethod
    async def _preflight_selectors(
        driver: PlaywrightDriver,
        actions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Fail fast when none of the planned selectors are present on page.

        This avoids expensive per-field timeouts when the browser lands on an
        unexpected page (login wall, expired URL, wrong template, etc.).
        """
        if not hasattr(driver, "selector_exists"):
            return []

        selectors_checked: list[str] = []
        for action in actions:
            selector = str(action.get("selector") or "").strip()
            if not selector or selector in selectors_checked:
                continue
            selectors_checked.append(selector)
            if len(selectors_checked) >= 5:
                break

        if not selectors_checked:
            return []

        found_any = False
        for selector in selectors_checked:
            try:
                exists = await driver.selector_exists(selector=selector, timeout_ms=1_200)
            except TypeError:
                exists = await driver.selector_exists(selector)
            if exists:
                found_any = True
                break

        if found_any:
            return []

        page_metadata: dict[str, Any] | None = None
        if hasattr(driver, "get_page_metadata"):
            try:
                page_metadata = await driver.get_page_metadata(max_text_chars=2_000)
            except TypeError:
                page_metadata = await driver.get_page_metadata()
            except Exception:
                page_metadata = None

        classified = GreenhouseStrategy._classify_page_mismatch(page_metadata)
        if classified:
            classified["selectors_checked"] = selectors_checked
            return [classified]

        return [
            {
                "field_id": "__page__",
                "error_type": "form_not_detected",
                "error_message": (
                    "None of the planned selectors were found on the loaded page. "
                    "Check apply URL, login/account gate, or ATS template mismatch."
                ),
                "selectors_checked": selectors_checked,
            }
        ]

    @staticmethod
    def _detect_submit_outcome_from_metadata(
        page_metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload = page_metadata or {}
        title = str(payload.get("title") or "")
        text_excerpt = str(payload.get("text_excerpt") or "")
        url = str(payload.get("url") or "")
        haystack = f"{title}\n{text_excerpt}".lower()
        url_lc = url.lower()

        success_url_markers = (
            "/job_app/confirmation",
            "/embed/job_app/confirmation",
            "confirmation?for=",
            "application/confirmation",
            "/thanks",
        )
        if any(marker in url_lc for marker in success_url_markers):
            return {
                "confirmed": True,
                "signal": "success_url",
                "metadata": page_metadata,
            }

        success_markers = (
            "thank you for applying",
            "thanks for applying",
            "application submitted",
            "we have received your application",
            "we've received your application",
            "your application has been submitted",
            "your application has been received",
            "application received",
            "track your status",
        )
        if any(marker in haystack for marker in success_markers):
            return {"confirmed": True, "signal": "success_text", "metadata": page_metadata}

        validation_markers = (
            "this field is required",
            "please complete this required field",
            "please complete all required fields",
            "there was a problem with your submission",
            "please review the errors below",
            "fix the errors",
        )
        if any(marker in haystack for marker in validation_markers):
            return {
                "confirmed": False,
                "error_type": "SubmissionValidationError",
                "error_message": (
                    "Form validation errors were detected after submit click."
                ),
                "metadata": page_metadata,
            }

        context_bits = []
        if url:
            context_bits.append(f"url={url}")
        if title:
            context_bits.append(f"title={title}")
        context_suffix = f" ({', '.join(context_bits)})" if context_bits else ""
        return {
            "confirmed": False,
            "error_type": "SubmissionUnconfirmed",
            "error_message": (
                "Submit click completed, but no success signal was detected on page."
                f"{context_suffix}"
            ),
            "metadata": page_metadata,
        }

    @staticmethod
    async def _assess_post_submit_state(
        driver: PlaywrightDriver,
    ) -> dict[str, Any]:
        """
        Verify that clicking submit actually resulted in a successful transition.
        """
        page_metadata: dict[str, Any] | None = None
        if hasattr(driver, "get_page_metadata"):
            try:
                page_metadata = await driver.get_page_metadata(max_text_chars=4000)
            except TypeError:
                page_metadata = await driver.get_page_metadata()
            except Exception:
                page_metadata = None
        return GreenhouseStrategy._detect_submit_outcome_from_metadata(page_metadata)

    @staticmethod
    async def _wait_for_post_submit_outcome(
        driver: PlaywrightDriver,
        timeout_seconds: float = 20.0,
        min_observation_seconds: float = 4.0,
    ) -> dict[str, Any]:
        """
        Poll post-submit page signals for a short period before closing browser.
        """
        page = driver._require_page()
        now_ts = datetime.now(timezone.utc).timestamp()
        deadline = now_ts + max(timeout_seconds, 1.0)
        min_observe_deadline = now_ts + max(min_observation_seconds, 0.0)
        while datetime.now(timezone.utc).timestamp() < deadline:
            outcome = await GreenhouseStrategy._assess_post_submit_state(driver)
            if outcome.get("confirmed") and datetime.now(timezone.utc).timestamp() >= min_observe_deadline:
                return outcome
            if outcome.get("error_type") == "SubmissionValidationError":
                return outcome
            await page.wait_for_timeout(750)
        return await GreenhouseStrategy._assess_post_submit_state(driver)

    @staticmethod
    def _submit_selector_candidates(primary_selector: str | None = None) -> list[str]:
        candidates = [
            primary_selector or "",
            "button[type='submit']",
            "input[type='submit']",
            "button[id*='submit' i]",
            "button[name*='submit' i]",
            "button:has-text('Submit application')",
            "button:has-text('Submit Application')",
            "button:has-text('Submit')",
            "button:has-text('Apply')",
            "input[value*='submit' i]",
            "input[value*='apply' i]",
        ]
        deduped: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(str(candidate))
        return deduped

    @staticmethod
    async def _click_submit_with_fallback(
        driver: PlaywrightDriver,
        primary_selector: str,
    ) -> str:
        page = driver._require_page()
        errors: list[str] = []
        for selector in GreenhouseStrategy._submit_selector_candidates(primary_selector):
            locator = page.locator(selector).first
            try:
                if await locator.count() == 0:
                    continue
                try:
                    await locator.scroll_into_view_if_needed(timeout=2_000)
                except Exception:
                    pass
                await locator.click(timeout=6_000)
                logger.info("Submit click succeeded via selector: %s", selector)
                return selector
            except Exception as exc:
                errors.append(f"{selector}: {exc}")

        # Final fallback: click first visible button/input whose text/value includes submit/apply.
        try:
            clicked = await page.evaluate("""() => {
                const norm = (v) => String(v || "").toLowerCase().trim();
                const controls = Array.from(document.querySelectorAll("button, input[type='submit'], input[type='button']"));
                for (const el of controls) {
                    const text = norm(el.textContent || el.value || el.getAttribute("aria-label"));
                    if (!text) continue;
                    if (!(text.includes("submit") || text.includes("apply"))) continue;
                    if (el.disabled) continue;
                    const rect = el.getBoundingClientRect();
                    if (rect.width < 2 || rect.height < 2) continue;
                    el.click();
                    return true;
                }
                return false;
            }""")
            if clicked:
                logger.info("Submit click succeeded via DOM text/value fallback.")
                return "__dom_fallback_submit__"
        except Exception as exc:
            errors.append(f"dom_fallback: {exc}")

        joined = " | ".join(errors[-4:]) if errors else "no matching submit controls found"
        raise RuntimeError(f"Failed to click submit with fallback selectors: {joined}")

    @staticmethod
    async def _collect_unfilled_required_fields(
        driver: PlaywrightDriver,
    ) -> list[dict[str, Any]]:
        """
        Detect required fields that remain empty before submit.

        This is a safety gate to prevent false "submitted" states when required
        custom questions were not actually completed.
        """
        if not hasattr(driver, "_require_page"):
            return []

        page = driver._require_page()
        try:
            rows = await page.evaluate(
                """() => {
                    const normalize = (v) => String(v || "").replace(/\\s+/g, " ").trim();
                    const results = [];
                    const seen = new Set();
                    const isVisible = (el) => {
                      if (!el) return false;
                      const rect = el.getBoundingClientRect();
                      return rect.width > 1 && rect.height > 1;
                    };
                    const fields = Array.from(
                      document.querySelectorAll(
                        ".field, [class*='application-field'], [class*='question']",
                      ),
                    );

                    const push = (fieldId, label, kind) => {
                      const id = normalize(fieldId) || normalize(label) || "__required__";
                      const key = `${kind}:${id}`;
                      if (seen.has(key)) return;
                      seen.add(key);
                      results.push({ field_id: id, label: normalize(label), kind });
                    };

                    const inspectControl = (control, labelHint, kindHint) => {
                      if (!control) return;
                      const type = String(control.getAttribute("type") || "").toLowerCase();
                      if (type === "hidden" || type === "submit" || type === "button") return;
                      if (control.disabled) return;
                      if (!isVisible(control)) return;

                      const tag = String(control.tagName || "").toLowerCase();
                      let empty = false;
                      if (type === "checkbox" || type === "radio") {
                        const name = control.getAttribute("name");
                        if (name) {
                          const group = Array.from(document.querySelectorAll(`input[name="${name}"]`));
                          const visibleGroup = group.filter((item) => !item.disabled && isVisible(item));
                          empty = !visibleGroup.some((item) => item.checked);
                        } else {
                          empty = !control.checked;
                        }
                      } else if (type === "file") {
                        empty = !(control.files && control.files.length > 0);
                      } else if (tag === "select") {
                        empty = !normalize(control.value);
                      } else {
                        empty = !normalize(control.value);
                      }

                      if (empty) {
                        push(control.id || control.name, labelHint, kindHint || tag || "input");
                      }
                    };

                    for (const field of fields) {
                      const labelEl = field.querySelector("label");
                      const rawLabel = normalize(labelEl ? labelEl.textContent : "");
                      const cleanLabel = normalize(rawLabel.replace(/\\*/g, ""));
                      const requiredByLabel = rawLabel.includes("*");
                      const requiredByAttr = !!field.querySelector("[required], [aria-required='true']");
                      if (!requiredByLabel && !requiredByAttr) continue;

                      // React-select style custom controls.
                      const reactContainer = field.querySelector(
                        "[class*='select__container'], [class*='-control']",
                      );
                      if (reactContainer) {
                        const hasValue = !!field.querySelector(
                          "[class*='singleValue'], [class*='single-value']",
                        );
                        if (!hasValue) {
                          const input = field.querySelector("input:not([type='hidden'])");
                          push(input && (input.id || input.name), cleanLabel, "react_select");
                        }
                        continue;
                      }

                      const controls = Array.from(field.querySelectorAll("input, select, textarea"))
                        .filter((el) => {
                          const type = String(el.getAttribute("type") || "").toLowerCase();
                          if (type === "hidden" || type === "submit" || type === "button") return false;
                          if (el.disabled) return false;
                          return true;
                        });
                      if (!controls.length) continue;

                      let control = controls.find(
                        (el) => el.hasAttribute("required") || el.getAttribute("aria-required") === "true",
                      ) || controls[0];
                      inspectControl(control, cleanLabel, "input");
                    }

                    // Global fallback: required controls outside expected wrappers.
                    const globalRequired = Array.from(
                      document.querySelectorAll(
                        "input[required], select[required], textarea[required], [aria-required='true']",
                      ),
                    );
                    for (const control of globalRequired) {
                      const type = String(control.getAttribute("type") || "").toLowerCase();
                      if (type === "hidden" || type === "submit" || type === "button") continue;
                      if (control.disabled) continue;
                      if (!isVisible(control)) continue;

                      const labelEl =
                        (control.id ? document.querySelector(`label[for="${control.id}"]`) : null) ||
                        control.closest("label") ||
                        control.closest(".field, [class*='question'], [class*='application-field'], form")?.querySelector("label");
                      const rawLabel = normalize(labelEl ? labelEl.textContent : control.getAttribute("aria-label") || "");
                      const cleanLabel = normalize(rawLabel.replace(/\\*/g, ""));
                      inspectControl(control, cleanLabel, "global_required");
                    }

                    return results.slice(0, 60);
                }""",
            )
        except Exception:
            logger.debug("Required-field safety scan unavailable.", exc_info=True)
            return []

        failures: list[dict[str, Any]] = []
        for row in rows or []:
            field_id = str(row.get("field_id") or "__required__")
            label = str(row.get("label") or "").strip()
            reason = (
                f"Required field still empty before submit: {label}"
                if label
                else "Required field still empty before submit."
            )
            failures.append(
                {
                    "field_id": field_id,
                    "error_type": "required_field_unfilled",
                    "error_message": reason,
                }
            )
        return failures

    @staticmethod
    async def _has_uploaded_file(
        driver: PlaywrightDriver,
        upload_kind: str,
    ) -> bool:
        """Best-effort check that a resume/cover file is attached in DOM file inputs."""
        if not hasattr(driver, "_require_page"):
            # Test doubles may not expose DOM access; avoid false negatives there.
            return True
        page = driver._require_page()
        tokens = {
            "resume": ["resume", "cv"],
            "cover_letter": ["cover", "letter"],
        }.get(str(upload_kind or "").lower(), [])
        try:
            return bool(
                await page.evaluate(
                    """(kindTokens) => {
                        const tokens = Array.isArray(kindTokens) ? kindTokens.map((t) => String(t).toLowerCase()) : [];

                        // Method 1: Check file inputs with files attached
                        const inputs = Array.from(document.querySelectorAll("input[type='file']"));
                        const withFiles = inputs.filter((input) => input && input.files && input.files.length > 0);
                        if (withFiles.length > 0) {
                            if (!tokens.length) return true;
                            for (const input of withFiles) {
                                const wrapper = input.closest(".field, [class*='application-field'], [class*='question']");
                                const label = wrapper?.querySelector("label")?.textContent || "";
                                const haystack = [
                                    input.id || "",
                                    input.name || "",
                                    input.getAttribute("aria-label") || "",
                                    input.className || "",
                                    label,
                                ].join(" ").toLowerCase();
                                if (tokens.some((token) => haystack.includes(token))) {
                                    return true;
                                }
                            }
                        }

                        // Method 2: Check for Greenhouse's post-upload display
                        // After upload, Greenhouse shows filename text near a remove/X button
                        const uploadFields = document.querySelectorAll(".field, [class*='application-field']");
                        for (const field of uploadFields) {
                            const label = (field.querySelector("label")?.textContent || "").toLowerCase();
                            const fieldId = field.id || field.querySelector("input")?.id || "";
                            const haystack = (label + " " + fieldId).toLowerCase();
                            const isRelevant = !tokens.length || tokens.some((token) => haystack.includes(token));
                            if (!isRelevant) continue;

                            // Look for filename display (.chosen-file, .filename, or text near remove button)
                            const filenameEl = field.querySelector(".chosen-file, .filename, [class*='file-name']");
                            if (filenameEl && filenameEl.textContent.trim()) return true;

                            // Look for remove/X button (present only when file is attached)
                            const removeBtn = field.querySelector("button.remove, a.remove, [class*='remove-file'], [aria-label*='remove']");
                            if (removeBtn) return true;

                            // Check for text content matching common uploaded-file patterns
                            const fieldText = field.textContent || "";
                            if (/[.](pdf|docx?|txt|rtf)/i.test(fieldText)) return true;
                        }

                        return false;
                    }""",
                    tokens,
                )
            )
        except Exception:
            return False

    @staticmethod
    def _upload_kind_for_action(action: dict[str, Any]) -> str:
        field_id = str(action.get("field_id") or "").strip().lower()
        label = str(action.get("label") or "").strip().lower()
        value = str(action.get("value") or "").strip().lower()
        haystack = " ".join(part for part in [field_id, label, value] if part)
        if "resume" in haystack or "cv" in haystack:
            return "resume"
        if "cover" in haystack or "letter" in haystack:
            return "cover_letter"
        return ""

    async def execute_fill_plan(
        self,
        driver: PlaywrightDriver,
        fill_plan: dict[str, Any],
        artifact_paths: dict[str, str] | None = None,
        submit: bool = False,
        submit_selector: str = "button[type='submit']",
    ) -> dict[str, Any]:
        """Execute a Greenhouse fill plan through the Playwright driver."""
        actions, failures = self.plan_actions(fill_plan=fill_plan, artifact_paths=artifact_paths)
        failures.extend(await self._preflight_selectors(driver=driver, actions=actions))
        fallback_answers = {
            str(action.get("field_id") or "").strip(): str(action.get("value") or "").strip()
            for action in actions
            if str(action.get("field_id") or "").strip() and str(action.get("value") or "").strip()
        }
        deferred_select_failures: list[dict[str, Any]] = []

        preflight_hard_failures = {"form_not_detected", "listing_inactive"}
        if any(str(item.get("error_type") or "") in preflight_hard_failures for item in failures):
            return {
                "ats_type": self.ATS_TYPE,
                "status": "partial",
                "submitted": False,
                "planned_actions": len(actions),
                "executed_count": 0,
                "executed_actions": [],
                "failures": failures,
                "executed_at": datetime.now(timezone.utc).isoformat(),
                "trace_hint": f"replay/traces/{Path(self.ATS_TYPE).name}/",
            }

        executed_actions: list[dict[str, Any]] = []
        for action in actions:
            try:
                action_type = action["action"]
                selector = action["selector"]
                value = action.get("value")

                if action_type == "fill_text":
                    result = await driver.fill_field(selector=selector, value=value)
                elif action_type == "select_option":
                    selector_present = True
                    if hasattr(driver, "selector_exists"):
                        try:
                            selector_present = await driver.selector_exists(
                                selector=selector,
                                timeout_ms=900,
                            )
                        except TypeError:
                            selector_present = await driver.selector_exists(selector)
                        except Exception:
                            selector_present = True

                    if not selector_present:
                        logger.info(
                            "Deferring select field '%s' because selector '%s' was not present.",
                            action.get("field_id"),
                            selector,
                        )
                        deferred_select_failures.append(
                            {
                                "field_id": action.get("field_id"),
                                "error_type": "deferred_missing_selector",
                                "error_message": (
                                    f"Selector '{selector}' was not present during template pass; "
                                    "attempting dynamic field recovery."
                                ),
                                "action": action,
                            }
                        )
                        continue

                    try:
                        result = await driver.select_option(selector=selector, value=value)
                    except Exception as exc:
                        if not action.get("required", True):
                            logger.info(
                                "Skipping optional select field %s since it failed: %s",
                                action.get("field_id"),
                                exc,
                            )
                            continue

                        logger.info(
                            "Deferring select field '%s' after template select failure: %s",
                            action.get("field_id"),
                            exc,
                        )
                        deferred_select_failures.append(
                            {
                                "field_id": action.get("field_id"),
                                "error_type": type(exc).__name__,
                                "error_message": str(exc),
                                "action": action,
                            }
                        )
                        continue
                elif action_type == "upload_file":
                    result = await driver.upload_file(selector=selector, file_path=str(value))
                elif action_type == "click":
                    result = await driver.click(selector=selector)
                else:
                    raise ValueError(f"Unsupported action type: {action_type}")

                executed_actions.append({**action, "result": result})
                logger.info(
                    "✓ Action '%s' on field '%s' succeeded (selector=%s)",
                    action_type, action.get("field_id"), selector,
                )
            except Exception as exc:
                if not action.get("required", True):
                    logger.info("Skipping optional field %s since it failed: %s", action.get("field_id"), exc)
                    continue
                logger.error(
                    "✗ Action '%s' on field '%s' FAILED: %s (selector=%s)",
                    action_type, action.get("field_id"), exc, selector,
                )
                failures.append(
                    {
                        "field_id": action.get("field_id"),
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                        "action": action,
                    }
                )

        # --- Pass 1.5: Recover skipped fields (missing selector) by DOM search ---
        skipped_fields = [
            f for f in (fill_plan.get("fields") or [])
            if not str(f.get("selector") or "").strip()
            and str(f.get("field_id") or "") not in {a.get("field_id") for a in executed_actions}
            and str(f.get("type") or "").lower() in {"select", "dropdown", "react_select"}
        ]
        if skipped_fields:
            logger.info(
                "Attempting to recover %d skipped dropdown field(s): %s",
                len(skipped_fields),
                [f.get("field_id") for f in skipped_fields],
            )
            page = driver._require_page()
            from browser.react_select import fill_react_select_from_input_with_variants

            for skipped in skipped_fields:
                field_id = str(skipped.get("field_id") or "")
                label_text = str(skipped.get("label") or field_id).strip()
                value = str(skipped.get("value") or "").strip()
                if not value:
                    continue

                # Try to find a matching input by scanning labels in the live DOM
                try:
                    matched_selector = await page.evaluate("""([fieldId, searchLabel]) => {
                        const normalize = (s) => (s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
                        const target = normalize(searchLabel);
                        if (!target) return null;

                        // Direct ID check: try the raw field_id first (e.g. "country" -> #country)
                        for (const candidateId of [fieldId, searchLabel, searchLabel.toLowerCase()]) {
                            if (!candidateId) continue;
                            const directEl = document.getElementById(candidateId);
                            if (directEl) {
                                const role = (directEl.getAttribute('role') || '').toLowerCase();
                                const tag = directEl.tagName.toLowerCase();
                                if (role === 'combobox' || tag === 'select' || tag === 'input') {
                                    return '#' + candidateId;
                                }
                            }
                        }

                        // Search field wrappers including .select class (used by Greenhouse/AppsFlyer)
                        const fields = document.querySelectorAll('.field, .select, [class*="question"], [class*="application-field"], fieldset');
                        for (const field of fields) {
                            const labelEl = field.querySelector('label');
                            if (!labelEl) continue;
                            const fieldLabel = normalize(labelEl.textContent);
                            if (!fieldLabel.includes(target) && !target.includes(fieldLabel)) continue;

                            // Found a matching label — look for react-select input or combobox
                            const rsInput = field.querySelector('[class*="select__input"] input, input[id*="react-select"], input[role="combobox"], [role="combobox"]');
                            if (rsInput) {
                                if (rsInput.id) return '#' + rsInput.id;
                                if (rsInput.name) return 'input[name="' + rsInput.name + '"]';
                            }

                            // Or a native select
                            const nativeSelect = field.querySelector('select');
                            if (nativeSelect) {
                                if (nativeSelect.id) return '#' + nativeSelect.id;
                                if (nativeSelect.name) return 'select[name="' + nativeSelect.name + '"]';
                            }

                            // Or any combobox/listbox input
                            const combobox = field.querySelector('[role="combobox"], [aria-autocomplete="list"]');
                            if (combobox) {
                                if (combobox.id) return '#' + combobox.id;
                            }
                        }
                        return null;
                    }""", [field_id, label_text])
                except Exception as exc:
                    logger.warning("DOM label search failed for '%s': %s", field_id, exc)
                    matched_selector = None

                if not matched_selector:
                    logger.info("No DOM match found for skipped field '%s' (label='%s')", field_id, label_text)
                    continue

                logger.info(
                    "Recovered selector for '%s': %s — filling with '%s'",
                    field_id, matched_selector, value,
                )

                try:
                    # Try as react-select first
                    variants = driver._build_select_variants(value)
                    rs_result = await fill_react_select_from_input_with_variants(
                        page, matched_selector, variants,
                    )
                    if rs_result.get("status") == "filled":
                        executed_actions.append({
                            "field_id": field_id,
                            "action": "select_option",
                            "selector": matched_selector,
                            "value": value,
                            "source": "selector_recovery",
                            "result": rs_result,
                        })
                        logger.info("✓ Recovered field '%s' via react-select", field_id)
                        # Remove from failures if it was there
                        failures[:] = [f for f in failures if f.get("field_id") != field_id]
                        continue

                    # Fallback: try native select
                    result = await driver.select_option(selector=matched_selector, value=value)
                    executed_actions.append({
                        "field_id": field_id,
                        "action": "select_option",
                        "selector": matched_selector,
                        "value": value,
                        "source": "selector_recovery",
                        "result": result,
                    })
                    logger.info("✓ Recovered field '%s' via native select", field_id)
                    failures[:] = [f for f in failures if f.get("field_id") != field_id]
                except Exception as exc:
                    logger.warning("Recovery fill failed for '%s' (%s): %s", field_id, matched_selector, exc)

        # --- Pass 2: Discover and fill dynamic fields not in the template ---
        try:
            dynamic_results = await self._discover_and_fill_dynamic_fields(
                driver=driver,
                already_filled={a.get("field_id") for a in actions},
                fallback_answers=fallback_answers,
            )
            executed_actions.extend(dynamic_results.get("filled", []))
        except Exception as exc:
            logger.warning("Dynamic field discovery failed: %s", exc)

        # --- Pass 2.5: Ensure required uploads are truly attached ---
        required_upload_actions = [
            action
            for action in actions
            if str(action.get("action", "")).lower() == "upload_file" and bool(action.get("required", True))
        ]
        # Track which upload fields already succeeded in the main pass
        successfully_uploaded_kinds = set()
        for ea in executed_actions:
            if str(ea.get("action", "")).lower() == "upload_file":
                kind = self._upload_kind_for_action(ea)
                if kind:
                    successfully_uploaded_kinds.add(kind)

        for upload_action in required_upload_actions:
            upload_kind = self._upload_kind_for_action(upload_action)

            # Skip if this upload already succeeded in the main pass
            if upload_kind in successfully_uploaded_kinds:
                logger.info(
                    "Skipping upload recovery for '%s' — already uploaded successfully.",
                    upload_action.get("field_id"),
                )
                continue

            already_attached = await self._has_uploaded_file(driver=driver, upload_kind=upload_kind)
            if already_attached:
                continue

            upload_path = str(upload_action.get("value") or "").strip()
            if not upload_path:
                failures.append(
                    {
                        "field_id": upload_action.get("field_id"),
                        "error_type": "missing_file_path",
                        "error_message": "Required upload field has no file path.",
                        "action": upload_action,
                    }
                )
                continue

            try:
                result = await driver.upload_file(selector="input[type='file']", file_path=upload_path)
                executed_actions.append(
                    {
                        **upload_action,
                        "selector": "input[type='file']",
                        "source": "upload_recovery",
                        "result": result,
                    }
                )
            except Exception as exc:
                logger.warning(
                    "Upload recovery failed for %s: %s",
                    upload_action.get("field_id"),
                    exc,
                )

            recovered_attached = await self._has_uploaded_file(driver=driver, upload_kind=upload_kind)
            if not recovered_attached:
                failures.append(
                    {
                        "field_id": upload_action.get("field_id"),
                        "error_type": "required_file_unfilled",
                        "error_message": (
                            "Required upload appears missing after upload attempts."
                        ),
                        "action": upload_action,
                    }
                )

        # Safety gate: do not attempt submit when required fields remain unfilled.
        required_unfilled = await self._collect_unfilled_required_fields(driver)

        # For select fields, treat missing-template-selector failures as recoverable
        # unless the same semantic field is still required+unfilled after dynamic pass.
        required_keys = {
            self._semantic_field_key(item.get("field_id"), item.get("label"))
            for item in required_unfilled
        }
        for pending in deferred_select_failures:
            pending_key = self._semantic_field_key(
                pending.get("field_id"),
                (pending.get("action") or {}).get("label"),
            )
            if pending_key:
                if pending_key not in required_keys:
                    logger.info(
                        "Ignoring deferred select failure for recovered/non-required field '%s'.",
                        pending.get("field_id"),
                    )
                else:
                    logger.info(
                        "Deferred select failure for '%s' remains unresolved and will be covered by required-field scan.",
                        pending.get("field_id"),
                    )
                continue
            failures.append(pending)

        # Heuristic: if upload actions succeeded, avoid false positives where ATS
        # file widgets are rendered via custom wrappers but underlying required scan
        # still reports resume/cover as empty.
        uploaded_resume_action = any(
            str(item.get("action", "")).lower() == "upload_file"
            and "resume" in str(item.get("field_id", "")).lower()
            for item in executed_actions
        )
        uploaded_cover_action = any(
            str(item.get("action", "")).lower() == "upload_file"
            and "cover" in str(item.get("field_id", "")).lower()
            for item in executed_actions
        )
        uploaded_resume = uploaded_resume_action and await self._has_uploaded_file(driver=driver, upload_kind="resume")
        uploaded_cover = uploaded_cover_action and await self._has_uploaded_file(
            driver=driver,
            upload_kind="cover_letter",
        )
        # Build set of field IDs that were successfully executed
        # Normalize by lowering and stripping spaces/underscores/hyphens for fuzzy match
        def _normalize_id(s: str) -> str:
            return s.strip().lower().replace(" ", "").replace("_", "").replace("-", "")

        successfully_filled_ids = set()
        for a in executed_actions:
            if a.get("field_id"):
                raw = str(a["field_id"])
                successfully_filled_ids.add(raw.strip().lower())
                successfully_filled_ids.add(_normalize_id(raw))
            sel = str(a.get("selector") or "")
            if sel.startswith("#"):
                raw_sel = sel[1:]
                successfully_filled_ids.add(raw_sel.lower())
                successfully_filled_ids.add(_normalize_id(raw_sel))
            if a.get("label"):
                successfully_filled_ids.add(_normalize_id(str(a["label"])))

        for item in required_unfilled:
            field_id_norm = str(item.get("field_id", "")).strip().lower()
            field_id_fuzzy = _normalize_id(str(item.get("field_id", "")))
            label_fuzzy = _normalize_id(str(item.get("label", "")))
            message_norm = str(item.get("error_message", "")).strip().lower()
            if uploaded_resume and ("resume" in field_id_norm or "resume" in message_norm or "cv" in message_norm):
                continue
            if uploaded_cover and ("cover" in field_id_norm or "cover" in message_norm):
                continue
            # Skip fields that were successfully filled — DOM scan may have false positives
            if field_id_norm in successfully_filled_ids or field_id_fuzzy in successfully_filled_ids or (label_fuzzy and label_fuzzy in successfully_filled_ids):
                logger.info(
                    "Ignoring required_field_unfilled for '%s' — was successfully filled in executed_actions.",
                    field_id_norm,
                )
                continue
            failures.append(item)

        # Classify failures: only truly fatal ones should block submission.
        # missing_selector from plan_actions (e.g. country/location dropdowns)
        # should NOT prevent clicking submit — the form may still be valid.
        HARD_FAILURE_TYPES = {
            "form_not_detected",
            "listing_inactive",
            "required_field_unfilled",
            "required_file_unfilled",
        }
        hard_failures = [
            f for f in failures
            if str(f.get("error_type", "")).lower() in HARD_FAILURE_TYPES
        ]
        soft_failures = [f for f in failures if f not in hard_failures]

        if soft_failures:
            logger.info(
                "Proceeding to submit despite %d soft failure(s): %s",
                len(soft_failures),
                [f.get("field_id") for f in soft_failures],
            )
        if hard_failures:
            logger.warning(
                "Blocking submit due to %d hard failure(s): %s",
                len(hard_failures),
                [(f.get("field_id"), f.get("error_type")) for f in hard_failures],
            )

        submitted = False
        submission_check: dict[str, Any] | None = None
        if submit and not hard_failures:
            try:
                used_submit_selector = await self._click_submit_with_fallback(
                    driver=driver,
                    primary_selector=submit_selector,
                )
                submission_check = await self._wait_for_post_submit_outcome(driver)
                submitted = bool(submission_check.get("confirmed"))
                if not submitted:
                    failures.append(
                        {
                            "field_id": "__submit__",
                            "error_type": str(submission_check.get("error_type") or "SubmissionUnconfirmed"),
                            "error_message": str(
                                submission_check.get("error_message")
                                or "Submit click completed but submission could not be confirmed."
                            ),
                            "action": {
                                "action": "click",
                                "selector": used_submit_selector,
                            },
                        }
                    )
            except Exception as exc:
                failures.append(
                    {
                        "field_id": "__submit__",
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                        "action": {
                            "action": "click",
                            "selector": submit_selector,
                        },
                    }
                )

        status = "success" if not failures else "partial"
        return {
            "ats_type": self.ATS_TYPE,
            "status": status,
            "submitted": submitted,
            "planned_actions": len(actions),
            "executed_count": len(executed_actions),
            "executed_actions": executed_actions,
            "failures": failures,
            "submission_check": submission_check,
            "executed_at": datetime.now(timezone.utc).isoformat(),
            "trace_hint": f"replay/traces/{Path(self.ATS_TYPE).name}/",
        }

    @staticmethod
    def _canonical_dynamic_field_id(
        label: str,
        discovered_id: str | None = None,
    ) -> str:
        normalized_label = " ".join(str(label or "").strip().lower().split())
        normalized_id = " ".join(str(discovered_id or "").strip().lower().split())

        if "country" in normalized_label or normalized_id in {"country", "country_code"}:
            return "country"

        if (
            "location" in normalized_label
            or "city" in normalized_label
            or normalized_id in {"candidate_location", "candidate-location", "location"}
        ):
            return "candidate_location"

        if "phone" in normalized_label or normalized_id in {"phone", "phone_number", "phone-number"}:
            return "phone"

        return str(discovered_id or "").strip()

    @staticmethod
    def _semantic_field_key(field_id: Any, label: Any = None) -> str:
        haystack = " ".join(
            part
            for part in [
                str(field_id or "").strip().lower(),
                str(label or "").strip().lower(),
            ]
            if part
        )
        if "country" in haystack:
            return "country"
        if "location" in haystack or "city" in haystack:
            return "candidate_location"
        if "phone" in haystack or "tel" in haystack:
            return "phone"
        return str(field_id or "").strip().lower()

    # ------------------------------------------------------------------ #
    #  Pass 2 — Live DOM discovery for custom questions / dynamic fields  #
    # ------------------------------------------------------------------ #

    # Common answer mappings for Greenhouse custom question patterns
    _QUESTION_ANSWERS: list[tuple[list[str], str]] = [
        # Country / location
        (["country"], "United States"),
        (["what country are you based", "country you are based"], "United States"),
        (["location (city)", "city"], "Ridgefield, Connecticut"),
        # Work authorization
        (["authorized to work", "authorised to work"], "Yes"),
        # Relocation / in-office
        (["currently live in", "plan to relocate", "relocate to"], "Yes"),
        (["in-office", "acknowledge and agree", "acknowledge and agree to this requirement"], "Yes"),
        # Consent / privacy
        (["consent to", "privacy policy", "processing your personal", "do you consent"], "Yes"),
        # How did you hear
        (["how did you hear"], "LinkedIn"),
        # Capital One / previous employer
        (["previously, worked at", "currently, or have you previously", "do you currently, or have you previously"], "No"),
        # Sponsorship
        (["sponsorship", "visa"], ""),
    ]

    @staticmethod
    def _match_question_answer(label: str) -> str | None:
        """Return a best-guess answer for a common Greenhouse question, or None."""
        label_lower = label.lower()
        for patterns, answer in GreenhouseStrategy._QUESTION_ANSWERS:
            if any(p in label_lower for p in patterns):
                return answer
        return None

    @staticmethod
    def _build_dynamic_variants(label: str, answer: str) -> list[str]:
        """
        Build dynamic search variants for react-select questions.
        """
        normalized_answer = " ".join(str(answer or "").strip().lower().split())
        normalized_label = " ".join(str(label or "").strip().lower().split())

        variants: list[str] = []
        seen: set[str] = set()

        def _add(term: str) -> None:
            key = " ".join(str(term or "").strip().lower().split())
            if not key or key in seen:
                return
            seen.add(key)
            variants.append(str(term))

        if (
            normalized_answer in {"united states", "united states of america"}
            and "country" in normalized_label
        ):
            for term in ["US", "USA", "United States", "United States of America"]:
                _add(term)
            return variants

        # Consent/privacy prompts often use option text like:
        # "I consent", "I agree", or similar (not always plain "Yes").
        if "consent" in normalized_label or "privacy policy" in normalized_label:
            for term in ["I consent", "Consent", "I agree", "Agree", "Yes"]:
                _add(term)
            return variants

        _add(answer)
        if len(answer) > 5:
            first_word = answer.split()[0]
            if first_word.lower() not in {"united"}:
                _add(first_word)
        return variants

    @staticmethod
    def _fallback_answer_for_label(
        label: str,
        fallback_answers: dict[str, str] | None = None,
    ) -> str | None:
        normalized_label = " ".join(str(label or "").strip().lower().split())
        answers = {str(k or "").strip().lower(): str(v or "").strip() for k, v in (fallback_answers or {}).items()}

        if "country" in normalized_label:
            for key in ("country",):
                if answers.get(key):
                    return answers[key]

        if "location" in normalized_label:
            for key in ("candidate_location", "location"):
                if answers.get(key):
                    return answers[key]

        if "phone" in normalized_label:
            for key in ("phone",):
                if answers.get(key):
                    return answers[key]

        return None

    async def _discover_and_fill_dynamic_fields(
        self,
        driver: PlaywrightDriver,
        already_filled: set[str],
        fallback_answers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Scan the live DOM for unfilled required React Select and text fields
        that were not part of the original template fill plan.

        Uses label text matching to pick sensible defaults for common
        Greenhouse custom questions.
        """
        page = driver._require_page()
        filled: list[dict[str, Any]] = []
        from browser.react_select import fill_react_select_from_input_with_variants

        # Discover all React Select custom question fields on the page.
        # Prefer input selectors over brittle synthetic container selectors.
        react_selects = await page.evaluate("""() => {
            const results = [];
            const containers = document.querySelectorAll('[class*=select__container]');
            for (const rs of containers) {
                const input = rs.querySelector('input:not([type="hidden"])');
                if (!input) continue;

                // Find the parent field wrapper
                const fieldDiv = rs.closest('.field, .select, [class*=question]');
                if (!fieldDiv) continue;

                const labelEl = fieldDiv.querySelector('label');
                const label = labelEl ? labelEl.textContent.trim() : '';
                const required = label.includes('*');
                const hasValue = rs.querySelector('[class*=singleValue], [class*=single-value]') !== null;

                let inputSelector = null;
                if (input.id) {
                    inputSelector = '#' + input.id;
                } else if (input.name) {
                    inputSelector = `input[name="${input.name}"]`;
                }
                if (!inputSelector) continue;

                results.push({
                    inputId: input.id || '',
                    inputSelector: inputSelector,
                    label: label.replace(/\\*/g, '').trim(),
                    required: required,
                    hasValue: hasValue,
                });
            }
            return results;
        }""")

        logger.info("Dynamic discovery found %d react-select fields", len(react_selects))

        for rs in react_selects:
            input_id = rs.get("inputId", "")
            input_selector = rs.get("inputSelector")
            label = rs.get("label", "")
            required = rs.get("required", False)
            has_value = rs.get("hasValue", False)

            if input_id in already_filled or has_value:
                continue
            if not required:
                continue

            answer = self._match_question_answer(label)
            if answer is None or not str(answer).strip():
                answer = self._fallback_answer_for_label(label=label, fallback_answers=fallback_answers)
            if answer is None:
                logger.info("No auto-answer for required question: '%s' (%s)", label, input_id)
                continue
            if not answer:
                continue

            variants = self._build_dynamic_variants(label=label, answer=answer)

            logger.info(
                "Dynamic fill attempting: '%s' -> %s (selector=%s)",
                label,
                variants,
                input_selector,
            )

            try:
                if input_selector:
                    result = await fill_react_select_from_input_with_variants(
                        page, input_selector, variants,
                    )
                    if result["status"] == "filled":
                        canonical_id = self._canonical_dynamic_field_id(
                            label=label,
                            discovered_id=input_id,
                        )
                        filled.append({
                            "field_id": canonical_id,
                            "action": "select_option",
                            "selector": input_selector,
                            "value": answer,
                            "selected_text": result.get("selected_text", answer),
                            "label": label,
                            "source": "dynamic_discovery",
                        })
                        logger.info(
                            "Dynamic fill SUCCESS: '%s' -> '%s'",
                            label,
                            result.get("selected_text", answer),
                        )
                    else:
                        logger.warning(
                            "Dynamic fill failed for '%s': %s (available: %s)",
                            label, result.get("error"), result.get("available_options", []),
                        )
                else:
                    logger.warning("No input selector for '%s' - skipping", label)
            except Exception as exc:
                logger.warning("Dynamic fill exception for '%s': %s", label, exc)

        # Discover unfilled required native select controls (non-React-Select).
        native_selects = await page.evaluate("""() => {
            const results = [];
            const selects = document.querySelectorAll("select");
            for (const sel of selects) {
                if (sel.disabled) continue;
                const required = sel.hasAttribute("required") || sel.getAttribute("aria-required") === "true";
                if (!required) continue;

                const fieldDiv = sel.closest(".field, [class*=question], [class*=application-field]");
                const labelEl = fieldDiv ? fieldDiv.querySelector("label") : null;
                const rawLabel = (labelEl ? labelEl.textContent : sel.getAttribute("aria-label") || "").trim();
                const label = rawLabel.replace(/\\*/g, "").trim();

                const value = (sel.value || "").trim();
                const hasValue = value.length > 0;

                let selector = null;
                if (sel.id) {
                    selector = "#" + sel.id;
                } else if (sel.name) {
                    selector = `select[name="${sel.name}"]`;
                }
                if (!selector) continue;

                results.push({
                    inputId: sel.id || sel.name || "",
                    selector: selector,
                    label: label,
                    hasValue: hasValue,
                    required: required,
                });
            }
            return results;
        }""")

        for sel in native_selects:
            input_id = sel.get("inputId", "")
            selector = sel.get("selector")
            label = sel.get("label", "")
            required = sel.get("required", False)
            has_value = sel.get("hasValue", False)

            if input_id in already_filled or has_value or not required:
                continue
            if not selector:
                continue

            answer = self._match_question_answer(label)
            if answer is None or not str(answer).strip():
                answer = self._fallback_answer_for_label(label=label, fallback_answers=fallback_answers)
            if answer is None or not str(answer).strip():
                continue

            variants = self._build_dynamic_variants(label=label, answer=answer)
            selected = False
            last_error: str | None = None
            for variant in variants:
                try:
                    result = await driver.select_option(selector=selector, value=variant)
                    canonical_id = self._canonical_dynamic_field_id(
                        label=label,
                        discovered_id=input_id,
                    )
                    filled.append(
                        {
                            "field_id": canonical_id,
                            "action": "select_option",
                            "selector": selector,
                            "value": answer,
                            "selected_text": variant,
                            "label": label,
                            "source": "dynamic_discovery_native_select",
                            "result": result,
                        }
                    )
                    selected = True
                    break
                except Exception as exc:
                    last_error = str(exc)

            if not selected and last_error:
                logger.warning("Dynamic fill (native select) failed for %s: %s", label, last_error)

        # Discover unfilled required text inputs (non-React-Select)
        text_inputs = await page.evaluate("""() => {
            const results = [];
            const inputs = document.querySelectorAll(
                'input.input.input__single-line, input[class*="input__single-line"]'
            );
            for (const inp of inputs) {
                if (!inp.id) continue;
                const fieldDiv = inp.closest('.field, [class*=question]');
                const labelEl = fieldDiv ? fieldDiv.querySelector('label') : null;
                const ariaLabel = inp.getAttribute('aria-label') || '';
                const label = labelEl ? labelEl.textContent.trim() : ariaLabel;
                const required = inp.hasAttribute('required') ||
                    inp.getAttribute('aria-required') === 'true';
                const hasValue = inp.value.trim().length > 0;
                results.push({
                    inputId: inp.id,
                    label: label.replace(/\\*/g, '').trim(),
                    ariaLabel: ariaLabel,
                    required: required,
                    hasValue: hasValue,
                });
            }
            return results;
        }""")

        for inp in text_inputs:
            input_id = inp.get("inputId", "")
            label = inp.get("label", "") or inp.get("ariaLabel", "")
            required = inp.get("required", False)
            has_value = inp.get("hasValue", False)

            if input_id in already_filled or has_value or not required:
                continue

            answer = self._match_question_answer(label)
            if answer is None or not str(answer).strip():
                answer = self._fallback_answer_for_label(label=label, fallback_answers=fallback_answers)
            if answer is None or not answer:
                continue

            try:
                result = await driver.fill_field(selector=f"#{input_id}", value=answer)
                canonical_id = self._canonical_dynamic_field_id(
                    label=label,
                    discovered_id=input_id,
                )
                filled.append({
                    "field_id": canonical_id,
                    "action": "fill_text",
                    "selector": f"#{input_id}",
                    "value": answer,
                    "label": label,
                    "source": "dynamic_discovery",
                    "result": result,
                })
                logger.info("Dynamic fill (text): %s -> %s", label, answer)
            except Exception as exc:
                logger.warning("Dynamic fill (text) failed for %s: %s", label, exc)

        # Discover required consent/privacy checkboxes that are still unchecked.
        consent_boxes = await page.evaluate("""() => {
            const norm = (v) => String(v || "").replace(/\\s+/g, " ").trim();
            const isVisible = (el) => {
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 1 && rect.height > 1;
            };
            const results = [];
            const nodes = document.querySelectorAll("input[type='checkbox']");
            for (const cb of nodes) {
                if (cb.disabled || cb.checked) continue;
                const required = cb.hasAttribute("required") || cb.getAttribute("aria-required") === "true";
                if (!required) continue;
                if (!isVisible(cb)) continue;

                const field = cb.closest(".field, [class*='question'], [class*='application-field'], form, [role='group']");
                const labelEl =
                    (field && field.querySelector("label")) ||
                    (cb.id ? document.querySelector(`label[for="${cb.id}"]`) : null) ||
                    cb.closest("label");
                const label = norm(labelEl ? labelEl.textContent : cb.getAttribute("aria-label") || "");

                let selector = null;
                if (cb.id) {
                    selector = "#" + cb.id;
                } else if (cb.name) {
                    selector = `input[type="checkbox"][name="${cb.name}"]`;
                } else {
                    selector = "input[type='checkbox']";
                }
                results.push({
                    fieldId: cb.id || cb.name || "__consent__",
                    label,
                    selector,
                });
            }
            return results;
        }""")

        for cb in consent_boxes:
            label = str(cb.get("label") or "").strip().lower()
            selector = str(cb.get("selector") or "").strip()
            if not selector:
                continue

            # Keep this conservative to avoid consenting to unrelated optional terms.
            if not any(token in label for token in ("consent", "privacy", "policy", "agree", "terms")):
                continue

            try:
                result = await driver.click(selector=selector)
                filled.append(
                    {
                        "field_id": str(cb.get("fieldId") or "__consent__"),
                        "action": "click",
                        "selector": selector,
                        "value": "checked",
                        "label": cb.get("label"),
                        "source": "dynamic_discovery_required_checkbox",
                        "result": result,
                    }
                )
                logger.info("Dynamic fill (checkbox): %s", cb.get("label"))
            except Exception as exc:
                logger.warning("Dynamic fill (checkbox) failed for %s: %s", cb.get("label"), exc)

        return {"filled": filled}
