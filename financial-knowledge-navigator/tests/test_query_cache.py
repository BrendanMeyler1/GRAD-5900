import shutil
from pathlib import Path
from uuid import uuid4

from backend.core.query_cache import QueryResultCache


def test_pipeline_cache_key_changes_when_document_version_changes():
    base_dir = Path("data") / f"test_query_cache_{uuid4().hex}"
    cache = QueryResultCache(base_dir=str(base_dir))

    try:
        key_a = cache.make_pipeline_key(
            query="What changed?",
            mode="hybrid",
            indexed_docs=["report.pdf::hash_a"],
            top_k=5,
            chunk_size=700,
            chunk_overlap=120,
            retrieval_backend="local_vector",
            graph_backend="sqlite",
        )
        key_b = cache.make_pipeline_key(
            query="What changed?",
            mode="hybrid",
            indexed_docs=["report.pdf::hash_b"],
            top_k=5,
            chunk_size=700,
            chunk_overlap=120,
            retrieval_backend="local_vector",
            graph_backend="sqlite",
        )

        assert key_a != key_b
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)


def test_pipeline_cache_key_changes_when_backends_change():
    base_dir = Path("data") / f"test_query_cache_backend_{uuid4().hex}"
    cache = QueryResultCache(base_dir=str(base_dir))

    try:
        file_search_key = cache.make_pipeline_key(
            query="What changed?",
            mode="graphrag",
            indexed_docs=["report.pdf::hash_a"],
            top_k=5,
            chunk_size=700,
            chunk_overlap=120,
            retrieval_backend="openai_file_search",
            graph_backend="sqlite",
        )
        local_key = cache.make_pipeline_key(
            query="What changed?",
            mode="graphrag",
            indexed_docs=["report.pdf::hash_a"],
            top_k=5,
            chunk_size=700,
            chunk_overlap=120,
            retrieval_backend="local_vector",
            graph_backend="neo4j",
        )

        assert file_search_key != local_key
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)


def test_query_cache_entries_can_be_listed_and_deleted():
    base_dir = Path("data") / f"test_query_cache_entries_{uuid4().hex}"
    cache = QueryResultCache(base_dir=str(base_dir))

    try:
        cache.save(
            "pipeline-entry",
            {
                "query": "What changed?",
                "mode": "hybrid",
                "selected_results": [],
            },
        )
        cache.save(
            "judge-entry",
            {
                "question": "How did revenue change?",
                "mode": "vector",
                "scores": {},
            },
        )

        entries = cache.list_entries()

        assert {entry["key"] for entry in entries} == {"pipeline-entry", "judge-entry"}
        assert cache.delete("pipeline-entry") is True
        assert {entry["key"] for entry in cache.list_entries()} == {"judge-entry"}
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)
