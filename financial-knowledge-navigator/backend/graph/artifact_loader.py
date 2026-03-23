from typing import List, Dict


def load_graph_from_extractions(knowledge_graph, extraction_batches: List[List[Dict]]) -> None:
    """
    Rebuild the in-memory graph from cached extraction batches.
    """
    for batch in extraction_batches:
        knowledge_graph.build_from_chunks(batch)
