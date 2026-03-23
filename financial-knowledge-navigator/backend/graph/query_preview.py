from typing import Dict, List, Optional

import networkx as nx

from backend.core.config import settings
from backend.graph.builder import FinancialKnowledgeGraph
from backend.graph.graphrag import (
    build_graph_context_from_graph,
    build_persisted_graph_context,
    collect_relevant_source_names,
)


def build_persisted_query_preview(
    graph_store,
    query: str,
    results: List[Dict],
    query_graph_linker=None,
    max_sources: Optional[int] = None,
) -> Optional[Dict]:
    if not query or not results:
        return None

    source_names = collect_relevant_source_names(results, max_sources=max_sources)
    if not source_names:
        return None

    graph_output = build_persisted_graph_context(
        graph_store=graph_store,
        query=query,
        source_names=source_names,
        query_graph_linker=query_graph_linker,
    )
    if graph_output is None:
        return None

    preview_graph = graph_output["subgraph"]
    highlighted_nodes = [
        node_id for node_id in graph_output["matched_nodes"] if preview_graph.has_node(node_id)
    ]

    detail_rows = [
        {
            "label": attrs.get("label", node_id),
            "entity_type": attrs.get("entity_type", "Unknown"),
        }
        for node_id, attrs in preview_graph.nodes(data=True)
    ][:20]

    if len(source_names) == 1:
        caption = f"Showing the persisted graph neighborhood from `{source_names[0]}` for this prompt."
    else:
        caption = (
            f"Showing the persisted graph neighborhood across {len(source_names)} relevant retrieved documents for this prompt."
        )

    return {
        "graph": FinancialKnowledgeGraph.serialize_graph_sets(preview_graph),
        "highlighted_nodes": highlighted_nodes,
        "detail_rows": detail_rows,
        "caption": caption,
        "source_names": source_names,
    }


def build_temporary_query_preview(
    query: str,
    results: List[Dict],
    graph_extractor,
    query_graph_linker=None,
    top_k: Optional[int] = None,
) -> Optional[Dict]:
    temp_graph = FinancialKnowledgeGraph()
    candidate_chunks = [
        chunk for chunk in results
        if graph_extractor.should_extract_chunk(chunk)
    ][: top_k or settings.top_k]

    for chunk in candidate_chunks:
        try:
            extraction = graph_extractor.extract_from_chunk(chunk)
        except Exception:
            continue
        temp_graph.add_extraction_result(extraction)

    graph = temp_graph.get_graph()
    if graph.number_of_nodes() == 0:
        return None

    graph_output = build_graph_context_from_graph(
        graph=graph,
        query=query,
        query_graph_linker=query_graph_linker,
        radius=1,
        max_edges=12,
    )
    if not graph_output["matched_nodes"]:
        return None
    preview_graph = graph_output["subgraph"]
    highlighted_nodes = [
        node_id for node_id in graph_output["matched_nodes"] if preview_graph.has_node(node_id)
    ]

    detail_rows = [
        {
            "label": attrs.get("label", node_id),
            "entity_type": attrs.get("entity_type", "Unknown"),
        }
        for node_id, attrs in preview_graph.nodes(data=True)
    ][:20]

    return {
        "graph": FinancialKnowledgeGraph.serialize_graph_sets(preview_graph),
        "highlighted_nodes": highlighted_nodes,
        "detail_rows": detail_rows,
        "caption": "Showing a temporary query-local graph built only from the retrieved chunks.",
        "source_names": collect_relevant_source_names(results, max_sources=2),
    }
