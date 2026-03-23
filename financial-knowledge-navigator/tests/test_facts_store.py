import gc
import uuid
from pathlib import Path

from backend.structured.facts_store import StructuredFactsStore


def _sample_fact(fact_id: str, file_hash: str, source_name: str, metric_key: str, metric_label: str):
    return {
        "fact_id": fact_id,
        "file_hash": file_hash,
        "source_name": source_name,
        "section_index": 1,
        "page_label": "Page 1",
        "metric_key": metric_key,
        "metric_label": metric_label,
        "period": "2024",
        "value_text": "$10 million",
        "value_numeric": 10.0,
        "normalized_value": 10_000_000.0,
        "unit": "million",
        "currency": "$",
        "evidence_text": "Revenue was $10 million.",
    }


def _temp_db_path() -> Path:
    path = Path("tests") / f".tmp_facts_{uuid.uuid4().hex}.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_structured_facts_store_replaces_and_lists_document_facts():
    db_path = _temp_db_path()
    store = StructuredFactsStore(str(db_path))
    try:
        store.replace_document_facts(
            source_name="tesla.pdf",
            file_hash="hash-1",
            facts=[
                _sample_fact("fact-1", "hash-1", "tesla.pdf", "revenue", "Revenue"),
                _sample_fact("fact-2", "hash-1", "tesla.pdf", "net_income", "Net income"),
            ],
        )

        summary = store.summary()
        assert summary == {"num_facts": 2, "num_documents": 1}
        assert store.document_fact_count(source_name="tesla.pdf") == 2

        rows = store.list_document_facts(source_name="tesla.pdf", limit=10)
        assert [row["metric_key"] for row in rows] == ["net_income", "revenue"]

        store.replace_document_facts(
            source_name="tesla.pdf",
            file_hash="hash-1",
            facts=[_sample_fact("fact-3", "hash-1", "tesla.pdf", "gross_margin", "Gross margin")],
        )

        summary = store.summary()
        assert summary == {"num_facts": 1, "num_documents": 1}
        rows = store.list_document_facts(source_name="tesla.pdf", limit=10)
        assert [row["metric_key"] for row in rows] == ["gross_margin"]
    finally:
        del store
        gc.collect()
        db_path.unlink(missing_ok=True)


def test_structured_facts_store_deletes_document_facts():
    db_path = _temp_db_path()
    store = StructuredFactsStore(str(db_path))
    try:
        store.replace_document_facts(
            source_name="tesla.pdf",
            file_hash="hash-1",
            facts=[_sample_fact("fact-1", "hash-1", "tesla.pdf", "revenue", "Revenue")],
        )

        removed = store.delete_document_facts("hash-1")

        assert removed == 1
        assert store.summary() == {"num_facts": 0, "num_documents": 0}
    finally:
        del store
        gc.collect()
        db_path.unlink(missing_ok=True)


def test_structured_facts_store_searches_relevant_facts_by_query():
    db_path = _temp_db_path()
    store = StructuredFactsStore(str(db_path))
    try:
        store.replace_document_facts(
            source_name="tesla.pdf",
            file_hash="hash-1",
            facts=[
                _sample_fact("fact-1", "hash-1", "tesla.pdf", "revenue", "Revenue"),
                {
                    **_sample_fact("fact-2", "hash-1", "tesla.pdf", "gross_margin", "Gross margin"),
                    "value_text": "18.4%",
                    "unit": "%",
                    "normalized_value": 0.184,
                    "evidence_text": "Gross margin was 18.4% in 2024.",
                },
            ],
        )

        rows = store.search_facts("What was Tesla revenue in 2024?", source_names=["tesla.pdf"], limit=5)

        assert rows
        assert rows[0]["metric_key"] == "revenue"
        assert rows[0]["match_score"] > 0
    finally:
        del store
        gc.collect()
        db_path.unlink(missing_ok=True)


def test_structured_facts_store_matches_relationship_style_finance_queries():
    db_path = _temp_db_path()
    store = StructuredFactsStore(str(db_path))
    try:
        store.replace_document_facts(
            source_name="tesla.pdf",
            file_hash="hash-1",
            facts=[
                {
                    **_sample_fact("fact-1", "hash-1", "tesla.pdf", "automotive_sales", "Automotive sales"),
                    "value_text": "$18,659 million",
                    "evidence_text": "Automotive sales were $18,659 million in fiscal year 2024.",
                    "period": "2024",
                },
                {
                    **_sample_fact("fact-2", "hash-1", "tesla.pdf", "regulatory_credits", "Regulatory credits"),
                    "value_text": "$692 million",
                    "evidence_text": "Regulatory credits were $692 million in fiscal year 2024.",
                    "period": "2024",
                },
            ],
        )

        rows = store.search_facts(
            "Show the key relationships among Tesla automotive sales regulatory credits and fiscal year 2024",
            source_names=["tesla.pdf"],
            limit=5,
        )

        assert rows
        assert {row["metric_key"] for row in rows} >= {"automotive_sales", "regulatory_credits"}
    finally:
        del store
        gc.collect()
        db_path.unlink(missing_ok=True)
