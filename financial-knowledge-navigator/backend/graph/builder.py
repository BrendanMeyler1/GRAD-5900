from typing import Dict, List
import networkx as nx


class FinancialKnowledgeGraph:
    def __init__(self):
        self.graph = nx.MultiDiGraph()

    def _node_id(self, entity_name: str, entity_type: str) -> str:
        return f"{entity_type}::{entity_name.strip().lower()}"

    def add_extraction_result(self, extraction: Dict) -> None:
        chunk_id = extraction.get("chunk_id")
        source = extraction.get("source")

        entity_name_to_node_id = {}

        for entity in extraction.get("entities", []):
            name = entity["name"].strip()
            entity_type = entity["type"].strip()
            node_id = self._node_id(name, entity_type)
            entity_name_to_node_id[name] = node_id

            if not self.graph.has_node(node_id):
                self.graph.add_node(
                    node_id,
                    label=name,
                    entity_type=entity_type,
                    sources=set([source]) if source else set(),
                    chunk_ids=set([chunk_id]) if chunk_id else set(),
                )
            else:
                if source:
                    self.graph.nodes[node_id]["sources"].add(source)
                if chunk_id:
                    self.graph.nodes[node_id]["chunk_ids"].add(chunk_id)

        for rel in extraction.get("relationships", []):
            source_name = rel["source"]
            target_name = rel["target"]
            rel_type = rel["type"]

            source_node_id = entity_name_to_node_id.get(source_name)
            target_node_id = entity_name_to_node_id.get(target_name)

            if not source_node_id or not target_node_id:
                continue

            self.graph.add_edge(
                source_node_id,
                target_node_id,
                relationship_type=rel_type,
                source_doc=source,
                chunk_id=chunk_id,
            )

    def build_from_chunks(self, extracted_chunks: List[Dict]) -> None:
        for extraction in extracted_chunks:
            self.add_extraction_result(extraction)

    def get_graph(self) -> nx.MultiDiGraph:
        return self.graph

    def get_query_relevant_nodes(self, query: str) -> List[str]:
        query_lower = query.lower()
        matched = []

        for node_id, attrs in self.graph.nodes(data=True):
            label = attrs.get("label", "").lower()
            entity_type = attrs.get("entity_type", "").lower()

            if label in query_lower or any(token in label for token in query_lower.split()):
                matched.append(node_id)
            elif entity_type in query_lower:
                matched.append(node_id)

        return list(dict.fromkeys(matched))

    def subgraph_around_nodes(self, node_ids: List[str], radius: int = 1) -> nx.MultiDiGraph:
        if not node_ids:
            return self.graph.copy()

        selected = set(node_ids)

        for _ in range(radius):
            neighbors = set()
            for node_id in list(selected):
                neighbors.update(self.graph.predecessors(node_id))
                neighbors.update(self.graph.successors(node_id))
            selected.update(neighbors)

        return self.graph.subgraph(selected).copy()

    def get_node_details(self, node_ids: List[str]) -> List[Dict]:
        details = []
        for node_id in node_ids:
            if self.graph.has_node(node_id):
                attrs = self.graph.nodes[node_id]
                details.append(
                    {
                        "node_id": node_id,
                        "label": attrs.get("label"),
                        "entity_type": attrs.get("entity_type"),
                        "sources": sorted(list(attrs.get("sources", []))) if isinstance(attrs.get("sources"), set) else attrs.get("sources", []),
                        "chunk_ids": sorted(list(attrs.get("chunk_ids", []))) if isinstance(attrs.get("chunk_ids"), set) else attrs.get("chunk_ids", []),
                    }
                )
        return details

    def graph_summary(self) -> Dict[str, int]:
        return {
            "num_nodes": self.graph.number_of_nodes(),
            "num_edges": self.graph.number_of_edges(),
        }

    @staticmethod
    def serialize_graph_sets(graph: nx.MultiDiGraph) -> nx.MultiDiGraph:
        copied = graph.copy()

        for node_id, attrs in copied.nodes(data=True):
            for key, value in list(attrs.items()):
                if isinstance(value, set):
                    attrs[key] = sorted(list(value))

        return copied
