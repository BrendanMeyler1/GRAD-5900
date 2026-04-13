"""
Reusable handler for react-select dropdown components.

Used by all ATS strategies (Greenhouse, Lever, Workday) since many ATS platforms
use react-select or similar custom dropdown libraries.

react-select renders as <div> wrappers, NOT native <select> elements.
Standard Playwright .select_option() and .fill() silently fail:
- .select_option() → does nothing (no <select> tag)
- .fill() → updates visual text but doesn't trigger React onChange,
  leaving form validation in "required" error state

Correct interaction:
  1. Click container → opens dropdown
  2. Type into search input → filters options
  3. Click matching option from rendered list → triggers onChange
  4. Verify React state updated
"""

import logging
from typing import Optional
from playwright.async_api import Page, Frame

logger = logging.getLogger("job_finder.browser.react_select")

_US_SELECTION_ALIASES = {
    "us",
    "u.s.",
    "usa",
    "u.s.a.",
    "united states",
    "united states of america",
}


# =============================================================================
# DETECTION
# =============================================================================

async def is_react_select(context: Page | Frame, container_selector: str) -> bool:
    """Detect whether a container holds a react-select component."""
    indicators = [
        f'{container_selector} [class*="select__control"]',
        f'{container_selector} [class*="-control"][class*="css-"]',
        f'{container_selector} [class*="select__placeholder"]',
        f'{container_selector} [class*="select__indicator"]',
        f'{container_selector} [class*="css-"][role="combobox"]',
    ]
    for sel in indicators:
        if await context.query_selector(sel):
            return True
    return False


async def detect_dropdown_type(
    context: Page | Frame, container_selector: str
) -> str:
    """
    Classify a dropdown field.

    Returns: "native_select" | "react_select" | "combobox" | "unknown"
    """
    if await context.query_selector(f"{container_selector} select"):
        return "native_select"
    if await is_react_select(context, container_selector):
        return "react_select"
    if await context.query_selector(f'{container_selector} [role="combobox"]'):
        return "combobox"
    return "unknown"


# =============================================================================
# CORE: FILL A REACT-SELECT
# =============================================================================

async def fill_react_select(
    context: Page | Frame,
    container_selector: str,
    value: str,
    exact_match: bool = False,
    typing_delay_ms: int = 80,
) -> dict:
    """
    Fill a react-select dropdown by opening it, typing to filter,
    and CLICKING the matching option (which triggers React's onChange).

    Args:
        context:            Page or Frame containing the form.
        container_selector: CSS selector for the field wrapper div.
        value:              Option text to select.
        exact_match:        Require exact text match.
        typing_delay_ms:    Keystroke delay for humanization.

    Returns:
        {"status": "filled"|"error", "selected_text": ..., ...}
    """
    try:
        # 1 — open the dropdown by clicking the control
        control = await _find_control(context, container_selector)
        if not control:
            return _err("Could not find react-select control element")

        await control.click()
        await context.wait_for_timeout(180)

        # 2 — clear any existing value
        await _clear_existing(context, container_selector, control)

        # 3 — find the search input and type
        search_input = await _find_search_input(context, container_selector)
        if search_input:
            await search_input.fill("")
            await search_input.type(value, delay=typing_delay_ms)
            await context.wait_for_timeout(250)
        else:
            logger.debug("No search input found — selecting from open menu directly")

        # 4 — click the matching option (this is what triggers onChange!)
        result = await _click_matching_option(context, container_selector, value, exact_match)
        if result["status"] != "filled":
            return result

        # 5 — verify React state updated
        verification = await _verify_selection(context, container_selector, result["selected_text"])
        result["verification"] = verification
        if not verification["registered"]:
            result["status"] = "error"
            result["error"] = verification.get("error", "Selection did not register in React state")

        return result

    except Exception as e:
        logger.error("react-select fill failed: %s", e, exc_info=True)
        return _err(str(e))


