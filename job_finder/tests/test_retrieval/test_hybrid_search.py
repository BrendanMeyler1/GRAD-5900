"""Tests for retrieval.embeddings, vector_store, and hybrid_search."""

from retrieval.embeddings import TextEmbedder
from retrieval.vector_store import VectorStore
from retrieval.hybrid_search import HybridSearch


def _documents():
    return [
        {
            "id": "backend_role",
            "text": (
                "Senior backend engineer with Python, FastAPI, AWS, and distributed "
                "systems experience."
            ),
            "metadata": {"kind": "job", "ats_type": "greenhouse"},
        },
        {
            "id": "frontend_role",
            "text": (
                "Frontend engineer using React, CSS, UX design, and TypeScript for "
                "web applications."
            ),
            "metadata": {"kind": "job", "ats_type": "lever"},
        },
        {
            "id": "company_profile",
            "text": (
                "Acme Corp is a fintech company focused on payment infrastructure and "
                "cloud systems."
            ),
            "metadata": {"kind": "company", "ats_type": "greenhouse"},
        },
    ]


def test_vector_store_query_returns_best_match():
    embedder = TextEmbedder(backend="hash")
    store = VectorStore(embedder=embedder, use_chroma=False)
    store.upsert(_documents())

    results = store.query("python backend distributed systems", k=2)

    assert len(results) == 2
    assert results[0]["id"] == "backend_role"
    assert 0.0 <= results[0]["score"] <= 1.0


def test_hybrid_search_prefers_relevant_document():
    embedder = TextEmbedder(backend="hash")
    store = VectorStore(embedder=embedder, use_chroma=False)
    search = HybridSearch(vector_store=store, vector_weight=0.6)
    search.index_documents(_documents())

    results = search.search("react frontend ux", k=2)

    assert len(results) == 2
    assert results[0]["id"] == "frontend_role"
    assert results[0]["score"] >= results[1]["score"]


def test_hybrid_search_where_filter():
    embedder = TextEmbedder(backend="hash")
    store = VectorStore(embedder=embedder, use_chroma=False)
    search = HybridSearch(vector_store=store, vector_weight=0.5)
    search.index_documents(_documents())

    results = search.search(
        "fintech cloud payments",
        k=5,
        where={"kind": "company"},
    )

    assert len(results) == 1
    assert results[0]["id"] == "company_profile"
    assert results[0]["metadata"]["kind"] == "company"
