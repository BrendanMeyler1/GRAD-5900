import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.core.config import settings


class ArtifactCache:
    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = Path(base_dir or settings.artifacts_dir)
        self.docs_dir = self.base_dir / "documents"
        self.chunks_dir = self.base_dir / "chunks"
        self.graph_dir = self.base_dir / "graph"
        self.manifest_path = self.base_dir / "manifest.json"

        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        self.chunks_dir.mkdir(parents=True, exist_ok=True)
        self.graph_dir.mkdir(parents=True, exist_ok=True)

        # Issue #6 fix: in-memory manifest cache to avoid redundant disk reads
        self._manifest_cache: Optional[Dict[str, Any]] = None

        if not self.manifest_path.exists():
            self._write_json(
                self.manifest_path,
                {
                    "documents": {}
                },
            )

    def _read_json(self, path: Path, default: Any = None) -> Any:
        if not path.exists():
            return default
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

    def file_sha256(self, file_path: str) -> str:
        sha = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha.update(chunk)
        return sha.hexdigest()

    def get_manifest(self) -> Dict[str, Any]:
        if self._manifest_cache is not None:
            return self._manifest_cache
        self._manifest_cache = self._read_json(self.manifest_path, default={"documents": {}})
        return self._manifest_cache

    def update_manifest(self, manifest: Dict[str, Any]) -> None:
        self._manifest_cache = manifest
        self._write_json(self.manifest_path, manifest)

    def get_document_record(self, file_hash: str) -> Optional[Dict[str, Any]]:
        manifest = self.get_manifest()
        return manifest.get("documents", {}).get(file_hash)

    def upsert_document_record(
        self,
        file_hash: str,
        record: Dict[str, Any],
    ) -> None:
        manifest = self.get_manifest()
        manifest.setdefault("documents", {})
        manifest["documents"][file_hash] = record
        self.update_manifest(manifest)

    def update_document_fields(self, file_hash: str, **fields: Any) -> None:
        manifest = self.get_manifest()
        documents = manifest.setdefault("documents", {})
        if file_hash not in documents:
            return
        documents[file_hash].update(fields)
        self.update_manifest(manifest)

    def set_field_for_all_documents(self, field_name: str, value: Any) -> None:
        manifest = self.get_manifest()
        for record in manifest.setdefault("documents", {}).values():
            record[field_name] = value
        self.update_manifest(manifest)

    def chunk_artifact_path(self, file_hash: str) -> Path:
        return self.chunks_dir / f"{file_hash}.json"

    def graph_artifact_path(self, file_hash: str) -> Path:
        return self.graph_dir / f"{file_hash}.json"

    def save_chunks(self, file_hash: str, chunks: List[Dict[str, Any]]) -> str:
        path = self.chunk_artifact_path(file_hash)
        self._write_json(path, chunks)
        return str(path)

    def append_chunks(self, file_hash: str, chunks: List[Dict[str, Any]]) -> str:
        path = self.chunk_artifact_path(file_hash)
        existing = self._read_json(path, default=[])
        existing.extend(chunks)
        self._write_json(path, existing)
        return str(path)

    def load_chunks(self, file_hash: str) -> Optional[List[Dict[str, Any]]]:
        path = self.chunk_artifact_path(file_hash)
        return self._read_json(path, default=None)

    def save_graph_extractions(self, file_hash: str, extractions: List[Dict[str, Any]]) -> str:
        path = self.graph_artifact_path(file_hash)
        self._write_json(path, extractions)
        return str(path)

    def append_graph_extractions(self, file_hash: str, extractions: List[Dict[str, Any]]) -> str:
        path = self.graph_artifact_path(file_hash)
        existing = self._read_json(path, default=[])
        existing.extend(extractions)
        self._write_json(path, existing)
        return str(path)

    def load_graph_extractions(self, file_hash: str) -> Optional[List[Dict[str, Any]]]:
        path = self.graph_artifact_path(file_hash)
        return self._read_json(path, default=None)

    def list_indexed_documents(self) -> List[Dict[str, Any]]:
        manifest = self.get_manifest()
        docs = list(manifest.get("documents", {}).values())
        docs.sort(key=lambda x: x.get("source_name", ""))
        return docs

    def list_indexed_document_keys(self) -> List[str]:
        return [
            self.make_document_cache_key(record)
            for record in self.list_indexed_documents()
        ]

    def make_document_cache_key(self, record: Dict[str, Any]) -> str:
        source_name = record.get("source_name", "")
        file_hash = record.get("file_hash", "")
        return f"{source_name}::{file_hash}" if file_hash else source_name

    def list_document_records_for_source(self, source_name: str) -> List[Dict[str, Any]]:
        return [
            record
            for record in self.list_indexed_documents()
            if record.get("source_name") == source_name
        ]

    def delete_document_artifacts(self, file_hash: str) -> Dict[str, int]:
        removed = {
            "manifest_removed": 0,
            "chunk_artifact_removed": 0,
            "graph_artifact_removed": 0,
        }

        chunk_path = self.chunk_artifact_path(file_hash)
        graph_path = self.graph_artifact_path(file_hash)

        if chunk_path.exists():
            chunk_path.unlink()
            removed["chunk_artifact_removed"] = 1

        if graph_path.exists():
            graph_path.unlink()
            removed["graph_artifact_removed"] = 1

        manifest = self.get_manifest()
        documents = manifest.get("documents", {})
        if file_hash in documents:
            del documents[file_hash]
            removed["manifest_removed"] = 1
            self.update_manifest(manifest)

        return removed