async def fill_react_select_with_variants(
    context: Page | Frame,
    container_selector: str,
    variants: list[str],
    typing_delay_ms: int = 80,
) -> dict:
    """
    Try multiple search terms until one matches.

    Essential for country fields where "United States" might show "No options"
    but "United" or "US" matches successfully.
    """
    expanded_variants = _expand_variants(variants)
    expected_aliases = _expected_aliases(expanded_variants)
    last_result = None
    for term in expanded_variants:
        last_result = await fill_react_select(
            context, container_selector, term, typing_delay_ms=typing_delay_ms,
        )
        if last_result["status"] == "filled":
            selected_text = str(last_result.get("selected_text") or "")
            if expected_aliases and not _selection_in_aliases(selected_text, expected_aliases):
                last_result = _err(
                    (
                        f"Selected '{selected_text}' while targeting "
                        f"{sorted(expected_aliases)}"
                    ),
                    selected_text=selected_text,
                )
                logger.debug("Variant '%s' produced mismatched selection '%s'", term, selected_text)
                continue
            return last_result
        logger.debug("Variant '%s' did not match, trying next...", term)

    return last_result or _err(f"All variants failed: {expanded_variants}")


async def fill_react_select_from_input(
    context: Page | Frame,
    input_selector: str,
    value: str,
    exact_match: bool = False,
    typing_delay_ms: int = 80,
) -> dict:
    """
    Fill react-select by targeting its input element directly.

    This avoids brittle container selector construction and is safer for pages
    with repeated custom question blocks (like Greenhouse custom questions).
    """
    try:
        input_locator = await _resolve_input_locator(context, input_selector)
        if input_locator is None:
            return _err(f"Could not resolve react-select input for '{input_selector}'")

        await input_locator.click()
        await context.wait_for_timeout(180)

        # Clear any prior search/filter text before typing.
        try:
            await input_locator.press("Control+A")
            await input_locator.press("Backspace")
        except Exception:
            await input_locator.fill("")

        await input_locator.type(value, delay=typing_delay_ms)
        await context.wait_for_timeout(220)

        result = await _click_matching_visible_option(context, value, exact_match)
        if result["status"] != "filled":
            return result

        verification = await _verify_selection_from_input(
            context, input_locator, result["selected_text"],
        )
        result["verification"] = verification
        if not verification["registered"]:
            result["status"] = "error"
            result["error"] = verification.get(
                "error", "Selection did not register in React state",
            )
        return result
    except Exception as exc:
        logger.error("react-select input-mode fill failed: %s", exc, exc_info=True)
        return _err(str(exc))


async def fill_react_select_from_input_with_variants(
    context: Page | Frame,
    input_selector: str,
    variants: list[str],
    typing_delay_ms: int = 80,
) -> dict:
    """Try multiple search terms while targeting a specific react-select input."""
    expanded_variants = _expand_variants(variants)
    expected_aliases = _expected_aliases(expanded_variants)
    last_result = None
    for term in expanded_variants:
        last_result = await fill_react_select_from_input(
            context=context,
            input_selector=input_selector,
            value=term,
            typing_delay_ms=typing_delay_ms,
        )
        if last_result["status"] == "filled":
            selected_text = str(last_result.get("selected_text") or "")
            if expected_aliases and not _selection_in_aliases(selected_text, expected_aliases):
                last_result = _err(
                    (
                        f"Selected '{selected_text}' while targeting "
                        f"{sorted(expected_aliases)}"
                    ),
                    selected_text=selected_text,
                )
                logger.debug(
                    "Input variant '%s' produced mismatched selection '%s'",
                    term,
                    selected_text,
                )
                continue
            return last_result
        logger.debug("Input variant '%s' did not match, trying next...", term)
    return last_result or _err(f"All variants failed: {expanded_variants}")


# =============================================================================
# SMART DISPATCHER
# =============================================================================

async def fill_any_dropdown(
    context: Page | Frame,
    container_selector: str,
    value: str,
    typing_delay_ms: int = 80,
) -> dict:
    """
    Auto-detects dropdown type (native <select> vs react-select) and fills.
    Drop-in replacement for the old _fill_select approach.
    """
    dd_type = await detect_dropdown_type(context, container_selector)

    if dd_type == "native_select":
        return await _fill_native_select(context, container_selector, value)

    if dd_type in ("react_select", "combobox"):
        return await fill_react_select(
            context, container_selector, value, typing_delay_ms=typing_delay_ms,
        )

    # Unknown — try react-select first, fall back to native
    result = await fill_react_select(
        context, container_selector, value, typing_delay_ms=typing_delay_ms,
    )
    if result["status"] == "filled":
        return result
    return await _fill_native_select(context, container_selector, value)


