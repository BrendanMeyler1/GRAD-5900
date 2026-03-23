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
        )
        key_b = cache.make_pipeline_key(
            query="What changed?",
            mode="hybrid",
            indexed_docs=["report.pdf::hash_b"],
            top_k=5,
            chunk_size=700,
            chunk_overlap=120,
        )

        assert key_a != key_b
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)
