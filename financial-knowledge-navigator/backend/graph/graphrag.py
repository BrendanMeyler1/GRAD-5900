from typing import Dict, List, Optional

import networkx as nx

from backend.core.config import settings
from backend.graph.builder import FinancialKnowledgeGraph


PERSISTED_GRAPH_MAX_SOURCES = None
PERSISTED_GRAPH_RADIUS = 2
PERSISTED_GRAPH_CONTEXT_MAX_EDGES = 24


def collect_relevant_source_names(
    results: List[Dict],
    max_sources: Optional[int] = PERSISTED_GRAPH_MAX_SOURCES,
) -> List[str]:
    source_names: List[str] = []
    seen = set()

    for result in results:
        source_name = result.get("source")
        if not source_name or source_name in seen:
            continue
        seen.add(source_name)
        source_names.append(source_name)
        if max_sources is not None and len(source_names) >= max_sources:
            break

    return source_names


def _match_query_nodes(
    graph: nx.MultiDiGraph,
    query: str,
    query_entities: Optional[List[Dict]] = None,
) -> List[str]:
    matched_node_ids = []
    query_entities = query_entities or []

    for query_entity in query_entities:
        q_name = query_entity["name"].strip().lower()
        q_type = query_entity["type"].strip()

        for node_id, attrs in graph.nodes(data=True):
            node_label = attrs.get("label", "").strip().lower()
            node_type = attrs.get("entity_type", "").strip()

            if node_type == q_type and (q_name == node_label or q_name in node_label or node_label in q_name):
                matched_node_ids.append(node_id)

    temp_graph = FinancialKnowledgeGraph()
    temp_graph.graph = graph.copy()
    lexical_matches = temp_graph.get_query_relevant_nodes(query)
    matched_node_ids.extend(lexical_matches)
    return list(dict.fromkeys(matched_node_ids))


def build_graph_context_from_graph(
    graph: nx.MultiDiGraph,
    query: str,
    query_entities: Optional[List[Dict]] = None,
    query_graph_linker=None,
    radius: int = 1,
    max_edges: int = 20,
) -> Dict:
    if graph.number_of_nodes() == 0:
        return {
            "matched_nodes": [],
            "graph_context_text": "No graph has been built yet for the indexed documents.",
            "subgraph": graph.copy(),
        }

    extracted_query_entities = query_entities
    if extracted_query_entities is None and query_graph_linker is not None:
        try:
            extracted_query_entities = query_graph_linker.extract_query_entities(query)
        except Exception:
            extracted_query_entities = []

    matched_nodes = _match_query_nodes(
        graph=graph,
        query=query,
        query_entities=extracted_query_entities,
    )

    return _build_context_for_matches(
        graph=graph,
        matched_nodes=matched_nodes,
        radius=radius,
        max_edges=max_edges,
    )


def _build_context_for_matches(
    graph: nx.MultiDiGraph,
    matched_nodes: List[str],
    radius: int = 1,
    max_edges: int = 20,
) -> Dict:
    if graph.number_of_nodes() == 0:
        return {
            "matched_nodes": [],
            "graph_context_text": "No graph has been built yet for the indexed documents.",
            "subgraph": graph.copy(),
        }

    if not matched_nodes:
        return {
            "matched_nodes": [],
            "graph_context_text": "No graph relationships were identified for this query.",
            "subgraph": graph.copy(),
        }

    temp_graph = FinancialKnowledgeGraph()
    temp_graph.graph = graph.copy()
    subgraph = temp_graph.subgraph_around_nodes(matched_nodes, radius=radius)

    lines = []
    edge_count = 0

    for source, target, attrs in subgraph.edges(data=True):
        if edge_count >= max_edges:
            break

        source_label = subgraph.nodes[source].get("label", source)
        source_type = subgraph.nodes[source].get("entity_type", "Unknown")
        target_label = subgraph.nodes[target].get("label", target)
        target_type = subgraph.nodes[target].get("entity_type", "Unknown")
        rel_type = attrs.get("relationship_type", "LINKED_TO")
        source_doc = attrs.get("source_doc", "Unknown")

        lines.append(
            f"- {source_label} ({source_type}) {rel_type} {target_label} ({target_type}) "
            f"[source: {source_doc}]"
        )
        edge_count += 1

    if not lines:
        lines.append("No connected graph relationships were found in the local neighborhood.")

    graph_context_text = "Relevant graph relationships:\n" + "\n".join(lines)

    return {
        "matched_nodes": matched_nodes,
        "graph_context_text": graph_context_text,
        "subgraph": subgraph,
    }


