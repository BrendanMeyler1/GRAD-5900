import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.core.config import settings


class QueryResultCache:
    def __init__(self, base_dir: Optional[str] = None):
        artifacts_dir = Path(base_dir or settings.artifacts_dir)
        self.cache_dir = artifacts_dir / "query_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _make_key(self, payload: Dict[str, Any]) -> str:
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _path_for_key(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def make_pipeline_key(
        self,
        query: str,
        mode: str,
        indexed_docs: list,
        top_k: int,
        chunk_size: int,
        chunk_overlap: int,
        retrieval_backend: str = "",
        graph_backend: str = "",
        version: str = "v1",
    ) -> str:
        payload = {
            "type": "query_pipeline",
            "query": query.strip(),
            "mode": mode,
            "indexed_docs": sorted(indexed_docs),
            "top_k": top_k,
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
            "retrieval_backend": retrieval_backend,
            "graph_backend": graph_backend,
            "version": version,
        }
        return self._make_key(payload)

    def make_judge_key(
        self,
        question: str,
        mode: str,
        candidate_answer: str,
        retrieved_context: str,
        graph_context: str,
        ideal_answer: str,
        version: str = "v1",
    ) -> str:
        payload = {
            "type": "judge_result",
            "question": question.strip(),
            "mode": mode,
            "candidate_answer": candidate_answer,
            "retrieved_context": retrieved_context,
            "graph_context": graph_context,
            "ideal_answer": ideal_answer,
            "version": version,
        }
        return self._make_key(payload)

    def load(self, key: str) -> Optional[Dict[str, Any]]:
        path = self._path_for_key(key)
        if not path.exists():
            return None

        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)

    def save(self, key: str, payload: Dict[str, Any]) -> str:
        path = self._path_for_key(key)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        return str(path)

    def get_cache_stats(self) -> Dict[str, Any]:
        files = list(self.cache_dir.glob("*.json"))
        return {
            "cache_dir": str(self.cache_dir),
            "num_entries": len(files),
        }

    def list_entries(self) -> List[Dict[str, Any]]:
        entries = []
        for path in sorted(self.cache_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                with open(path, "r", encoding="utf-8-sig") as f:
                    payload = json.load(f)
            except Exception:
                payload = {}

            entry_type = "pipeline" if "selected_results" in payload else "judge"
            entries.append(
                {
                    "key": path.stem,
                    "path": str(path),
                    "size_bytes": path.stat().st_size,
                    "entry_type": entry_type,
                    "query": payload.get("query") or payload.get("question") or "",
                    "mode": payload.get("mode", ""),
                }
            )

        return entries

    def delete(self, key: str) -> bool:
        path = self._path_for_key(key)
        if not path.exists():
            return False

        path.unlink()
        return True
