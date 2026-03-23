from typing import Dict, List


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
        try:
            query_entities = self.query_graph_linker.extract_query_entities(query)
        except Exception:
            query_entities = []

        matched_node_ids = []

        for query_entity in query_entities:
            q_name = query_entity["name"].strip().lower()
            q_type = query_entity["type"].strip()

            for node_id, attrs in graph.nodes(data=True):
                node_label = attrs.get("label", "").strip().lower()
                node_type = attrs.get("entity_type", "").strip()

                if node_type == q_type and (q_name == node_label or q_name in node_label or node_label in q_name):
                    matched_node_ids.append(node_id)

        # Deduplicate while preserving order
        matched_node_ids = list(dict.fromkeys(matched_node_ids))

        if matched_node_ids:
            return matched_node_ids

        return self.knowledge_graph.get_query_relevant_nodes(query)

    def build_graph_context(self, query: str, radius: int = 1, max_edges: int = 20) -> Dict:
        """
        Builds a compact graph context around query-relevant nodes.
        """
        graph = self.knowledge_graph.get_graph()
        matched_nodes = self.find_matching_graph_nodes(query)

        if not matched_nodes:
            return {
                "matched_nodes": [],
                "graph_context_text": "No graph relationships were identified for this query.",
                "subgraph": graph.copy(),
            }

        subgraph = self.knowledge_graph.subgraph_around_nodes(matched_nodes, radius=radius)

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
