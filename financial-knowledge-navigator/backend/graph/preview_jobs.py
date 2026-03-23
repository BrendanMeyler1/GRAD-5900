import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Dict, List, Optional

from backend.graph.query_preview import (
    build_persisted_query_preview,
    build_temporary_query_preview,
)


class GraphPreviewJobManager:
    def __init__(self, max_workers: int = 2, max_completed_jobs: int = 24):
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="graph-preview",
        )
        self._lock = threading.Lock()
        self._jobs: Dict[str, Dict] = {}
        self._max_completed_jobs = max_completed_jobs

    def _snapshot_unlocked(self, record: Dict) -> Dict:
        return {
            "request_id": record["request_id"],
            "status": record["status"],
            "detail": record.get("detail", ""),
            "error": record.get("error"),
            "submitted_at": record.get("submitted_at"),
            "updated_at": record.get("updated_at"),
            "completed_at": record.get("completed_at"),
            "result": record.get("result"),
        }

    def _update_job(self, request_id: str, **updates) -> None:
        with self._lock:
            record = self._jobs.get(request_id)
            if record is None:
                return
            record.update(updates)
            record["updated_at"] = time.time()

    def _prune_completed_jobs(self) -> None:
        completed_ids = [
            request_id
            for request_id, record in self._jobs.items()
            if record.get("status") in {"ready", "empty", "failed"}
        ]
        if len(completed_ids) <= self._max_completed_jobs:
            return

        completed_ids.sort(
            key=lambda request_id: self._jobs[request_id].get("completed_at", 0.0)
        )
        for request_id in completed_ids[:-self._max_completed_jobs]:
            self._jobs.pop(request_id, None)

    def _run_job(
        self,
        request_id: str,
        query: str,
        results: List[Dict],
        mode: str,
        graph_store,
        graph_extractor,
        query_graph_linker,
        graph_context_origin,
    ):
        try:
            preview = None
            prefer_persisted = graph_context_origin == "persisted_graph"
            prefer_query_local = graph_context_origin == "query_local"

            if graph_store is not None and not prefer_query_local:
                self._update_job(
                    request_id,
                    status="running",
                    detail=(
                        "Loading the persisted graph neighborhood used for this answer..."
                        if prefer_persisted
                        else "Checking persisted graph connections for the retrieved documents..."
                    ),
                )
                preview = build_persisted_query_preview(
                    graph_store=graph_store,
                    query=query,
                    results=results,
                    query_graph_linker=query_graph_linker,
                )

            if preview is None and graph_extractor is not None:
                self._update_job(
                    request_id,
                    status="running",
                    detail=(
                        "Loading the temporary graph neighborhood used for this answer..."
                        if prefer_query_local
                        else "Building a lightweight prompt graph from the retrieved chunks..."
                    ),
                )
                preview = build_temporary_query_preview(
                    query=query,
                    results=results,
                    graph_extractor=graph_extractor,
                    query_graph_linker=query_graph_linker,
                )

            if preview is None:
                self._update_job(
                    request_id,
                    status="empty",
                    detail="No graph connections are available for the latest prompt yet.",
                    completed_at=time.time(),
                    result=None,
                )
                return None

            self._update_job(
                request_id,
                status="ready",
                detail=preview.get("caption", "Graph preview ready."),
                completed_at=time.time(),
                result=preview,
            )
            return preview
        except Exception as exc:
            self._update_job(
                request_id,
                status="failed",
                detail=f"Graph preview failed: {exc}",
                error=str(exc),
                completed_at=time.time(),
                result=None,
            )
            raise

    def submit(
        self,
        request_id: str,
        query: str,
        results: List[Dict],
        mode: str,
        graph_store=None,
        graph_extractor=None,
        query_graph_linker=None,
        graph_context_origin: str = "none",
    ) -> Dict:
        with self._lock:
            existing = self._jobs.get(request_id)
            if existing is not None:
                return self._snapshot_unlocked(existing)

            record = {
                "request_id": request_id,
                "status": "queued",
                "detail": "Graph preview queued.",
                "error": None,
                "submitted_at": time.time(),
                "updated_at": time.time(),
                "completed_at": None,
                "result": None,
                "future": None,
            }
            self._jobs[request_id] = record
            future = self._executor.submit(
                self._run_job,
                request_id,
                query,
                results,
                mode,
                graph_store,
                graph_extractor,
                query_graph_linker,
                graph_context_origin,
            )
            record["future"] = future
            self._prune_completed_jobs()
            return self._snapshot_unlocked(record)

    def get_snapshot(self, request_id: Optional[str]) -> Optional[Dict]:
        if not request_id:
            return None
        with self._lock:
            record = self._jobs.get(request_id)
            if record is None:
                return None
            return self._snapshot_unlocked(record)

    def wait_for(self, request_id: str, timeout: float = 5.0) -> Optional[Dict]:
        with self._lock:
            record = self._jobs.get(request_id)
            future: Optional[Future] = record.get("future") if record else None
        if future is not None:
            future.result(timeout=timeout)
        return self.get_snapshot(request_id)
