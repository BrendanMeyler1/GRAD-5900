import networkx as nx

from backend.graph.visualization import get_graph_legend_items, summarize_graph_relationships


def test_summarize_graph_relationships_deduplicates_and_limits():
    graph = nx.MultiDiGraph()
    graph.add_node("Organization::tesla", label="Tesla", entity_type="Organization")
    graph.add_node("Metric::revenue", label="Revenue", entity_type="Metric")
    graph.add_node("Period::2024", label="2024", entity_type="Period")

    graph.add_edge(
        "Organization::tesla",
        "Metric::revenue",
        relationship_type="REPORTS_METRIC",
        source_doc="TSLA-Q4-2024-Update.pdf",
    )
    graph.add_edge(
        "Organization::tesla",
        "Metric::revenue",
        relationship_type="REPORTS_METRIC",
        source_doc="TSLA-Q4-2024-Update.pdf",
    )
    graph.add_edge(
        "Metric::revenue",
        "Period::2024",
        relationship_type="FOR_PERIOD",
        source_doc="TSLA-Q4-2024-Update.pdf",
    )

    summary = summarize_graph_relationships(graph, limit=5)

    assert summary == [
        {
            "source_label": "Tesla",
            "target_label": "Revenue",
            "relationship_type": "REPORTS_METRIC",
            "source_doc": "TSLA-Q4-2024-Update.pdf",
        },
        {
            "source_label": "Revenue",
            "target_label": "2024",
            "relationship_type": "FOR_PERIOD",
            "source_doc": "TSLA-Q4-2024-Update.pdf",
        },
    ]


def test_get_graph_legend_items_exposes_node_and_edge_keys():
    legend = get_graph_legend_items()

    assert any(item["label"] == "Metric" and item["shape"] == "diamond" for item in legend["nodes"])
    assert any(item["relationship_type"] == "REPORTS_METRIC" for item in legend["edges"])
