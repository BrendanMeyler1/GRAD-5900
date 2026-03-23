import pytest

from backend.graph.neo4j_store import Neo4jGraphStore


class FakeResult:
    def __init__(self, records=None, single_record=None):
        self.records = records or []
        self.single_record = single_record

    def single(self):
        if self.single_record is not None:
            return self.single_record
        if self.records:
            return self.records[0]
        return None

    def __iter__(self):
        return iter(self.records)


class FakeSession:
    def __init__(self, driver):
        self.driver = driver

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def run(self, query, **params):
        normalized_query = " ".join(query.split())
        self.driver.calls.append((normalized_query, params))

        if "RETURN count(n) AS num_nodes" in normalized_query:
            return FakeResult(single_record={"num_nodes": 3})

        if "RETURN count(r) AS num_edges" in normalized_query:
            return FakeResult(single_record={"num_edges": 2})

        if "RETURN 1 AS found" in normalized_query:
            return FakeResult(single_record={"found": 1})

        if "RETURN properties(j) AS job_props" in normalized_query:
            return FakeResult(
                records=[
                    {
                        "job_props": {
                            "job_id": "job-1",
                            "source_name": "tesla.pdf",
                            "file_hash": "hash-123",
                            "status": "queued",
                            "created_at": "2026-03-23T00:00:00Z",
                        }
                    }
                ]
            )

        if "RETURN DISTINCT seed.node_id AS node_id" in normalized_query:
            return FakeResult(
                records=[
                    {
                        "node_id": "Organization::tesla",
                        "label": "Tesla",
                        "entity_type": "Organization",
                        "source_name": "tesla.pdf",
                    },
                    {
                        "node_id": "Metric::tesla.pdf::revenue::2024::1",
                        "label": "Revenue: $97,690 million",
                        "entity_type": "Metric",
                        "source_name": "tesla.pdf",
                    },
                ]
            )

        if "RETURN DISTINCT n.node_id AS node_id" in normalized_query:
            return FakeResult(
                records=[
                    {
                        "node_id": "Organization::tesla",
                        "label": "Tesla",
                        "entity_type": "Organization",
                        "source_name": "tesla.pdf",
                    },
                    {
                        "node_id": "Metric::tesla.pdf::revenue::2024::1",
                        "label": "Revenue: $97,690 million",
                        "entity_type": "Metric",
                        "source_name": "tesla.pdf",
                    },
                    {
                        "node_id": "Period::2024",
                        "label": "2024",
                        "entity_type": "Period",
                        "source_name": "",
                    },
                ]
            )

        if "RETURN DISTINCT source.node_id AS source_node_id" in normalized_query:
            return FakeResult(
                records=[
                    {
                        "source_node_id": "Organization::tesla",
                        "target_node_id": "Metric::tesla.pdf::revenue::2024::1",
                        "relationship_type": "REPORTS_METRIC",
                        "source_doc": "tesla.pdf",
                        "chunk_id": "fact-1",
                    },
                    {
                        "source_node_id": "Metric::tesla.pdf::revenue::2024::1",
                        "target_node_id": "Period::2024",
                        "relationship_type": "FOR_PERIOD",
                        "source_doc": "tesla.pdf",
                        "chunk_id": "fact-1",
                    },
                ]
            )

        if (
            "RETURN e.node_id AS node_id, e.label AS label, e.entity_type AS entity_type" in normalized_query
            and "LIMIT $limit" not in normalized_query
        ):
            return FakeResult(
                records=[
                    {
                        "node_id": "Asset::automotive sales",
                        "label": "Automotive sales",
                        "entity_type": "Asset",
                    },
                    {
                        "node_id": "Organization::tesla",
                        "label": "Tesla",
                        "entity_type": "Organization",
                    },
                ]
            )

        if "RETURN source.node_id AS source_node_id" in normalized_query:
            return FakeResult(
                records=[
                    {
                        "source_node_id": "Organization::tesla",
                        "target_node_id": "Asset::automotive sales",
                        "relationship_type": "GENERATES",
                        "source_doc": "tesla.pdf",
                        "chunk_id": "chunk-1",
                    }
                ]
            )

        if "LIMIT $limit" in normalized_query:
            return FakeResult(
                records=[
                    {
                        "node_id": "Asset::automotive sales",
                        "label": "Automotive sales",
                        "entity_type": "Asset",
                    },
                    {
                        "node_id": "Organization::tesla",
                        "label": "Tesla",
                        "entity_type": "Organization",
                    },
                ]
            )

        return FakeResult()


