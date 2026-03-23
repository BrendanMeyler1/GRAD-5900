import re
import uuid
from typing import Dict, List, Optional

import networkx as nx

from backend.core.config import settings
from backend.graph.base import GraphStore

try:  # pragma: no cover - import-path behavior depends on local environment
    from neo4j import GraphDatabase
except ImportError:  # pragma: no cover - exercised in environments without neo4j installed
    GraphDatabase = None


def _record_get(record, key: str, default=None):
    if record is None:
        return default
    if isinstance(record, dict):
        return record.get(key, default)
    try:
        return record.get(key, default)
    except AttributeError:
        try:
            return record[key]
        except Exception:
            return default


class Neo4jGraphStore(GraphStore):
    """
    Scaffold for a Neo4j-backed persistent graph store.

    This adapter is intentionally light-touch in Phase 1: it mirrors the
    document-graph methods already used by the app while keeping Neo4j
    optional until the runtime is explicitly migrated to it.
    """

    backend_name = "neo4j"
    _QUERY_STOPWORDS = {
        "the", "and", "for", "with", "from", "that", "this", "what", "which",
        "show", "across", "into", "their", "about", "then", "plain", "english",
        "among", "between", "using",
    }

    def __init__(
        self,
        uri: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        database: Optional[str] = None,
        driver=None,
    ):
        self.uri = uri if uri is not None else settings.neo4j_uri
        self.username = username if username is not None else settings.neo4j_username
        self.password = password if password is not None else settings.neo4j_password
        self.database = database if database is not None else settings.neo4j_database
        self.driver = driver or self._create_driver()

        if self.driver is not None:
            self.ensure_schema()

    def _create_driver(self):
        if not self.uri:
            return None

        if GraphDatabase is None:
            raise RuntimeError(
                "Neo4jGraphStore is configured but the 'neo4j' package is not installed. "
                "Install it with `python -m pip install neo4j`."
            )

        auth = None
        if self.username:
            auth = (self.username, self.password)
        return GraphDatabase.driver(self.uri, auth=auth)

    def is_configured(self) -> bool:
        return self.driver is not None

    def _require_driver(self) -> None:
        if self.driver is None:
            raise RuntimeError(
                "Neo4jGraphStore is not configured. Set NEO4J_URI / NEO4J_USERNAME / "
                "NEO4J_PASSWORD or inject a driver explicitly."
            )

    def _session(self):
        self._require_driver()
        if self.database:
            return self.driver.session(database=self.database)
        return self.driver.session()

    def ensure_schema(self) -> None:
        queries = [
            "CREATE CONSTRAINT document_source_name IF NOT EXISTS FOR (d:Document) REQUIRE d.source_name IS UNIQUE",
            "CREATE CONSTRAINT section_chunk_id IF NOT EXISTS FOR (s:Section) REQUIRE s.chunk_id IS UNIQUE",
            "CREATE CONSTRAINT entity_node_id IF NOT EXISTS FOR (e:Entity) REQUIRE e.node_id IS UNIQUE",
            "CREATE CONSTRAINT metric_metric_id IF NOT EXISTS FOR (m:Metric) REQUIRE m.metric_id IS UNIQUE",
            "CREATE CONSTRAINT period_period_id IF NOT EXISTS FOR (p:Period) REQUIRE p.period_id IS UNIQUE",
            "CREATE CONSTRAINT graph_job_id IF NOT EXISTS FOR (j:GraphBuildJob) REQUIRE j.job_id IS UNIQUE",
        ]
        with self._session() as session:
            for query in queries:
                session.run(query)

    def clear(self) -> None:
        with self._session() as session:
            session.run("MATCH (n) DETACH DELETE n")

    def replace_document_graph(
        self,
        source_name: str,
        extractions: List[Dict],
        structured_facts: Optional[List[Dict]] = None,
    ) -> None:
        with self._session() as session:
            session.run(
                """
                MATCH (:Document {source_name: $source_name})-[:HAS_SECTION]->(s:Section)
                DETACH DELETE s
                """,
                source_name=source_name,
            )
            session.run(
                "MATCH (d:Document {source_name: $source_name}) DETACH DELETE d",
                source_name=source_name,
            )
            session.run(
                "MATCH (m:Metric) WHERE NOT (:Document)-[:REPORTS_METRIC]->(m) DETACH DELETE m"
            )
            session.run(
                "MATCH (p:Period) WHERE NOT (:Metric)-[:FOR_PERIOD]->(p) DETACH DELETE p"
            )
            session.run(
                "MATCH (e:Entity) WHERE NOT ()-[:MENTIONS]->(e) DETACH DELETE e"
            )

        self.build_from_extractions(extractions, structured_facts=structured_facts)

    def build_from_extractions(
        self,
        extractions: List[Dict],
        structured_facts: Optional[List[Dict]] = None,
    ) -> None:
        if not extractions and not structured_facts:
            return

        with self._session() as session:
            for extraction in extractions:
                source_name = extraction.get("source")
                chunk_id = extraction.get("chunk_id")
                if not source_name or not chunk_id:
                    continue

                session.run(
                    """
                    MERGE (d:Document {source_name: $source_name})
                    SET d.node_id = $document_node_id,
                        d.label = $source_name,
                        d.entity_type = 'Document'
                    MERGE (s:Section {chunk_id: $chunk_id})
                    SET s.source_name = $source_name,
                        s.node_id = $section_node_id,
                        s.label = $chunk_id,
                        s.entity_type = 'Section'
                    MERGE (d)-[:HAS_SECTION]->(s)
                    """,
                    source_name=source_name,
                    document_node_id=f"Document::{source_name.lower()}",
                    section_node_id=f"Section::{chunk_id.lower()}",
                    chunk_id=chunk_id,
                )

                entity_map = {}
                for entity in extraction.get("entities", []):
                    label = entity["name"].strip()
                    entity_type = entity["type"].strip()
                    node_id = f"{entity_type}::{label.lower()}"
                    entity_map[label] = node_id

                    session.run(
                        """
                        MERGE (e:Entity {node_id: $node_id})
                        SET e.label = $label, e.entity_type = $entity_type
                        WITH e
                        MATCH (d:Document {source_name: $source_name})
                        MERGE (d)-[:MENTIONS_ENTITY {
                            relationship_type: 'MENTIONS_ENTITY',
                            source_doc: $source_name,
                            chunk_id: $chunk_id
                        }]->(e)
                        WITH e
                        MATCH (s:Section {chunk_id: $chunk_id})
                        MERGE (s)-[:MENTIONS]->(e)
                        """,
                        node_id=node_id,
                        label=label,
                        entity_type=entity_type,
                        source_name=source_name,
                        chunk_id=chunk_id,
                    )

                for rel in extraction.get("relationships", []):
                    source_node_id = entity_map.get(rel.get("source"))
                    target_node_id = entity_map.get(rel.get("target"))
                    relationship_type = rel.get("type")
                    if not source_node_id or not target_node_id or not relationship_type:
                        continue

                    session.run(
                        """
                        MATCH (source:Entity {node_id: $source_node_id})
                        MATCH (target:Entity {node_id: $target_node_id})
                        MERGE (source)-[r:RELATES_TO {
                            relationship_type: $relationship_type,
                            source_doc: $source_name,
                            chunk_id: $chunk_id
                        }]->(target)
                        """,
                        source_node_id=source_node_id,
                        target_node_id=target_node_id,
                        relationship_type=relationship_type,
                        source_name=source_name,
                        chunk_id=chunk_id,
                    )

            for fact in structured_facts or []:
                source_name = fact.get("source_name")
                if not source_name:
                    continue

                metric_key = fact.get("metric_key", "metric")
                period = fact.get("period") or "Unknown period"
                section_index = fact.get("section_index")
                fact_id = fact.get("fact_id")
                metric_id = (
                    f"{source_name.lower()}::{metric_key.lower()}::"
                    f"{str(period).lower()}::{section_index}"
                )
                period_id = str(period).lower()
                metric_label = fact.get("metric_label", metric_key)
                value_text = fact.get("value_text", "")
                page_label = fact.get("page_label")
                metric_node_label = metric_label
                if value_text:
                    metric_node_label = f"{metric_label}: {value_text}"
                if page_label:
                    metric_node_label = f"{metric_node_label} ({page_label})"

                session.run(
                    """
                    MERGE (d:Document {source_name: $source_name})
                    SET d.node_id = $document_node_id,
                        d.label = $source_name,
                        d.entity_type = 'Document'
                    MERGE (m:Metric {metric_id: $metric_id})
                    SET m.node_id = $metric_node_id,
                        m.label = $metric_label,
                        m.entity_type = 'Metric',
                        m.metric_key = $metric_key,
                        m.period = $period,
                        m.value_text = $value_text,
                        m.page_label = $page_label,
                        m.fact_id = $fact_id,
                        m.source_name = $source_name
                    MERGE (d)-[:REPORTS_METRIC {
                        relationship_type: 'REPORTS_METRIC',
                        source_doc: $source_name,
                        chunk_id: $fact_id
                    }]->(m)
                    MERGE (p:Period {period_id: $period_id})
                    SET p.node_id = $period_node_id,
                        p.label = $period,
                        p.entity_type = 'Period'
                    MERGE (m)-[:FOR_PERIOD {
                        relationship_type: 'FOR_PERIOD',
                        source_doc: $source_name,
                        chunk_id: $fact_id
                    }]->(p)
                    """,
                    source_name=source_name,
                    document_node_id=f"Document::{source_name.lower()}",
                    metric_id=metric_id,
                    metric_node_id=f"Metric::{metric_id}",
                    metric_label=metric_node_label,
                    metric_key=metric_key,
                    period=str(period),
                    period_id=period_id,
                    period_node_id=f"Period::{period_id}",
                    value_text=value_text,
                    page_label=page_label,
                    fact_id=fact_id,
                )

    def graph_summary(self) -> Dict[str, int]:
        with self._session() as session:
            node_row = session.run(
                "MATCH (n) WHERE NOT n:GraphBuildJob RETURN count(n) AS num_nodes"
            ).single()
            edge_row = session.run(
                """
                MATCH ()-[r]->()
                WHERE NOT startNode(r):GraphBuildJob AND NOT endNode(r):GraphBuildJob
                RETURN count(r) AS num_edges
                """
            ).single()
        return {
            "num_nodes": int(_record_get(node_row, "num_nodes", 0) or 0),
            "num_edges": int(_record_get(edge_row, "num_edges", 0) or 0),
        }

    def document_has_graph(self, source_name: str) -> bool:
        with self._session() as session:
            row = session.run(
                """
                MATCH (d:Document {source_name: $source_name})
                OPTIONAL MATCH (d)-[:HAS_SECTION]->(s:Section)
                OPTIONAL MATCH (d)-[:REPORTS_METRIC]->(m:Metric)
                WITH count(s) AS section_count, count(m) AS metric_count
                WHERE section_count > 0 OR metric_count > 0
                RETURN 1 AS found
                LIMIT 1
                """,
                source_name=source_name,
            ).single()
        return row is not None

    def get_document_graph(
        self,
        source_name: str,
        max_nodes: Optional[int] = None,
        max_edges: Optional[int] = None,
    ) -> nx.MultiDiGraph:
        graph = nx.MultiDiGraph()

        with self._session() as session:
            entity_rows = list(
                session.run(
                    """
                    MATCH (:Document {source_name: $source_name})-[:HAS_SECTION]->(:Section)-[:MENTIONS]->(e:Entity)
                    WITH DISTINCT e
                    ORDER BY e.label ASC
                    RETURN e.node_id AS node_id, e.label AS label, e.entity_type AS entity_type
                    """,
                    source_name=source_name,
                )
            )
            metric_rows = list(
                session.run(
                    """
                    MATCH (d:Document {source_name: $source_name})
                    OPTIONAL MATCH (d)-[:REPORTS_METRIC]->(m:Metric)
                    OPTIONAL MATCH (m)-[:FOR_PERIOD]->(p:Period)
                    WITH d, collect(DISTINCT m) AS metrics, collect(DISTINCT p) AS periods
                    UNWIND ([d] + metrics + periods) AS n
                    WITH DISTINCT n
                    WHERE n IS NOT NULL
                    RETURN n.node_id AS node_id, n.label AS label, n.entity_type AS entity_type
                    """,
                    source_name=source_name,
                )
            )
            entity_edge_rows = list(
                session.run(
                    """
                    MATCH (d:Document {source_name: $source_name})-[r:MENTIONS_ENTITY]->(target:Entity)
                    RETURN d.node_id AS source_node_id,
                           target.node_id AS target_node_id,
                           r.relationship_type AS relationship_type,
                           r.source_doc AS source_doc,
                           r.chunk_id AS chunk_id
                    UNION
                    MATCH (:Document {source_name: $source_name})-[:HAS_SECTION]->(:Section)-[:MENTIONS]->(source:Entity)
                    MATCH (source)-[r:RELATES_TO {source_doc: $source_name}]->(target:Entity)
                    RETURN source.node_id AS source_node_id,
                           target.node_id AS target_node_id,
                           r.relationship_type AS relationship_type,
                           r.source_doc AS source_doc,
                           r.chunk_id AS chunk_id
                    """,
                    source_name=source_name,
                )
            )
            metric_edge_rows = list(
                session.run(
                    """
                    MATCH (d:Document {source_name: $source_name})-[r:REPORTS_METRIC]->(m:Metric)
                    RETURN d.node_id AS source_node_id,
                           m.node_id AS target_node_id,
                           r.relationship_type AS relationship_type,
                           r.source_doc AS source_doc,
                           r.chunk_id AS chunk_id
                    UNION
                    MATCH (d:Document {source_name: $source_name})-[:REPORTS_METRIC]->(m:Metric)-[r:FOR_PERIOD]->(p:Period)
                    RETURN m.node_id AS source_node_id,
                           p.node_id AS target_node_id,
                           r.relationship_type AS relationship_type,
                           r.source_doc AS source_doc,
                           r.chunk_id AS chunk_id
                    """,
                    source_name=source_name,
                )
            )

        node_rows = entity_rows + metric_rows
        deduped_node_rows = {}
        for row in node_rows:
            node_id = _record_get(row, "node_id")
            if node_id:
                deduped_node_rows[node_id] = row
        node_rows = list(deduped_node_rows.values())
        edge_rows = entity_edge_rows + metric_edge_rows

        if max_nodes is not None:
            node_rows = node_rows[:max_nodes]

        allowed_node_ids = {
            _record_get(row, "node_id")
            for row in node_rows
            if _record_get(row, "node_id")
        }

        for row in node_rows:
            node_id = _record_get(row, "node_id")
            if not node_id:
                continue
            graph.add_node(
                node_id,
                label=_record_get(row, "label", node_id),
                entity_type=_record_get(row, "entity_type", "Entity"),
                sources=[source_name],
            )

        filtered_edges = [
            row
            for row in edge_rows
            if _record_get(row, "source_node_id") in allowed_node_ids
            and _record_get(row, "target_node_id") in allowed_node_ids
        ]
        if max_edges is not None:
            filtered_edges = filtered_edges[:max_edges]

        for row in filtered_edges:
            graph.add_edge(
                _record_get(row, "source_node_id"),
                _record_get(row, "target_node_id"),
                relationship_type=_record_get(row, "relationship_type", "RELATES_TO"),
                source_doc=_record_get(row, "source_doc", source_name),
                chunk_id=_record_get(row, "chunk_id"),
            )

        return graph

    def get_document_node_details(
        self,
        source_name: str,
        limit: int = 20,
    ) -> List[Dict[str, str]]:
        with self._session() as session:
            entity_rows = list(
                session.run(
                    """
                    MATCH (:Document {source_name: $source_name})-[:HAS_SECTION]->(:Section)-[:MENTIONS]->(e:Entity)
                    WITH DISTINCT e
                    ORDER BY e.label ASC
                    RETURN e.node_id AS node_id, e.label AS label, e.entity_type AS entity_type
                    LIMIT $limit
                    """,
                    source_name=source_name,
                    limit=limit,
                )
            )
            metric_rows = list(
                session.run(
                    """
                    MATCH (d:Document {source_name: $source_name})
                    OPTIONAL MATCH (d)-[:REPORTS_METRIC]->(m:Metric)
                    OPTIONAL MATCH (m)-[:FOR_PERIOD]->(p:Period)
                    WITH collect(DISTINCT d) + collect(DISTINCT m) + collect(DISTINCT p) AS nodes
                    UNWIND nodes AS n
                    WITH DISTINCT n
                    WHERE n IS NOT NULL
                    RETURN n.node_id AS node_id, n.label AS label, n.entity_type AS entity_type
                    LIMIT $limit
                    """,
                    source_name=source_name,
                    limit=limit,
                )
            )

        rows = entity_rows + metric_rows
        deduped_rows = {}
        for row in rows:
            node_id = _record_get(row, "node_id")
            if node_id:
                deduped_rows[node_id] = row
        rows = list(deduped_rows.values())[:limit]

        return [
            {
                "node_id": _record_get(row, "node_id", ""),
                "label": _record_get(row, "label", ""),
                "entity_type": _record_get(row, "entity_type", "Entity"),
            }
            for row in rows
        ]

    def get_sources_graph(
        self,
        source_names: List[str],
        max_nodes: Optional[int] = None,
        max_edges: Optional[int] = None,
    ) -> nx.MultiDiGraph:
        graph = nx.MultiDiGraph()
        if not source_names:
            return graph
        effective_max_nodes = max_nodes or max(len(source_names) * 30, 80)
        effective_max_edges = max_edges or max(len(source_names) * 48, 140)

        with self._session() as session:
            node_rows = list(
                session.run(
                    """
                    MATCH (n)
                    WHERE NOT n:GraphBuildJob
                      AND NOT n:Section
                      AND (
                        (n:Document AND n.source_name IN $source_names)
                        OR EXISTS {
                            MATCH (d:Document)-[r:MENTIONS_ENTITY|REPORTS_METRIC]->(n)
                            WHERE d.source_name IN $source_names
                              AND (r.source_doc IS NULL OR r.source_doc IN $source_names)
                        }
                        OR EXISTS {
                            MATCH (d:Document)-[:REPORTS_METRIC]->(:Metric)-[r:FOR_PERIOD]->(n)
                            WHERE d.source_name IN $source_names
                              AND (r.source_doc IS NULL OR r.source_doc IN $source_names)
                        }
                        OR EXISTS {
                            MATCH (:Entity)-[r:RELATES_TO]->(n)
                            WHERE r.source_doc IN $source_names
                        }
                        OR EXISTS {
                            MATCH (n)-[r:RELATES_TO]->(:Entity)
                            WHERE r.source_doc IN $source_names
                        }
                      )
                    RETURN DISTINCT n.node_id AS node_id,
                                    n.label AS label,
                                    n.entity_type AS entity_type,
                                    coalesce(n.source_name, '') AS source_name
                    LIMIT $max_nodes
                    """,
                    source_names=source_names,
                    max_nodes=effective_max_nodes,
                )
            )
            edge_rows = list(
                session.run(
                    """
                    MATCH (source)-[r]->(target)
                    WHERE type(r) IN ['MENTIONS_ENTITY', 'RELATES_TO', 'REPORTS_METRIC', 'FOR_PERIOD']
                      AND NOT source:Section
                      AND NOT target:Section
                      AND NOT source:GraphBuildJob
                      AND NOT target:GraphBuildJob
                      AND (
                        (source:Document AND source.source_name IN $source_names)
                        OR coalesce(r.source_doc, '') IN $source_names
                        OR (
                            type(r) = 'FOR_PERIOD'
                            AND EXISTS {
                                MATCH (d:Document)-[:REPORTS_METRIC]->(source)
                                WHERE d.source_name IN $source_names
                            }
                        )
                      )
                    RETURN DISTINCT source.node_id AS source_node_id,
                                    target.node_id AS target_node_id,
                                    r.relationship_type AS relationship_type,
                                    r.source_doc AS source_doc,
                                    r.chunk_id AS chunk_id
                    LIMIT $max_edges
                    """,
                    source_names=source_names,
                    max_edges=effective_max_edges,
                )
            )

        allowed_node_ids = set()
        for row in node_rows:
            node_id = _record_get(row, "node_id")
            if not node_id:
                continue
            allowed_node_ids.add(node_id)
            source_name = _record_get(row, "source_name") or None
            graph.add_node(
                node_id,
                label=_record_get(row, "label", node_id),
                entity_type=_record_get(row, "entity_type", "Entity"),
                sources=[source_name] if source_name else list(source_names),
            )

        for row in edge_rows:
            source_node_id = _record_get(row, "source_node_id")
            target_node_id = _record_get(row, "target_node_id")
            if source_node_id not in allowed_node_ids or target_node_id not in allowed_node_ids:
                continue
            graph.add_edge(
                source_node_id,
                target_node_id,
                relationship_type=_record_get(row, "relationship_type", "RELATES_TO"),
                source_doc=_record_get(row, "source_doc"),
                chunk_id=_record_get(row, "chunk_id"),
            )

        return graph

    def _seed_terms(self, query: str, query_entities: Optional[List[Dict]] = None) -> List[str]:
        terms: List[str] = []
        for entity in query_entities or []:
            name = (entity.get("name") or "").strip().lower()
            if name:
                terms.append(name)
        for token in re.findall(r"[a-zA-Z0-9]+", (query or "").lower()):
            if len(token) >= 4 and token not in self._QUERY_STOPWORDS:
                terms.append(token)
        return list(dict.fromkeys(terms))

    def get_query_neighborhood(
        self,
        source_names: List[str],
        query: str,
        query_entities: Optional[List[Dict]] = None,
        radius: int = 2,
        max_nodes: Optional[int] = None,
        max_edges: Optional[int] = None,
    ) -> Optional[Dict]:
        if not source_names:
            return None

        graph = nx.MultiDiGraph()
        seed_terms = self._seed_terms(query, query_entities=query_entities)
        hop_limit = max(1, min(int(radius or 1), 3))
        effective_max_nodes = max_nodes or max(len(source_names) * 18, 48)
        effective_max_edges = max_edges or max(len(source_names) * 24, 72)
        seed_limit = max(len(seed_terms) * 2, 8)

        with self._session() as session:
            seed_query = """
                MATCH (seed)
                WHERE NOT seed:GraphBuildJob
                  AND NOT seed:Section
                  AND (
                    (seed:Document AND seed.source_name IN $source_names)
                    OR EXISTS {
                        MATCH (d:Document)-[r:MENTIONS_ENTITY|REPORTS_METRIC]->(seed)
                        WHERE d.source_name IN $source_names
                          AND (r.source_doc IS NULL OR r.source_doc IN $source_names)
                    }
                    OR EXISTS {
                        MATCH (d:Document)-[:REPORTS_METRIC]->(:Metric)-[r:FOR_PERIOD]->(seed)
                        WHERE d.source_name IN $source_names
                          AND (r.source_doc IS NULL OR r.source_doc IN $source_names)
                    }
                    OR EXISTS {
                        MATCH (:Entity)-[r:RELATES_TO]->(seed)
                        WHERE r.source_doc IN $source_names
                    }
                    OR EXISTS {
                        MATCH (seed)-[r:RELATES_TO]->(:Entity)
                        WHERE r.source_doc IN $source_names
                    }
                  )
            """
            if seed_terms:
                seed_query += """
                  AND any(term IN $seed_terms WHERE
                    toLower(coalesce(seed.label, '')) CONTAINS term
                    OR toLower(coalesce(seed.metric_key, '')) CONTAINS term
                    OR toLower(coalesce(seed.source_name, '')) CONTAINS term
                  )
                """
            seed_query += """
                RETURN DISTINCT seed.node_id AS node_id,
                                seed.label AS label,
                                seed.entity_type AS entity_type,
                                coalesce(seed.source_name, '') AS source_name
                LIMIT $seed_limit
            """
            seed_rows = list(
                session.run(
                    seed_query,
                    source_names=source_names,
                    seed_terms=seed_terms,
                    seed_limit=seed_limit,
                )
            )
            if not seed_rows:
                return None

            seed_node_ids = [
                _record_get(row, "node_id")
                for row in seed_rows
                if _record_get(row, "node_id")
            ]
            if not seed_node_ids:
                return None

            node_rows = list(
                session.run(
                    f"""
                    MATCH (seed)
                    WHERE seed.node_id IN $seed_node_ids
                    CALL {{
                        WITH seed
                        MATCH p=(seed)-[:MENTIONS_ENTITY|RELATES_TO|REPORTS_METRIC|FOR_PERIOD*1..{hop_limit}]-(neighbor)
                        WHERE NOT neighbor:GraphBuildJob
                          AND NOT neighbor:Section
                          AND all(rel IN relationships(p) WHERE coalesce(rel.source_doc, '') = '' OR rel.source_doc IN $source_names)
                        UNWIND nodes(p) AS n
                        RETURN DISTINCT n
                    }}
                    WITH DISTINCT n
                    WHERE NOT n:GraphBuildJob AND NOT n:Section
                    RETURN DISTINCT n.node_id AS node_id,
                                    n.label AS label,
                                    n.entity_type AS entity_type,
                                    coalesce(n.source_name, '') AS source_name
                    LIMIT $max_nodes
                    """,
                    seed_node_ids=seed_node_ids,
                    source_names=source_names,
                    max_nodes=effective_max_nodes,
                )
            )

            edge_rows = list(
                session.run(
                    f"""
                    MATCH (seed)
                    WHERE seed.node_id IN $seed_node_ids
                    CALL {{
                        WITH seed
                        MATCH p=(seed)-[:MENTIONS_ENTITY|RELATES_TO|REPORTS_METRIC|FOR_PERIOD*1..{hop_limit}]-(neighbor)
                        WHERE NOT neighbor:GraphBuildJob
                          AND NOT neighbor:Section
                          AND all(rel IN relationships(p) WHERE coalesce(rel.source_doc, '') = '' OR rel.source_doc IN $source_names)
                        UNWIND relationships(p) AS r
                        RETURN DISTINCT r, startNode(r) AS source, endNode(r) AS target
                    }}
                    WHERE NOT source:Section
                      AND NOT target:Section
                      AND NOT source:GraphBuildJob
                      AND NOT target:GraphBuildJob
                    RETURN DISTINCT source.node_id AS source_node_id,
                                    target.node_id AS target_node_id,
                                    r.relationship_type AS relationship_type,
                                    r.source_doc AS source_doc,
                                    r.chunk_id AS chunk_id
                    LIMIT $max_edges
                    """,
                    seed_node_ids=seed_node_ids,
                    source_names=source_names,
                    max_edges=effective_max_edges,
                )
            )

        for row in seed_rows:
            node_id = _record_get(row, "node_id")
            if not node_id:
                continue
            source_name = _record_get(row, "source_name") or None
            graph.add_node(
                node_id,
                label=_record_get(row, "label", node_id),
                entity_type=_record_get(row, "entity_type", "Entity"),
                sources=[source_name] if source_name else list(source_names),
            )

        for row in node_rows:
            node_id = _record_get(row, "node_id")
            if not node_id:
                continue
            source_name = _record_get(row, "source_name") or None
            graph.add_node(
                node_id,
                label=_record_get(row, "label", node_id),
                entity_type=_record_get(row, "entity_type", "Entity"),
                sources=[source_name] if source_name else list(source_names),
            )

        allowed_node_ids = set(graph.nodes())
        for row in edge_rows:
            source_node_id = _record_get(row, "source_node_id")
            target_node_id = _record_get(row, "target_node_id")
            if source_node_id not in allowed_node_ids or target_node_id not in allowed_node_ids:
                continue
            graph.add_edge(
                source_node_id,
                target_node_id,
                relationship_type=_record_get(row, "relationship_type", "RELATES_TO"),
                source_doc=_record_get(row, "source_doc"),
                chunk_id=_record_get(row, "chunk_id"),
            )

        if graph.number_of_nodes() == 0:
            return None

        return {
            "graph": graph,
            "matched_node_ids": [node_id for node_id in seed_node_ids if graph.has_node(node_id)],
            "source_names": list(dict.fromkeys(source_names)),
        }

    def queue_job(self, source_name: str, file_hash: str) -> str:
        job_id = str(uuid.uuid4())
        with self._session() as session:
            session.run(
                """
                CREATE (j:GraphBuildJob {
                    job_id: $job_id,
                    source_name: $source_name,
                    file_hash: $file_hash,
                    status: 'queued',
                    created_at: toString(datetime())
                })
                """,
                job_id=job_id,
                source_name=source_name,
                file_hash=file_hash,
            )
        return job_id

    def list_jobs(self) -> List[Dict]:
        with self._session() as session:
            rows = list(
                session.run(
                    """
                    MATCH (j:GraphBuildJob)
                    RETURN properties(j) AS job_props
                    ORDER BY j.created_at DESC
                    """
                )
            )
        return [
            {
                "job_id": _record_get(_record_get(row, "job_props", {}), "job_id"),
                "source_name": _record_get(_record_get(row, "job_props", {}), "source_name"),
                "file_hash": _record_get(_record_get(row, "job_props", {}), "file_hash"),
                "status": _record_get(_record_get(row, "job_props", {}), "status"),
                "error": _record_get(_record_get(row, "job_props", {}), "error"),
                "created_at": _record_get(_record_get(row, "job_props", {}), "created_at"),
                "started_at": _record_get(_record_get(row, "job_props", {}), "started_at"),
                "finished_at": _record_get(_record_get(row, "job_props", {}), "finished_at"),
            }
            for row in rows
        ]

    def next_queued_job(self) -> Optional[Dict]:
        with self._session() as session:
            row = session.run(
                """
                MATCH (j:GraphBuildJob {status: 'queued'})
                RETURN j.job_id AS job_id,
                       j.source_name AS source_name,
                       j.file_hash AS file_hash
                ORDER BY j.created_at ASC
                LIMIT 1
                """
            ).single()
        if row is None:
            return None
        return {
            "job_id": _record_get(row, "job_id"),
            "source_name": _record_get(row, "source_name"),
            "file_hash": _record_get(row, "file_hash"),
        }

    def mark_job_running(self, job_id) -> None:
        with self._session() as session:
            session.run(
                """
                MATCH (j:GraphBuildJob {job_id: $job_id})
                SET j.status = 'running', j.started_at = toString(datetime()), j.error = NULL
                """,
                job_id=str(job_id),
            )

    def mark_job_complete(self, job_id) -> None:
        with self._session() as session:
            session.run(
                """
                MATCH (j:GraphBuildJob {job_id: $job_id})
                SET j.status = 'completed', j.finished_at = toString(datetime())
                """,
                job_id=str(job_id),
            )

    def mark_job_failed(self, job_id, error: str) -> None:
        with self._session() as session:
            session.run(
                """
                MATCH (j:GraphBuildJob {job_id: $job_id})
                SET j.status = 'failed',
                    j.finished_at = toString(datetime()),
                    j.error = $error
                """,
                job_id=str(job_id),
                error=error[:1000],
            )

    def close(self) -> None:
        if self.driver is not None:
            self.driver.close()
