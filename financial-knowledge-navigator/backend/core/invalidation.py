import shutil
from pathlib import Path
from typing import Dict

from backend.core.config import settings


class CacheInvalidationManager:
    def __init__(self):
        self.artifacts_dir = Path(settings.artifacts_dir)
        self.query_cache_dir = self.artifacts_dir / "query_cache"
        self.chunks_dir = self.artifacts_dir / "chunks"
        self.graph_dir = self.artifacts_dir / "graph"
        self.documents_dir = self.artifacts_dir / "documents"
        self.manifest_path = self.artifacts_dir / "manifest.json"

        self.eval_results_dir = Path("data/eval_results")
        self.reports_dir = Path("data/reports")
        self.uploads_dir = Path("data/uploads")
        self.qdrant_dir = Path(settings.qdrant_path)

    def _safe_remove_dir_contents(self, path: Path) -> int:
        """
        Remove all contents inside a directory but keep the directory itself.
        Returns number of removed items.
        """
        removed = 0
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            return removed

        for item in path.iterdir():
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)
            removed += 1

        path.mkdir(parents=True, exist_ok=True)
        return removed

    def _safe_remove_matching_files(self, path: Path, pattern: str) -> int:
        removed = 0
        path.mkdir(parents=True, exist_ok=True)
        for file_path in path.glob(pattern):
            if file_path.is_file():
                file_path.unlink(missing_ok=True)
                removed += 1
        return removed

    def clear_query_cache(self) -> Dict[str, int]:
        removed = self._safe_remove_matching_files(self.query_cache_dir, "*.json")
        return {"query_cache_removed": removed}

    def clear_artifact_cache(self) -> Dict[str, int]:
        removed_chunks = self._safe_remove_matching_files(self.chunks_dir, "*.json")
        removed_graph = self._safe_remove_matching_files(self.graph_dir, "*.json")
        removed_docs = self._safe_remove_matching_files(self.documents_dir, "*.json")

        if self.manifest_path.exists():
            self.manifest_path.unlink(missing_ok=True)

        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text('{\n  "documents": {}\n}\n', encoding="utf-8")

        return {
            "chunk_artifacts_removed": removed_chunks,
            "graph_artifacts_removed": removed_graph,
            "document_artifacts_removed": removed_docs,
            "manifest_reset": 1,
        }

    def clear_eval_results(self) -> Dict[str, int]:
        removed = self._safe_remove_matching_files(self.eval_results_dir, "evaluation_*.json")
        return {"eval_results_removed": removed}

    def clear_reports(self) -> Dict[str, int]:
        removed_md = self._safe_remove_matching_files(self.reports_dir, "*.md")
        removed_csv = self._safe_remove_matching_files(self.reports_dir, "*.csv")
        removed_json = self._safe_remove_matching_files(self.reports_dir, "*.json")
        return {
            "report_md_removed": removed_md,
            "report_csv_removed": removed_csv,
            "report_json_removed": removed_json,
        }

    def clear_uploads(self) -> Dict[str, int]:
        removed = self._safe_remove_dir_contents(self.uploads_dir)
        return {"uploads_removed": removed}

    def clear_qdrant(self) -> Dict[str, int]:
        if self.qdrant_dir.exists():
            shutil.rmtree(self.qdrant_dir, ignore_errors=True)
        self.qdrant_dir.mkdir(parents=True, exist_ok=True)
        return {"qdrant_reset": 1}

    def full_reset(self) -> Dict[str, int]:
        results = {}
        results.update(self.clear_query_cache())
        results.update(self.clear_artifact_cache())
        results.update(self.clear_eval_results())
        results.update(self.clear_reports())
        results.update(self.clear_uploads())
        results.update(self.clear_qdrant())
        return results
