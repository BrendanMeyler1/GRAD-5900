import networkx as nx
from backend.graph.builder import FinancialKnowledgeGraph

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
