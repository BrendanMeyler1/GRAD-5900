import threading
from typing import List, Optional

from backend.graph.base import GraphStore
from backend.retrieval.base import Retriever


class BackgroundGraphJobRunner:
    def __init__(
        self,
        graph_store: GraphStore,
        retrieval_store: Retriever,
        graph_extractor,
        facts_store=None,
    ):
        self.graph_store = graph_store
        self.retrieval_store = retrieval_store
        self.graph_extractor = graph_extractor
        self.facts_store = facts_store
        self._worker_lock = threading.Lock()
        self._worker = None

    def _collect_document_chunks(self, file_hash: str) -> List[dict]:
        queries = [
            "revenue income cash flow debt assets liabilities",
            "risks markets products operations regulation",
            "guidance outlook margins spending capex liquidity",
        ]
        deduped = {}
        for query in queries:
            for result in self.retrieval_store.search(query=query, top_k=8, file_hash=file_hash):
                deduped.setdefault((result["source"], result["text"]), result)
        return list(deduped.values())

    def _collect_document_facts(self, file_hash: str) -> List[dict]:
        if self.facts_store is None:
            return []
        try:
            return self.facts_store.list_document_facts(file_hash=file_hash, limit=200)
        except Exception:
            return []

    def process_next_job(self) -> bool:
        job = self.graph_store.next_queued_job()
        if not job:
            return False

        job_id = job["job_id"]
        self.graph_store.mark_job_running(job_id)

        try:
            retrieved_chunks = self._collect_document_chunks(job["file_hash"])
            structured_facts = self._collect_document_facts(job["file_hash"])
            extractions = []
            for chunk in retrieved_chunks:
                if not self.graph_extractor.should_extract_chunk(chunk):
                    continue
                extractions.append(self.graph_extractor.extract_from_chunk(chunk))

            self.graph_store.replace_document_graph(
                job["source_name"],
                extractions,
                structured_facts=structured_facts,
            )
            self.graph_store.mark_job_complete(job_id)
            return True
        except Exception as exc:
            self.graph_store.mark_job_failed(job_id, str(exc))
            return False

    def start_background_worker(self) -> bool:
        with self._worker_lock:
            if self._worker and self._worker.is_alive():
                return False

            def run():
                while self.process_next_job():
                    pass

            self._worker = threading.Thread(target=run, daemon=True)
            self._worker.start()
            return True
