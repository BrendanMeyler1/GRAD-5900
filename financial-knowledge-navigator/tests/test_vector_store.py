from types import SimpleNamespace

from backend.retrieval import vector_store as vector_store_module


class FakeEmbeddingClient:
    def __init__(self):
        self.calls = []
        self.embeddings = self

    def create(self, model, input):
        self.calls.append(list(input))
        return SimpleNamespace(
            data=[
                SimpleNamespace(embedding=[float(idx), float(idx) + 0.5])
                for idx, _ in enumerate(input)
            ]
        )


class FakeQdrant:
    def __init__(self):
        self.upsert_calls = []
        self.collections_calls = 0
        self.query_points_calls = []

    def upsert(self, collection_name, points):
        self.upsert_calls.append(
            {
                "collection_name": collection_name,
                "points": points,
            }
        )

    def get_collections(self):
        self.collections_calls += 1
        return SimpleNamespace(collections=[])

    def create_collection(self, collection_name, vectors_config):
        return None

    def scroll(self, **kwargs):
        return [], None

    def query_points(self, collection_name, query, limit, query_filter=None):
        self.query_points_calls.append(
            {
                "collection_name": collection_name,
                "query": query,
                "limit": limit,
                "query_filter": query_filter,
            }
        )
        point = SimpleNamespace(
            score=0.9,
            payload={
                "chunk_id": "chunk-1",
                "source": "doc.pdf",
                "text": "revenue rose",
                "file_hash": "hash-123",
            },
        )
        return SimpleNamespace(points=[point])


def test_index_chunks_upserts_in_small_batches(monkeypatch):
    monkeypatch.setattr(vector_store_module, "EMBED_BATCH_SIZE", 2)

    store = vector_store_module.VectorStore.__new__(vector_store_module.VectorStore)
    store.client = FakeEmbeddingClient()
    store.qdrant = FakeQdrant()
    store.collection_name = "test_collection"
    store.indexed_chunk_ids = set()

    chunks = [
        {"chunk_id": f"chunk-{idx}", "source": "doc.pdf", "text": f"chunk text {idx}"}
        for idx in range(5)
    ]

    indexed_count = vector_store_module.VectorStore.index_chunks(store, chunks)

    assert indexed_count == 5
    assert len(store.client.calls) == 3
    assert [len(call) for call in store.client.calls] == [2, 2, 1]
    assert len(store.qdrant.upsert_calls) == 3
    assert [len(call["points"]) for call in store.qdrant.upsert_calls] == [2, 2, 1]
    assert store.indexed_chunk_ids == {chunk["chunk_id"] for chunk in chunks}


def test_ensure_open_recreates_closed_qdrant(monkeypatch):
    created_clients = []

    class ClosedQdrant(FakeQdrant):
        def get_collections(self):
            raise RuntimeError("QdrantLocal instance is closed. Please create a new instance.")

    class ReopenedQdrant(FakeQdrant):
        pass

    def fake_qdrant_client(path):
        client = ReopenedQdrant()
        created_clients.append((path, client))
        return client

    monkeypatch.setattr(vector_store_module, "QdrantClient", fake_qdrant_client)

    store = vector_store_module.VectorStore.__new__(vector_store_module.VectorStore)
    store.client = FakeEmbeddingClient()
    store.qdrant = ClosedQdrant()
    store.collection_name = "test_collection"
    store.vector_size = 1536
    store.indexed_chunk_ids = {"old-id"}

    store.ensure_open()

    assert len(created_clients) == 1
    assert isinstance(store.qdrant, ReopenedQdrant)
    assert store.indexed_chunk_ids == set()


def test_search_supports_file_hash_filter():
    store = vector_store_module.VectorStore.__new__(vector_store_module.VectorStore)
    store.client = FakeEmbeddingClient()
    store.qdrant = FakeQdrant()
    store.collection_name = "test_collection"
    store.vector_size = 1536
    store.indexed_chunk_ids = set()

    results = vector_store_module.VectorStore.search(
        store,
        query="revenue",
        top_k=3,
        file_hash="hash-123",
    )

    assert results[0]["file_hash"] == "hash-123"
    assert store.qdrant.query_points_calls[0]["query_filter"] is not None