# =============================================================================
# INTERNALS
# =============================================================================

async def _find_control(context: Page | Frame, container_selector: str):
    """Find the clickable control element inside a react-select."""
    for sel in [
        f'{container_selector} [class*="select__control"]',
        f'{container_selector} [class*="-control"]',
        f'{container_selector} [role="combobox"]',
        f'{container_selector} [class*="select"]',
    ]:
        el = await context.query_selector(sel)
        if el:
            return el
    return None


async def _resolve_input_locator(context: Page | Frame, selector: str):
    """
    Resolve an input locator from either an input selector or a container selector.
    Returns None if no suitable input can be found quickly.
    """
    candidate = context.locator(selector).first
    try:
        await candidate.wait_for(state="attached", timeout=1500)
    except Exception:
        return None

    try:
        tag_name = await candidate.evaluate("el => (el.tagName || '').toLowerCase()")
    except Exception:
        tag_name = ""
    if tag_name == "input":
        input_type = (await candidate.get_attribute("type") or "").strip().lower()
        if input_type != "hidden":
            return candidate

    nested_input = candidate.locator("input:not([type='hidden'])").first
    try:
        await nested_input.wait_for(state="attached", timeout=1200)
        return nested_input
    except Exception:
        return None


async def _find_search_input(context: Page | Frame, container_selector: str):
    """Find the hidden search/filter input inside a react-select."""
    for sel in [
        f'{container_selector} input[class*="select__input"]',
        f'{container_selector} input[id*="react-select"]',
        f'{container_selector} [class*="-control"] input',
        f'{container_selector} input[role="combobox"]',
        f'{container_selector} input[aria-autocomplete="list"]',
        f'{container_selector} input:not([type="hidden"])',
    ]:
        el = await context.query_selector(sel)
        if el:
            return el
    return None


async def _clear_existing(context: Page | Frame, container_selector: str, control):
    """Clear any existing selection before typing a new one."""
    clear_btn = await context.query_selector(
        f'{container_selector} [class*="select__clear-indicator"], '
        f'{container_selector} [class*="-clear-indicator"], '
        f'{container_selector} [aria-label="Remove"]'
    )
    if clear_btn:
        await clear_btn.click()
        await context.wait_for_timeout(120)
        await control.click()
        await context.wait_for_timeout(150)


async def _click_matching_option(
    context: Page | Frame, container_selector: str, value: str, exact: bool
) -> dict:
    """Find and click the best-matching option from the open dropdown menu."""

    # Wait for the menu to appear — check both inside container and portal
    menu = None
    for sel in [
        f'{container_selector} [class*="select__menu"]',
        f'{container_selector} [class*="-menu"]',
        f'{container_selector} [role="listbox"]',
        '[class*="select__menu-portal"] [class*="select__menu"]',
        '[class*="select__menu"]',
    ]:
        try:
            menu = await context.wait_for_selector(sel, timeout=3000)
            if menu:
                break
        except Exception:
            continue

    if not menu:
        return _err("Dropdown menu did not appear after clicking")

    # Gather option elements
    options = []
    for sel in [
        f'{container_selector} [class*="select__option"]',
        f'{container_selector} [role="option"]',
        '[class*="select__menu-portal"] [class*="select__option"]',
        '[class*="select__option"]',
        '[role="option"]',
    ]:
        options = await context.query_selector_all(sel)
        if options:
            break

    if not options:
        # Check for "No options" message
        no_opts = await context.query_selector(
            '[class*="select__menu-notice"], [class*="NoOptionsMessage"]'
        )
        if no_opts:
            msg = await no_opts.inner_text()
            return _err(
                f"No matching options: '{msg}'",
                search_value=value,
                suggestion="Try a shorter or alternative search term.",
            )
        return _err("No options found in dropdown")

    # Score options — find best match
    val_lower = value.lower().strip()
    best, best_score = None, 0

    for opt in options:
        text = (await opt.inner_text()).strip()
        if not text or "no option" in text.lower():
            continue
        t_lower = text.lower()

        # Exact match → score 100 (best possible)
        if t_lower == val_lower:
            best, best_score = opt, 100
            break

        if not exact:
            # Search value contained in option text
            if val_lower in t_lower:
                s = len(val_lower) / len(t_lower) * 90
                if s > best_score:
                    best, best_score = opt, s
            # Option text contained in search value
            elif t_lower in val_lower:
                s = len(t_lower) / len(val_lower) * 80
                if s > best_score:
                    best, best_score = opt, s

    if not best:
        available = []
        for o in options:
            t = (await o.inner_text()).strip()
            if t:
                available.append(t)
        return _err(
            f"No matching option for '{value}'",
            available_options=available,
            suggestion=f"Try one of: {available}",
        )

    selected_text = (await best.inner_text()).strip()
    await best.click()
    await context.wait_for_timeout(140)

    return {"status": "filled", "selected_text": selected_text, "match_score": best_score}


