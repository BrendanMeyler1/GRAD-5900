import asyncio
import gc
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import networkx as nx
from backend.graph.builder import FinancialKnowledgeGraph
from backend.graph.extractor import FinancialGraphExtractor
from backend.graph.graphrag import GraphRAGEngine
from backend.graph.sqlite_store import SQLiteGraphStore

def test_graph_multi_hop():
    """Test simple graph and multi-hop radius traversal."""
    fkg = FinancialKnowledgeGraph()
    # A -> B -> C
    nodes = [
        {"id": "A", "label": "A", "entity_type": "Company"},
        {"id": "B", "label": "B", "entity_type": "Person"},
        {"id": "C", "label": "C", "entity_type": "Product"},
    ]
    edges = [
        {"source": "A", "target": "B", "type": "HAS_CEO"},
        {"source": "B", "target": "C", "type": "INVENTED"},
    ]
    
    for n in nodes:
        fkg.graph.add_node(n["id"], **n)
        
    for e in edges:
        fkg.graph.add_edge(e["source"], e["target"], **e)
    
    # Test radius 1
    sub_1 = fkg.subgraph_around_nodes(["A"], radius=1)
    assert "A" in sub_1.nodes
    assert "B" in sub_1.nodes
    assert "C" not in sub_1.nodes
    
    # Test radius 2 (multi-hop)
    sub_2 = fkg.subgraph_around_nodes(["A"], radius=2)
    assert "A" in sub_2.nodes
    assert "B" in sub_2.nodes
    assert "C" in sub_2.nodes


def test_graph_build_from_chunks_is_idempotent():
    fkg = FinancialKnowledgeGraph()
    extraction = {
        "chunk_id": "doc::abc::chunk_0",
        "source": "doc.pdf",
        "entities": [
            {"name": "Apple", "type": "Company"},
            {"name": "iPhone", "type": "Product"},
        ],
        "relationships": [
            {"source": "Apple", "target": "iPhone", "type": "PRODUCES"},
        ],
    }

    fkg.build_from_chunks([extraction])
    fkg.build_from_chunks([extraction])

    assert fkg.graph.number_of_nodes() == 2
    assert fkg.graph.number_of_edges() == 1


def test_graphrag_falls_back_to_lexical_matching_when_linker_fails():
    fkg = FinancialKnowledgeGraph()
    fkg.graph.add_node("Company::apple", label="Apple", entity_type="Company")

    linker = MagicMock()
    linker.extract_query_entities.side_effect = RuntimeError("linker failure")

    engine = GraphRAGEngine(knowledge_graph=fkg, query_graph_linker=linker)

    assert engine.find_matching_graph_nodes("How is Apple performing?") == ["Company::apple"]


def test_graphrag_short_circuits_when_graph_is_empty():
    fkg = FinancialKnowledgeGraph()
    linker = MagicMock()
    engine = GraphRAGEngine(knowledge_graph=fkg, query_graph_linker=linker)

    result = engine.build_graph_context("How is Tesla performing?")

    assert result["matched_nodes"] == []
    assert "No graph has been built yet" in result["graph_context_text"]
    linker.extract_query_entities.assert_not_called()


def test_graph_extractor_reuses_instance_across_event_loops():
    extractor = FinancialGraphExtractor()
    extractor._has_financial_entities = MagicMock(return_value=True)
    extractor.async_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(
                    return_value=SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                message=SimpleNamespace(
                                    content='{"entities": [], "relationships": []}'
                                )
                            )
                        ]
                    )
                )
            )
        )
    )

    async def run_once(chunk_id: str):
        return await extractor.extract_from_chunk_async(
            {
                "chunk_id": chunk_id,
                "source": "doc.txt",
                "text": "Revenue increased 10%",
            }
        )

    first = asyncio.run(run_once("chunk-1"))
    second = asyncio.run(run_once("chunk-2"))

    assert first["chunk_id"] == "chunk-1"
    assert second["chunk_id"] == "chunk-2"


def test_graph_extractor_chunk_heuristic_filters_non_financial_text():
    extractor = FinancialGraphExtractor()

    assert extractor.should_extract_chunk({"text": "Revenue increased 12% year over year."}) is True
    assert extractor.should_extract_chunk({"text": "This paragraph is only about colors and weather."}) is False


def test_sqlite_graph_store_rebuilds_document_graph():
    db_path = Path("tests/.tmp_sqlite_graph.db")
    if db_path.exists():
        db_path.unlink()

    try:
        store = SQLiteGraphStore(str(db_path))
        extraction = {
            "chunk_id": "chunk-1",
            "source": "tesla.pdf",
            "entities": [
                {"name": "Tesla", "type": "Organization"},
                {"name": "Automotive sales", "type": "Asset"},
            ],
            "relationships": [
                {"source": "Tesla", "target": "Automotive sales", "type": "GENERATES"},
            ],
        }

        store.replace_document_graph("tesla.pdf", [extraction])
        graph = store.get_document_graph("tesla.pdf")
        details = store.get_document_node_details("tesla.pdf")

        assert store.document_has_graph("tesla.pdf") is True
        assert graph.number_of_nodes() == 3
        assert graph.number_of_edges() == 3
        assert {detail["label"] for detail in details} == {"tesla.pdf", "Tesla", "Automotive sales"}
    finally:
        del store
        gc.collect()
        if db_path.exists():
            db_path.unlink(missing_ok=True)


def test_sqlite_graph_store_persists_structured_fact_nodes():
    db_path = Path("tests/.tmp_sqlite_graph_facts.db")
    if db_path.exists():
        db_path.unlink()

    try:
        store = SQLiteGraphStore(str(db_path))
        extraction = {
            "chunk_id": "chunk-1",
            "source": "tesla.pdf",
            "entities": [
                {"name": "Tesla", "type": "Organization"},
            ],
            "relationships": [],
        }
        structured_fact = {
            "fact_id": "fact-1",
            "source_name": "tesla.pdf",
            "metric_key": "revenue",
            "metric_label": "Revenue",
            "period": "2024",
            "value_text": "$97,690 million",
            "page_label": "Page 4",
            "section_index": 1,
        }

        store.replace_document_graph("tesla.pdf", [extraction], structured_facts=[structured_fact])
        graph = store.get_document_graph("tesla.pdf")
        details = store.get_document_node_details("tesla.pdf")

        labels = {detail["label"] for detail in details}
        entity_types = {detail["entity_type"] for detail in details}

        assert graph.number_of_nodes() >= 4
        assert graph.number_of_edges() >= 2
        assert "Tesla" in labels
        assert "2024" in labels
        assert any("Revenue: $97,690 million" in label for label in labels)
        assert {"Document", "Metric", "Period"}.issubset(entity_types)
    finally:
        del store
        gc.collect()
        if db_path.exists():
            db_path.unlink(missing_ok=True)
