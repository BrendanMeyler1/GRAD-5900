from backend.retrieval.bm25_store import BM25Store


def test_bm25_indexing_is_idempotent_for_chunk_ids():
    store = BM25Store()
    chunk = {
        "chunk_id": "doc::abc123::chunk_0",
        "source": "doc",
        "text": "revenue grew strongly",
    }

    store.index_chunks([chunk])
    store.index_chunks([chunk])

    results = store.search("revenue", top_k=5)

    assert len(results) == 1
    assert results[0]["chunk_id"] == chunk["chunk_id"]