class FakeDriver:
    def __init__(self):
        self.calls = []
        self.closed = False

    def session(self, database=None):
        self.calls.append(("SESSION", {"database": database}))
        return FakeSession(self)

    def close(self):
        self.closed = True


def test_neo4j_graph_store_is_optional_when_not_configured():
    store = Neo4jGraphStore(uri="", username="", password="", driver=None)

    assert store.is_configured() is False
    with pytest.raises(RuntimeError):
        store.graph_summary()


def test_neo4j_graph_store_scaffold_works_with_injected_driver():
    driver = FakeDriver()
    store = Neo4jGraphStore(
        uri="bolt://localhost:7687",
        username="neo4j",
        password="password",
        driver=driver,
    )

    summary = store.graph_summary()
    has_graph = store.document_has_graph("tesla.pdf")
    graph = store.get_document_graph("tesla.pdf", max_nodes=10, max_edges=10)
    details = store.get_document_node_details("tesla.pdf")
    job_id = store.queue_job("tesla.pdf", "hash-123")
    jobs = store.list_jobs()
    store.mark_job_running(job_id)
    store.mark_job_complete(job_id)
    store.mark_job_failed(job_id, "boom")
    store.close()

    assert store.is_configured() is True
    assert summary == {"num_nodes": 3, "num_edges": 2}
    assert has_graph is True
    assert graph.number_of_nodes() == 2
    assert graph.number_of_edges() == 1
    assert {detail["label"] for detail in details} == {"Tesla", "Automotive sales"}
    assert jobs[0]["job_id"] == "job-1"
    assert jobs[0]["error"] is None
    assert driver.closed is True
    assert any("CREATE CONSTRAINT document_source_name" in query for query, _ in driver.calls)
    assert any("CREATE (j:GraphBuildJob" in query for query, _ in driver.calls)
    assert any("created_at: toString(datetime())" in query for query, _ in driver.calls)
    assert any("j.started_at = toString(datetime())" in query for query, _ in driver.calls)
    assert any("j.finished_at = toString(datetime())" in query for query, _ in driver.calls)


def test_neo4j_graph_store_persists_structured_fact_nodes():
    driver = FakeDriver()
    store = Neo4jGraphStore(
        uri="bolt://localhost:7687",
        username="neo4j",
        password="password",
        driver=driver,
    )

    store.build_from_extractions(
        extractions=[],
        structured_facts=[
            {
                "fact_id": "fact-1",
                "source_name": "tesla.pdf",
                "metric_key": "revenue",
                "metric_label": "Revenue",
                "period": "2024",
                "value_text": "$97,690 million",
                "page_label": "Page 4",
                "section_index": 1,
            }
        ],
    )

    assert any("CREATE CONSTRAINT metric_metric_id" in query for query, _ in driver.calls)
    assert any("CREATE CONSTRAINT period_period_id" in query for query, _ in driver.calls)
    assert any("MERGE (m:Metric {metric_id: $metric_id})" in query for query, _ in driver.calls)
    assert any("MERGE (p:Period {period_id: $period_id})" in query for query, _ in driver.calls)

    store.close()


def test_neo4j_graph_store_query_neighborhood_returns_global_metric_period_graph():
    driver = FakeDriver()
    store = Neo4jGraphStore(
        uri="bolt://localhost:7687",
        username="neo4j",
        password="password",
        driver=driver,
    )

    neighborhood = store.get_query_neighborhood(
        source_names=["tesla.pdf"],
        query="Show Tesla revenue for 2024",
        query_entities=[{"name": "Tesla", "type": "Organization"}],
        radius=2,
        max_nodes=10,
        max_edges=10,
    )

    assert neighborhood is not None
    assert neighborhood["matched_node_ids"] == [
        "Organization::tesla",
        "Metric::tesla.pdf::revenue::2024::1",
    ]
    assert neighborhood["graph"].number_of_nodes() == 3
    assert neighborhood["graph"].number_of_edges() == 2
