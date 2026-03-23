from backend.structured.facts_extractor import StructuredFactsExtractor


def test_structured_facts_extractor_finds_money_and_percentage_metrics():
    extractor = StructuredFactsExtractor()
    section = """
    [Page 4]
    Q4 2024 revenue was $25,167 million and operating income was $1,610 million.
    Gross margin reached 18.4% in Q4 2024.
    """

    facts = extractor.extract_from_section(
        section_text=section,
        source_name="tesla.pdf",
        file_hash="abc123",
        section_index=1,
    )

    assert len(facts) >= 3

    revenue_fact = next(fact for fact in facts if fact["metric_key"] == "revenue")
    assert revenue_fact["metric_label"] == "Revenue"
    assert revenue_fact["period"] == "Q4 2024"
    assert revenue_fact["currency"] == "$"
    assert revenue_fact["unit"] == "million"
    assert revenue_fact["normalized_value"] == 25_167_000_000.0
    assert revenue_fact["page_label"] == "Page 4"

    margin_fact = next(fact for fact in facts if fact["metric_key"] == "gross_margin")
    assert margin_fact["unit"] == "%"
    assert margin_fact["normalized_value"] == 0.184


def test_structured_facts_extractor_skips_lines_without_financial_values():
    extractor = StructuredFactsExtractor()
    section = """
    [Page 1]
    Tesla continues expanding manufacturing capacity globally.
    Vehicle deliveries improved year over year.
    """

    facts = extractor.extract_from_section(
        section_text=section,
        source_name="tesla.pdf",
        file_hash="def456",
        section_index=2,
    )

    assert facts == []
