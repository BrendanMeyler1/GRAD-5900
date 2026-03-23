from abc import ABC, abstractmethod
from typing import Dict, List, Optional

import networkx as nx


class GraphStore(ABC):
    """
    Base interface for persistent graph backends.

    SQLite remains the local fallback, while Neo4j can be introduced without
    forcing the rest of the app to change its graph-facing contract.
    """

    backend_name: str = "graph_store"

    @abstractmethod
    def clear(self) -> None:
        """Delete all persisted graph state for the current backend."""

    @abstractmethod
    def replace_document_graph(
        self,
        source_name: str,
        extractions: List[Dict],
        structured_facts: Optional[List[Dict]] = None,
    ) -> None:
        """Replace one document's graph projection with fresh extraction output."""

    @abstractmethod
    def build_from_extractions(
        self,
        extractions: List[Dict],
        structured_facts: Optional[List[Dict]] = None,
    ) -> None:
        """Persist graph state from extraction results."""

    @abstractmethod
    def graph_summary(self) -> Dict[str, int]:
        """Return high-level graph counts."""

    @abstractmethod
    def document_has_graph(self, source_name: str) -> bool:
        """Whether a document already has persisted graph state."""

    @abstractmethod
    def get_document_graph(
        self,
        source_name: str,
        max_nodes: Optional[int] = None,
        max_edges: Optional[int] = None,
    ) -> nx.MultiDiGraph:
        """Return a visualization-friendly graph for one document."""

    @abstractmethod
    def get_document_node_details(
        self,
        source_name: str,
        limit: int = 20,
    ) -> List[Dict[str, str]]:
        """Return user-facing node details for one document."""

    def get_sources_graph(
        self,
        source_names: List[str],
        max_nodes: Optional[int] = None,
        max_edges: Optional[int] = None,
    ) -> nx.MultiDiGraph:
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support multi-document graph retrieval."
        )

    def get_query_neighborhood(
        self,
        source_names: List[str],
        query: str,
        query_entities: Optional[List[Dict]] = None,
        radius: int = 2,
        max_nodes: Optional[int] = None,
        max_edges: Optional[int] = None,
    ) -> Optional[Dict]:
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support query-time graph neighborhoods."
        )

    def queue_job(self, source_name: str, file_hash: str):
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support background graph jobs."
        )

    def list_jobs(self) -> List[Dict]:
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support listing graph jobs."
        )

    def next_queued_job(self) -> Optional[Dict]:
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support queued graph jobs."
        )

    def mark_job_running(self, job_id):
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support graph job state transitions."
        )

    def mark_job_complete(self, job_id):
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support graph job state transitions."
        )

    def mark_job_failed(self, job_id, error: str):
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support graph job state transitions."
        )

    def close(self) -> None:
        """Optional resource cleanup hook."""
