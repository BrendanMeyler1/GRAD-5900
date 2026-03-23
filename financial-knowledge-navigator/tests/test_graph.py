from unittest.mock import MagicMock

import networkx as nx
from backend.graph.builder import FinancialKnowledgeGraph
from backend.graph.graphrag import GraphRAGEngine

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
