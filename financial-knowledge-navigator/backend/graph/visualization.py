from typing import List, Optional
from streamlit_agraph import Node, Edge, Config


ENTITY_TYPE_COLORS = {
    "Asset": "#4CAF50",
    "Organization": "#2196F3",
    "EconomicFactor": "#FF9800",
    "MarketEvent": "#9C27B0",
    "Risk": "#F44336",
    "Mechanism": "#009688",
}


def build_agraph_elements(graph, highlighted_nodes: Optional[List[str]] = None):
    highlighted_nodes = set(highlighted_nodes or [])

    nodes = []
    edges = []

    for node_id, attrs in graph.nodes(data=True):
        label = attrs.get("label", node_id)
        entity_type = attrs.get("entity_type", "Unknown")
        sources = attrs.get("sources", [])
        if isinstance(sources, set):
            sources = sorted(list(sources))

        color = ENTITY_TYPE_COLORS.get(entity_type, "#9E9E9E")
        size = 30 if node_id in highlighted_nodes else 22
        border_width = 4 if node_id in highlighted_nodes else 2

        title = (
            f"Type: {entity_type}\n"
            f"Sources: {', '.join(sources) if sources else 'N/A'}"
        )

        nodes.append(
            Node(
                id=node_id,
                label=label,
                size=size,
                color=color,
                borderWidth=border_width,
                title=title,
            )
        )

    for source, target, attrs in graph.edges(data=True):
        rel_type = attrs.get("relationship_type", "LINKED_TO")
        source_doc = attrs.get("source_doc", "Unknown")

        edges.append(
            Edge(
                source=source,
                target=target,
                label=rel_type,
                title=f"Source doc: {source_doc}",
            )
        )

    return nodes, edges


def default_graph_config() -> Config:
    return Config(
        width="100%",
        height=650,
        directed=True,
        physics=True,
        hierarchical=False,
        nodeHighlightBehavior=True,
        highlightColor="#F7A7A6",
        collapsible=False,
        automaticRearrangeAfterDropNode=True,
    )
