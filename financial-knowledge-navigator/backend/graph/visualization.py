import textwrap
from typing import Dict, List, Optional
from streamlit_agraph import Node, Edge, Config


ENTITY_TYPE_COLORS = {
    "Document": "#607D8B",
    "Asset": "#4CAF50",
    "Organization": "#2196F3",
    "EconomicFactor": "#FF9800",
    "MarketEvent": "#9C27B0",
    "Risk": "#F44336",
    "Mechanism": "#009688",
    "Metric": "#FFC107",
    "Period": "#795548",
}

ENTITY_TYPE_SHAPES = {
    "Document": "box",
    "Organization": "dot",
    "Asset": "ellipse",
    "EconomicFactor": "ellipse",
    "MarketEvent": "ellipse",
    "Risk": "triangle",
    "Mechanism": "hexagon",
    "Metric": "diamond",
    "Period": "hexagon",
}

ENTITY_TYPE_LEVELS = {
    "Document": 0,
    "Organization": 1,
    "Asset": 2,
    "EconomicFactor": 2,
    "MarketEvent": 2,
    "Risk": 2,
    "Mechanism": 2,
    "Metric": 3,
    "Period": 4,
}

RELATIONSHIP_COLORS = {
    "MENTIONS_ENTITY": "#607D8B",
    "RELATES_TO": "#455A64",
    "REPORTS_METRIC": "#FFB300",
    "FOR_PERIOD": "#8D6E63",
    "GENERATES": "#2E7D32",
}

NODE_LEGEND_ITEMS = [
    {"label": "Document", "entity_type": "Document", "color": ENTITY_TYPE_COLORS["Document"], "shape": "box"},
    {"label": "Organization", "entity_type": "Organization", "color": ENTITY_TYPE_COLORS["Organization"], "shape": "circle"},
    {"label": "Asset", "entity_type": "Asset", "color": ENTITY_TYPE_COLORS["Asset"], "shape": "ellipse"},
    {"label": "Economic Factor", "entity_type": "EconomicFactor", "color": ENTITY_TYPE_COLORS["EconomicFactor"], "shape": "ellipse"},
    {"label": "Risk", "entity_type": "Risk", "color": ENTITY_TYPE_COLORS["Risk"], "shape": "triangle"},
    {"label": "Mechanism", "entity_type": "Mechanism", "color": ENTITY_TYPE_COLORS["Mechanism"], "shape": "hexagon"},
    {"label": "Metric", "entity_type": "Metric", "color": ENTITY_TYPE_COLORS["Metric"], "shape": "diamond"},
    {"label": "Period", "entity_type": "Period", "color": ENTITY_TYPE_COLORS["Period"], "shape": "hexagon"},
]

EDGE_LEGEND_ITEMS = [
    {"label": "Mentions", "relationship_type": "MENTIONS_ENTITY", "color": RELATIONSHIP_COLORS["MENTIONS_ENTITY"]},
    {"label": "Entity Link", "relationship_type": "RELATES_TO", "color": RELATIONSHIP_COLORS["RELATES_TO"]},
    {"label": "Reports Metric", "relationship_type": "REPORTS_METRIC", "color": RELATIONSHIP_COLORS["REPORTS_METRIC"]},
    {"label": "For Period", "relationship_type": "FOR_PERIOD", "color": RELATIONSHIP_COLORS["FOR_PERIOD"]},
    {"label": "Generates", "relationship_type": "GENERATES", "color": RELATIONSHIP_COLORS["GENERATES"]},
]


def _format_node_label(label: str, width: int = 18, max_lines: int = 3) -> str:
    normalized = " ".join((label or "").replace("::", " ").split())
    wrapped = textwrap.wrap(normalized, width=width) or [normalized or "Unknown"]
    if len(wrapped) > max_lines:
        wrapped = wrapped[:max_lines]
        wrapped[-1] = wrapped[-1][: max(0, width - 1)].rstrip() + "..."
    return "\n".join(wrapped)


def summarize_graph_relationships(graph, limit: int = 12) -> List[Dict[str, str]]:
    summaries: List[Dict[str, str]] = []
    seen = set()

    for source, target, attrs in graph.edges(data=True):
        source_label = graph.nodes[source].get("label", source)
        target_label = graph.nodes[target].get("label", target)
        relationship_type = attrs.get("relationship_type", "LINKED_TO")
        source_doc = attrs.get("source_doc", "Unknown")
        signature = (source_label, relationship_type, target_label, source_doc)
        if signature in seen:
            continue
        seen.add(signature)
        summaries.append(
            {
                "source_label": source_label,
                "target_label": target_label,
                "relationship_type": relationship_type,
                "source_doc": source_doc,
            }
        )
        if len(summaries) >= limit:
            break

    return summaries


def get_graph_legend_items() -> Dict[str, List[Dict[str, str]]]:
    return {
        "nodes": list(NODE_LEGEND_ITEMS),
        "edges": list(EDGE_LEGEND_ITEMS),
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
        base_size = 20
        if entity_type in {"Document", "Metric"}:
            base_size = 22
        elif entity_type == "Period":
            base_size = 21
        size = base_size + 8 if node_id in highlighted_nodes else base_size
        border_width = 4 if node_id in highlighted_nodes else 2
        shape = ENTITY_TYPE_SHAPES.get(entity_type, "dot")
        level = ENTITY_TYPE_LEVELS.get(entity_type, 2)

        title = (
            f"{label}\n"
            f"Type: {entity_type}\n"
            f"Sources: {', '.join(sources) if sources else 'N/A'}"
        )

        nodes.append(
            Node(
                id=node_id,
                label=_format_node_label(label),
                size=size,
                color=color,
                shape=shape,
                level=level,
                group=entity_type,
                font={
                    "size": 15,
                    "face": "Source Sans Pro",
                    "multi": True,
                    "color": "#E8EEF5",
                    "strokeWidth": 3,
                    "strokeColor": "#0E1117",
                },
                borderWidth=border_width,
                borderWidthSelected=border_width + 1,
                title=title,
            )
        )

    for source, target, attrs in graph.edges(data=True):
        rel_type = attrs.get("relationship_type", "LINKED_TO")
        source_doc = attrs.get("source_doc", "Unknown")
        edge_color = RELATIONSHIP_COLORS.get(rel_type, "#90A4AE")

        edges.append(
            Edge(
                source=source,
                target=target,
                label="",
                title=f"{rel_type}\nSource doc: {source_doc}",
                color=edge_color,
                width=2.2,
                arrows="to",
                smooth={"enabled": True, "type": "cubicBezier", "roundness": 0.12},
            )
        )

    return nodes, edges


def default_graph_config() -> Config:
    return Config(
        width="100%",
        height=650,
        directed=True,
        physics=False,
        hierarchical=True,
        nodeHighlightBehavior=True,
        highlightColor="#F7A7A6",
        collapsible=False,
        automaticRearrangeAfterDropNode=True,
        layout={
            "hierarchical": {
                "enabled": True,
                "direction": "LR",
                "sortMethod": "directed",
                "levelSeparation": 180,
                "nodeSpacing": 160,
                "treeSpacing": 220,
                "blockShifting": True,
                "edgeMinimization": True,
                "parentCentralization": True,
            }
        },
        interaction={
            "dragNodes": False,
            "hover": True,
            "zoomView": True,
            "dragView": True,
        },
        nodes={
            "font": {"size": 14, "face": "Source Sans Pro", "multi": True},
            "shadow": True,
        },
        edges={
            "font": {"size": 0},
            "selectionWidth": 3,
        },
    )