def build_persisted_graph_context(
    graph_store,
    query: str,
    source_names: List[str],
    query_entities: Optional[List[Dict]] = None,
    query_graph_linker=None,
    max_nodes: Optional[int] = None,
    max_edges: Optional[int] = None,
    radius: int = PERSISTED_GRAPH_RADIUS,
    context_max_edges: int = PERSISTED_GRAPH_CONTEXT_MAX_EDGES,
) -> Optional[Dict]:
    if graph_store is None or not query or not source_names:
        return None

    source_count = max(len(source_names), 1)
    effective_source_count = min(source_count, 8)
    extracted_query_entities = query_entities

    checker = getattr(graph_store, "is_configured", None)
    if checker is not None:
        try:
            if not checker():
                return None
        except Exception:
            return None

    if extracted_query_entities is None and query_graph_linker is not None:
        try:
            extracted_query_entities = query_graph_linker.extract_query_entities(query)
        except Exception:
            extracted_query_entities = []

    neighborhood_getter = getattr(graph_store, "get_query_neighborhood", None)
    if callable(neighborhood_getter):
        try:
            neighborhood = neighborhood_getter(
                source_names=source_names,
                query=query,
                query_entities=extracted_query_entities,
                radius=radius,
                max_nodes=max_nodes if max_nodes is not None else max(settings.top_k * 12, 40, effective_source_count * 18),
                max_edges=max_edges if max_edges is not None else max(settings.top_k * 16, 60, effective_source_count * 28),
            )
        except Exception:
            neighborhood = None

        if isinstance(neighborhood, dict):
            neighborhood_graph = neighborhood.get("graph")
            matched_node_ids = neighborhood.get("matched_node_ids") or []
            if (
                neighborhood_graph is not None
                and hasattr(neighborhood_graph, "number_of_nodes")
                and neighborhood_graph.number_of_nodes() > 0
                and matched_node_ids
            ):
                graph_output = _build_context_for_matches(
                    graph=neighborhood_graph,
                    matched_nodes=matched_node_ids,
                    radius=1,
                    max_edges=context_max_edges,
                )
                return {
                    "source_names": neighborhood.get("source_names", source_names),
                    "matched_nodes": graph_output["matched_nodes"],
                    "graph_context_text": graph_output["graph_context_text"],
                    "subgraph": graph_output["subgraph"],
                    "graph_context_origin": "persisted_graph",
                }

    getter = getattr(graph_store, "get_sources_graph", None)
    if getter is None:
        return None

    try:
        persisted_graph = getter(
            source_names,
            max_nodes=max_nodes if max_nodes is not None else max(settings.top_k * 12, 40, effective_source_count * 18),
            max_edges=max_edges if max_edges is not None else max(settings.top_k * 16, 60, effective_source_count * 28),
        )
    except Exception:
        return None

    if persisted_graph.number_of_nodes() == 0:
        return None

    graph_output = build_graph_context_from_graph(
        graph=persisted_graph,
        query=query,
        query_entities=extracted_query_entities,
        query_graph_linker=query_graph_linker,
        radius=radius,
        max_edges=context_max_edges,
    )
    if not graph_output.get("matched_nodes"):
        return None

    return {
        "source_names": source_names,
        "matched_nodes": graph_output["matched_nodes"],
        "graph_context_text": graph_output["graph_context_text"],
        "subgraph": graph_output["subgraph"],
        "graph_context_origin": "persisted_graph",
    }


class GraphRAGEngine:
    def __init__(self, knowledge_graph, query_graph_linker):
        self.knowledge_graph = knowledge_graph
        self.query_graph_linker = query_graph_linker

    def find_matching_graph_nodes(self, query: str) -> List[str]:
        """
        First tries LLM-based query entity extraction, then matches those entities
        to graph nodes. Falls back to lexical node matching if necessary.
        """
        graph = self.knowledge_graph.get_graph()
        if graph.number_of_nodes() == 0:
            return []
        try:
            query_entities = self.query_graph_linker.extract_query_entities(query)
        except Exception:
            query_entities = []
        return _match_query_nodes(
            graph=graph,
            query=query,
            query_entities=query_entities,
        )

    def build_graph_context(self, query: str, radius: int = 1, max_edges: int = 20) -> Dict:
        """
        Builds a compact graph context around query-relevant nodes.
        """
        graph = self.knowledge_graph.get_graph()
        return build_graph_context_from_graph(
            graph=graph,
            query=query,
            query_graph_linker=self.query_graph_linker,
            radius=radius,
            max_edges=max_edges,
        )
