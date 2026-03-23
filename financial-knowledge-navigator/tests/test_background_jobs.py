from backend.graph.background_jobs import BackgroundGraphJobRunner


class FakeGraphStore:
    def __init__(self, job_id):
        self.job = {"job_id": job_id, "source_name": "tesla.pdf", "file_hash": "hash-123"}
        self.status_changes = []
        self.replaced = None

    def next_queued_job(self):
        job, self.job = self.job, None
        return job

    def mark_job_running(self, job_id):
        self.status_changes.append(("running", job_id))

    def mark_job_complete(self, job_id):
        self.status_changes.append(("completed", job_id))

    def mark_job_failed(self, job_id, error):
        self.status_changes.append(("failed", job_id, error))

    def replace_document_graph(self, source_name, extractions, structured_facts=None):
        self.replaced = (source_name, extractions, structured_facts or [])


class FakeRetriever:
    def __init__(self):
        self.calls = []

    def search(self, query: str, top_k: int = 5, file_hash=None):
        self.calls.append((query, top_k, file_hash))
        return [
            {
                "source": "tesla.pdf",
                "text": "Revenue increased and cash flow improved.",
                "chunk_id": "chunk-1",
                "file_hash": file_hash,
            }
        ]


class FakeExtractor:
    def should_extract_chunk(self, chunk):
        return True

    def extract_from_chunk(self, chunk):
        return {
            "chunk_id": chunk["chunk_id"],
            "source": chunk["source"],
            "entities": [{"name": "Tesla", "type": "Organization"}],
            "relationships": [],
        }


class FakeFactsStore:
    def list_document_facts(self, file_hash=None, limit=20, source_name=None):
        return [
            {
                "fact_id": "fact-1",
                "file_hash": file_hash,
                "source_name": "tesla.pdf",
                "metric_key": "revenue",
                "metric_label": "Revenue",
                "period": "2024",
                "value_text": "$97,690 million",
                "section_index": 1,
            }
        ]


def test_background_graph_jobs_only_use_shared_contract_with_string_job_ids():
    graph_store = FakeGraphStore(job_id="job-1")
    retriever = FakeRetriever()
    runner = BackgroundGraphJobRunner(
        graph_store=graph_store,
        retrieval_store=retriever,
        graph_extractor=FakeExtractor(),
        facts_store=FakeFactsStore(),
    )

    processed = runner.process_next_job()

    assert processed is True
    assert graph_store.status_changes == [("running", "job-1"), ("completed", "job-1")]
    assert graph_store.replaced[0] == "tesla.pdf"
    assert len(graph_store.replaced[1]) == 1
    assert len(graph_store.replaced[2]) == 1
    assert graph_store.replaced[2][0]["metric_key"] == "revenue"
    assert all(call[2] == "hash-123" for call in retriever.calls)
