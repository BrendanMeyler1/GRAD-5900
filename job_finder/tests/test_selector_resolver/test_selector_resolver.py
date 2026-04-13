"""Tests for browser.selector_resolver."""

from browser.selector_resolver import DOMField, SelectorResolver


def _dom_fields():
    return [
        DOMField(
            tag="input",
            input_type="text",
            selector="#first_name",
            label="First Name",
            id_attr="first_name",
            name_attr="first_name",
            aria_label="First Name",
            placeholder="First Name",
            index=1,
        ),
        DOMField(
            tag="input",
            input_type="text",
            selector='[name="salary_expectation"]',
            label="Expected Salary (USD)",
            id_attr=None,
            name_attr="salary_expectation",
            aria_label="Expected Salary",
            placeholder="120000",
            index=2,
        ),
    ]


def test_exact_css_resolution():
    resolver = SelectorResolver()
    result = resolver.resolve(
        expected_selector="#first_name",
        expected_label="First Name",
        expected_type="text_input",
        dom_fields=_dom_fields(),
    )
    assert result.strategy == "exact_css"
    assert result.selector == "#first_name"


def test_fallback_to_aria_match():
    resolver = SelectorResolver()
    result = resolver.resolve(
        expected_selector="#missing_selector",
        expected_label="Expected Salary",
        expected_type="text_input",
        dom_fields=_dom_fields(),
    )
    assert result.strategy in {"label_based_xpath", "aria_label_match"}
    assert result.selector is not None


def test_resolution_failure_returns_none_strategy():
    resolver = SelectorResolver()
    result = resolver.resolve(
        expected_selector="#does_not_exist",
        expected_label="Completely unrelated label",
        expected_type="checkbox",
        dom_fields=[],
    )
    assert result.strategy == "none"
    assert result.selector is None