async def _click_matching_visible_option(
    context: Page | Frame,
    value: str,
    exact: bool,
) -> dict:
    """
    Find and click the best matching *visible* option in the currently open menu.

    Visibility filtering prevents clicking stale/hidden options from previously
    rendered react-select menus elsewhere on the page.
    """
    options_locator = context.locator(
        "[role='option']:visible, [class*='select__option']:visible, [id*='-option-']:visible",
    )
    try:
        await options_locator.first.wait_for(state="visible", timeout=3000)
    except Exception:
        return _err("Dropdown options did not appear after typing")

    option_count = await options_locator.count()
    if option_count == 0:
        return _err("No visible options found in dropdown")

    val_lower = _normalize_text(value)
    best_index: int | None = None
    best_text = ""
    best_score = 0.0
    available: list[str] = []

    for idx in range(min(option_count, 60)):
        option = options_locator.nth(idx)
        text = (await option.inner_text()).strip()
        if not text:
            continue
        text_norm = _normalize_text(text)
        if not text_norm:
            continue
        available.append(text)
        if "no options" in text_norm or "start typing" in text_norm:
            continue

        if text_norm == val_lower:
            best_index = idx
            best_text = text
            best_score = 100.0
            break

        if exact:
            continue

        if val_lower in text_norm:
            score = (len(val_lower) / max(len(text_norm), 1)) * 90.0
            if score > best_score:
                best_index = idx
                best_text = text
                best_score = score
        elif text_norm in val_lower:
            score = (len(text_norm) / max(len(val_lower), 1)) * 80.0
            if score > best_score:
                best_index = idx
                best_text = text
                best_score = score

    if best_index is None:
        notice = context.locator(
            "[class*='select__menu-notice']:visible, [class*='NoOptionsMessage']:visible",
        )
        if await notice.count():
            msg = (await notice.first.inner_text()).strip()
            return _err(
                f"No matching options: '{msg}'",
                search_value=value,
                suggestion="Try a shorter or alternative search term.",
            )
        return _err(
            f"No matching option for '{value}'",
            available_options=available[:12],
        )

    chosen = options_locator.nth(best_index)
    await chosen.click()
    await context.wait_for_timeout(120)
    return {
        "status": "filled",
        "selected_text": best_text,
        "match_score": best_score,
    }


async def _verify_selection(
    context: Page | Frame, container_selector: str, expected: str
) -> dict:
    """Confirm react-select registered the value in React state."""
    await context.wait_for_timeout(120)

    # Check for single-value display element
    for sel in [
        f'{container_selector} [class*="select__single-value"]',
        f'{container_selector} [class*="-singleValue"]',
    ]:
        el = await context.query_selector(sel)
        if el:
            text = (await el.inner_text()).strip()
            if expected.lower() in text.lower():
                return {"registered": True, "display_text": text}

    # Check that placeholder is gone (indirect evidence)
    ph = await context.query_selector(
        f'{container_selector} [class*="select__placeholder"]'
    )
    if ph and await ph.is_visible():
        return {
            "registered": False,
            "error": "Placeholder still visible — selection not registered",
        }

    # Check for hidden input with a value
    hidden = await context.query_selector(
        f'{container_selector} input[type="hidden"]'
    )
    if hidden:
        v = await hidden.get_attribute("value")
        if v:
            return {"registered": True, "hidden_value": v}

    return {"registered": False, "error": "Could not verify selection"}


