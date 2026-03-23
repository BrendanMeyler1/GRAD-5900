import networkx as nx

from backend.graph.preview_jobs import GraphPreviewJobManager
from backend.graph.query_preview import (
    build_persisted_query_preview,
    build_temporary_query_preview,
)


class FakeGraphStore:
    def __init__(self, graphs):
        self.graphs = graphs
        self.last_source_names = []

    def is_configured(self):
        return True

    def get_sources_graph(self, source_names, max_nodes=None, max_edges=None):
        self.last_source_names = list(source_names)
        graph = nx.MultiDiGraph()
        for source_name in source_names:
            if source_name in self.graphs:
                graph = nx.compose(graph, self.graphs[source_name].copy())
        return graph


class FakeLinker:
    def extract_query_entities(self, query):
        return [{"name": "Tesla", "type": "Organization"}]


def _make_graph():
    graph = nx.MultiDiGraph()
    graph.add_node(
        "Organization::tesla",
        label="Tesla",
        entity_type="Organization",
        sources=["tesla.pdf"],
    )
    graph.add_node(
        "Asset::automotive sales",
        label="Automotive sales",
        entity_type="Asset",
        sources=["tesla.pdf"],
    )
    graph.add_edge(
        "Organization::tesla",
        "Asset::automotive sales",
        relationship_type="GENERATES",
        source_doc="tesla.pdf",
        chunk_id="chunk-1",
    )
    return graph


def test_persisted_query_preview_uses_same_persisted_neighborhood_matching():
    preview = build_persisted_query_preview(
        graph_store=FakeGraphStore({"tesla.pdf": _make_graph()}),
        query="How is the company performing?",
        results=[{"source": "tesla.pdf", "text": "Tesla automotive sales", "chunk_id": "c1"}],
        query_graph_linker=FakeLinker(),
    )

    assert preview is not None
    assert preview["graph"].number_of_edges() == 1
    assert preview["highlighted_nodes"] == ["Organization::tesla"]
    assert "persisted graph neighborhood" in preview["caption"].lower()


def test_persisted_query_preview_returns_none_when_no_graphs_exist():
    preview = build_persisted_query_preview(
        graph_store=FakeGraphStore({}),
        query="How is Tesla performing?",
        results=[{"source": "tesla.pdf", "text": "Tesla automotive sales", "chunk_id": "c1"}],
    )

    assert preview is None


def test_persisted_query_preview_uses_all_relevant_result_sources():
    graph_store = FakeGraphStore(
        {
            "tesla-q1.pdf": _make_graph(),
            "tesla-q3.pdf": _make_graph(),
            "tesla-q4.pdf": _make_graph(),
            "tesla-10k.pdf": _make_graph(),
            "tesla-8k.pdf": _make_graph(),
        }
    )

    preview = build_persisted_query_preview(
        graph_store=graph_store,
        query="How is the company performing in 2024?",
        results=[
            {"source": "tesla-q1.pdf", "text": "Tesla q1", "chunk_id": "c1"},
            {"source": "tesla-q3.pdf", "text": "Tesla q3", "chunk_id": "c2"},
            {"source": "tesla-q4.pdf", "text": "Tesla q4", "chunk_id": "c3"},
            {"source": "tesla-10k.pdf", "text": "Tesla 10k", "chunk_id": "c4"},
            {"source": "tesla-8k.pdf", "text": "Tesla 8k", "chunk_id": "c5"},
        ],
        query_graph_linker=FakeLinker(),
    )

    assert preview is not None
    assert graph_store.last_source_names == [
        "tesla-q1.pdf",
        "tesla-q3.pdf",
        "tesla-q4.pdf",
        "tesla-10k.pdf",
        "tesla-8k.pdf",
    ]


class FakeExtractor:
    def should_extract_chunk(self, chunk):
        return True

    def extract_from_chunk(self, chunk):
        return {
            "chunk_id": chunk["chunk_id"],
            "source": chunk["source"],
            "entities": [{"name": "Tesla", "type": "Organization"}],
            "relationships": [],
        }


def test_temporary_query_preview_builds_from_retrieved_chunks():
    preview = build_temporary_query_preview(
        query="How is the company performing?",
        results=[{"source": "tesla.pdf", "text": "Tesla automotive sales", "chunk_id": "c1"}],
        graph_extractor=FakeExtractor(),
        query_graph_linker=FakeLinker(),
        top_k=1,
    )

    assert preview is not None
    assert preview["graph"].number_of_nodes() == 1
    assert preview["highlighted_nodes"] == ["Organization::tesla"]
    assert "temporary query-local graph" in preview["caption"].lower()


def test_graph_preview_job_manager_reports_ready_status():
    manager = GraphPreviewJobManager(max_workers=1)

    manager.submit(
        request_id="req-1",
        query="How is Tesla performing?",
        results=[{"source": "tesla.pdf", "text": "Tesla automotive sales", "chunk_id": "c1"}],
        mode="vector",
        graph_store=FakeGraphStore({"tesla.pdf": _make_graph()}),
        graph_extractor=FakeExtractor(),
        query_graph_linker=FakeLinker(),
        graph_context_origin="persisted_graph",
    )
    snapshot = manager.wait_for("req-1", timeout=2.0)

    assert snapshot["status"] == "ready"
    assert snapshot["result"]["graph"].number_of_edges() == 1


def test_graph_preview_job_manager_falls_back_to_temporary_graph_for_graphrag():
    manager = GraphPreviewJobManager(max_workers=1)

    manager.submit(
        request_id="req-2",
        query="How is the company performing?",
        results=[{"source": "tesla.pdf", "text": "Tesla automotive sales", "chunk_id": "c1"}],
        mode="graphrag",
        graph_store=FakeGraphStore({}),
        graph_extractor=FakeExtractor(),
        query_graph_linker=FakeLinker(),
        graph_context_origin="query_local",
    )
    snapshot = manager.wait_for("req-2", timeout=2.0)

    assert snapshot["status"] == "ready"
    assert "temporary query-local graph" in snapshot["result"]["caption"].lower()


def test_graph_preview_job_manager_builds_temporary_graph_for_file_search_when_needed():
    manager = GraphPreviewJobManager(max_workers=1)

    manager.submit(
        request_id="req-file-search",
        query="How is the company performing?",
        results=[{"source": "tesla.pdf", "text": "Tesla automotive sales", "chunk_id": "c1"}],
        mode="file_search",
        graph_store=FakeGraphStore({}),
        graph_extractor=FakeExtractor(),
        query_graph_linker=FakeLinker(),
        graph_context_origin="none",
    )
    snapshot = manager.wait_for("req-file-search", timeout=2.0)

    assert snapshot["status"] == "ready"
    assert "temporary query-local graph" in snapshot["result"]["caption"].lower()


def test_graph_preview_job_manager_skips_persisted_preview_when_answer_used_query_local():
    manager = GraphPreviewJobManager(max_workers=1)

    manager.submit(
        request_id="req-3",
        query="How is the company performing?",
        results=[{"source": "tesla.pdf", "text": "Tesla automotive sales", "chunk_id": "c1"}],
        mode="graphrag",
        graph_store=FakeGraphStore({"tesla.pdf": _make_graph()}),
        graph_extractor=FakeExtractor(),
        query_graph_linker=FakeLinker(),
        graph_context_origin="query_local",
    )
    snapshot = manager.wait_for("req-3", timeout=2.0)

    assert snapshot["status"] == "ready"
    assert "temporary query-local graph" in snapshot["result"]["caption"].lower()
