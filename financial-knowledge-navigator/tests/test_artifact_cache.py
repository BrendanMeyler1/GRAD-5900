import shutil
from pathlib import Path
from uuid import uuid4
import json

from backend.core.cache import ArtifactCache


def test_append_chunks_and_graph_extractions():
    temp_root = Path(__file__).resolve().parents[1] / ".tmp_test_cache"
    temp_root.mkdir(exist_ok=True)
    cache_dir = temp_root / uuid4().hex

    try:
        cache = ArtifactCache(base_dir=str(cache_dir))

        cache.save_chunks("doc-hash", [])
        cache.append_chunks("doc-hash", [{"chunk_id": "c1", "source": "a", "text": "one"}])
        cache.append_chunks("doc-hash", [{"chunk_id": "c2", "source": "a", "text": "two"}])

        cache.save_graph_extractions("doc-hash", [])
        cache.append_graph_extractions(
            "doc-hash",
            [{"chunk_id": "c1", "source": "a", "entities": [], "relationships": []}],
        )
        cache.append_graph_extractions(
            "doc-hash",
            [{"chunk_id": "c2", "source": "a", "entities": [], "relationships": []}],
        )

        assert [chunk["chunk_id"] for chunk in cache.load_chunks("doc-hash")] == ["c1", "c2"]
        assert [item["chunk_id"] for item in cache.load_graph_extractions("doc-hash")] == ["c1", "c2"]
    finally:
        shutil.rmtree(cache_dir, ignore_errors=True)


def test_artifact_cache_reads_manifest_with_utf8_bom():
    temp_root = Path(__file__).resolve().parents[1] / ".tmp_test_cache"
    temp_root.mkdir(exist_ok=True)
    cache_dir = temp_root / uuid4().hex

    try:
        cache = ArtifactCache(base_dir=str(cache_dir))
        manifest_path = cache_dir / "manifest.json"
        with open(manifest_path, "w", encoding="utf-8-sig") as f:
            json.dump(
                {
                    "documents": {
                        "hash-1": {
                            "file_hash": "hash-1",
                            "source_name": "tesla.pdf",
                        }
                    }
                },
                f,
            )

        cache._manifest_cache = None
        docs = cache.list_indexed_documents()

        assert docs == [{"file_hash": "hash-1", "source_name": "tesla.pdf"}]
    finally:
        shutil.rmtree(cache_dir, ignore_errors=True)