async def _verify_selection_from_input(
    context: Page | Frame,
    input_locator,
    expected: str,
) -> dict:
    """
    Verify selection state using the input's local field subtree.

    Falls back to an inferred-success signal when placeholder is gone but direct
    single-value/hidden-value checks are inconclusive.
    """
    await context.wait_for_timeout(120)

    expected_norm = _normalize_text(expected)
    result = await input_locator.evaluate(
        """(input, expectedText) => {
            const normalize = (v) => (v || "").toLowerCase().replace(/\\s+/g, " ").trim();
            const field = input.closest(
              ".field, [class*='application-field'], [class*='question'], [class*='select']",
            ) || input.parentElement;

            if (!field) {
              return { registered: false, error: "Could not locate field container" };
            }

            const single = field.querySelector(
              "[class*='select__single-value'], [class*='singleValue'], [class*='single-value']",
            );
            if (single) {
              const text = (single.textContent || "").trim();
              if (normalize(text).includes(expectedText)) {
                return { registered: true, display_text: text };
              }
            }

            const hiddenInputs = field.querySelectorAll("input[type='hidden']");
            for (const hidden of hiddenInputs) {
              const value = (hidden.value || "").trim();
              if (value) {
                return { registered: true, hidden_value: value };
              }
            }

            const placeholder = field.querySelector(
              "[class*='select__placeholder'], [class*='placeholder']",
            );
            if (placeholder && placeholder.offsetParent !== null) {
              return {
                registered: false,
                error: "Placeholder still visible - selection may not have registered",
              };
            }

            return {
              registered: true,
              inferred: true,
              note: "Selection inferred from closed menu / hidden placeholder.",
            };
        }""",
        expected_norm,
    )
    if isinstance(result, dict):
        return result
    return {"registered": False, "error": "Could not verify selection"}


async def _fill_native_select(
    context: Page | Frame, container_selector: str, value: str
) -> dict:
    """Fill a standard HTML <select> element."""
    sel_el = await context.query_selector(f"{container_selector} select")
    if not sel_el:
        return _err("No <select> element found")

    # Try exact label match
    try:
        await sel_el.select_option(label=value)
        return {"status": "filled", "type": "native_select", "value": value}
    except Exception:
        pass

    # Fuzzy match against all options
    opts = await context.eval_on_selector_all(
        f"{container_selector} select option",
        "os => os.map(o => ({value: o.value, text: o.textContent.trim()}))",
    )
    for o in opts:
        if value.lower() in o["text"].lower():
            await sel_el.select_option(value=o["value"])
            return {"status": "filled", "type": "native_select", "value": o["text"]}

    return _err(
        f"No matching option for '{value}'",
        available_options=[o["text"] for o in opts],
    )


def _err(msg: str, **extra) -> dict:
    """Build an error result dict."""
    d = {"status": "error", "error": msg}
    d.update(extra)
    return d


def _normalize_text(value: str | None) -> str:
    return " ".join((value or "").split()).strip().lower()


def _expand_variants(variants: list[str]) -> list[str]:
    expanded: list[str] = []
    seen: set[str] = set()
    for raw in variants or []:
        term = str(raw or "").strip()
        if not term:
            continue
        norm = _normalize_text(term)
        if norm and norm not in seen:
            seen.add(norm)
            expanded.append(term)

    # Country-specific fallback order for US fields.
    if "united states" in seen or "united states of america" in seen:
        us_order = ["US", "USA", "United States", "United States of America"]
        reordered: list[str] = []
        added: set[str] = set()
        for term in us_order:
            norm = _normalize_text(term)
            if norm in seen and norm not in added:
                reordered.append(term)
                added.add(norm)
        for term in expanded:
            norm = _normalize_text(term)
            if norm not in added:
                reordered.append(term)
                added.add(norm)
        expanded = reordered
    return expanded


def _expected_aliases(variants: list[str]) -> set[str] | None:
    normalized = {_normalize_text(item) for item in variants if str(item).strip()}
    if "united states" in normalized or "united states of america" in normalized:
        return set(_US_SELECTION_ALIASES)
    return None


def _selection_in_aliases(selected_text: str, aliases: set[str]) -> bool:
    selected_norm = _normalize_text(selected_text)
    if selected_norm in aliases:
        return True
    for alias in aliases:
        if len(alias) >= 4 and alias in selected_norm:
            return True
    return False
