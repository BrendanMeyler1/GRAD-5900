import re
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

import networkx as nx
from backend.graph.base import GraphStore


class SQLiteGraphStore(GraphStore):
    backend_name = "sqlite"
    _QUERY_STOPWORDS = {
        "the", "and", "for", "with", "from", "that", "this", "what", "which",
        "show", "across", "into", "their", "about", "then", "plain", "english",
        "among", "between",
    }

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS nodes (
                    node_id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    entity_type TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS node_sources (
                    node_id TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    chunk_id TEXT,
                    PRIMARY KEY (node_id, source_name, chunk_id)
                );

                CREATE TABLE IF NOT EXISTS edges (
                    source_node_id TEXT NOT NULL,
                    target_node_id TEXT NOT NULL,
                    relationship_type TEXT NOT NULL,
                    source_doc TEXT,
                    chunk_id TEXT,
                    PRIMARY KEY (source_node_id, target_node_id, relationship_type, source_doc, chunk_id)
                );

                CREATE TABLE IF NOT EXISTS graph_jobs (
                    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_name TEXT NOT NULL,
                    file_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    started_at TEXT,
                    finished_at TEXT
                );
                """
            )

    def clear(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                DELETE FROM edges;
                DELETE FROM node_sources;
                DELETE FROM nodes;
                DELETE FROM graph_jobs;
                """
            )

    def replace_document_graph(
        self,
        source_name: str,
        extractions: List[Dict],
        structured_facts: Optional[List[Dict]] = None,
    ) -> None:
        with self._connect() as conn:
            node_ids = [
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT node_id FROM node_sources WHERE source_name = ?",
                    (source_name,),
                ).fetchall()
            ]
            conn.execute("DELETE FROM edges WHERE source_doc = ?", (source_name,))
            conn.execute("DELETE FROM node_sources WHERE source_name = ?", (source_name,))
            for node_id in node_ids:
                still_used = conn.execute(
                    "SELECT 1 FROM node_sources WHERE node_id = ? LIMIT 1",
                    (node_id,),
                ).fetchone()
                if not still_used:
                    conn.execute("DELETE FROM nodes WHERE node_id = ?", (node_id,))
            conn.commit()

        self.build_from_extractions(extractions, structured_facts=structured_facts)

    def build_from_extractions(
        self,
        extractions: List[Dict],
        structured_facts: Optional[List[Dict]] = None,
    ) -> None:
        with self._connect() as conn:
            for extraction in extractions:
                chunk_id = extraction.get("chunk_id")
                source_name = extraction.get("source")
                entity_map = {}
                document_node_id = f"Document::{source_name.lower()}" if source_name else None

                if source_name and document_node_id:
                    conn.execute(
                        """
                        INSERT INTO nodes (node_id, label, entity_type)
                        VALUES (?, ?, ?)
                        ON CONFLICT(node_id) DO UPDATE SET
                            label = excluded.label,
                            entity_type = excluded.entity_type
                        """,
                        (document_node_id, source_name, "Document"),
                    )
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO node_sources (node_id, source_name, chunk_id)
                        VALUES (?, ?, ?)
                        """,
                        (document_node_id, source_name, None),
                    )

                for entity in extraction.get("entities", []):
                    label = entity["name"].strip()
                    entity_type = entity["type"].strip()
                    node_id = f"{entity_type}::{label.lower()}"
                    entity_map[label] = node_id
                    conn.execute(
                        """
                        INSERT INTO nodes (node_id, label, entity_type)
                        VALUES (?, ?, ?)
                        ON CONFLICT(node_id) DO UPDATE SET
                            label = excluded.label,
                            entity_type = excluded.entity_type
                        """,
                        (node_id, label, entity_type),
                    )
                    if source_name:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO node_sources (node_id, source_name, chunk_id)
                            VALUES (?, ?, ?)
                            """,
                            (node_id, source_name, chunk_id),
                        )
                        if document_node_id:
                            conn.execute(
                                """
                                INSERT OR IGNORE INTO edges (
                                    source_node_id, target_node_id, relationship_type, source_doc, chunk_id
                                ) VALUES (?, ?, ?, ?, ?)
                                """,
                                (
                                    document_node_id,
                                    node_id,
                                    "MENTIONS_ENTITY",
                                    source_name,
                                    chunk_id,
                                ),
                            )

                for rel in extraction.get("relationships", []):
                    source_node_id = entity_map.get(rel["source"])
                    target_node_id = entity_map.get(rel["target"])
                    if not source_node_id or not target_node_id:
                        continue
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO edges (
                            source_node_id, target_node_id, relationship_type, source_doc, chunk_id
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            source_node_id,
                            target_node_id,
                            rel["type"],
                            source_name,
                            chunk_id,
                        ),
                    )

            for fact in structured_facts or []:
                source_name = fact.get("source_name")
                if not source_name:
                    continue

                metric_key = fact.get("metric_key", "metric")
                metric_label = fact.get("metric_label", metric_key)
                period = fact.get("period") or "Unknown period"
                page_label = fact.get("page_label")
                value_text = fact.get("value_text", "")
                section_index = fact.get("section_index")

                document_node_id = f"Document::{source_name.lower()}"
                metric_node_id = (
                    f"Metric::{source_name.lower()}::{metric_key.lower()}::"
                    f"{str(period).lower()}::{section_index}"
                )
                period_node_id = f"Period::{str(period).lower()}"

                conn.execute(
                    """
                    INSERT INTO nodes (node_id, label, entity_type)
                    VALUES (?, ?, ?)
                    ON CONFLICT(node_id) DO UPDATE SET
                        label = excluded.label,
                        entity_type = excluded.entity_type
                    """,
                    (document_node_id, source_name, "Document"),
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO node_sources (node_id, source_name, chunk_id)
                    VALUES (?, ?, ?)
                    """,
                    (document_node_id, source_name, None),
                )

                metric_label_text = metric_label
                if value_text:
                    metric_label_text = f"{metric_label}: {value_text}"
                if page_label:
                    metric_label_text = f"{metric_label_text} ({page_label})"

                conn.execute(
                    """
                    INSERT INTO nodes (node_id, label, entity_type)
                    VALUES (?, ?, ?)
                    ON CONFLICT(node_id) DO UPDATE SET
                        label = excluded.label,
                        entity_type = excluded.entity_type
                    """,
                    (metric_node_id, metric_label_text, "Metric"),
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO node_sources (node_id, source_name, chunk_id)
                    VALUES (?, ?, ?)
                    """,
                    (metric_node_id, source_name, None),
                )

                conn.execute(
                    """
                    INSERT INTO nodes (node_id, label, entity_type)
                    VALUES (?, ?, ?)
                    ON CONFLICT(node_id) DO UPDATE SET
                        label = excluded.label,
                        entity_type = excluded.entity_type
                    """,
                    (period_node_id, str(period), "Period"),
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO node_sources (node_id, source_name, chunk_id)
                    VALUES (?, ?, ?)
                    """,
                    (period_node_id, source_name, None),
                )

                conn.execute(
                    """
                    INSERT OR IGNORE INTO edges (
                        source_node_id, target_node_id, relationship_type, source_doc, chunk_id
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        document_node_id,
                        metric_node_id,
                        "REPORTS_METRIC",
                        source_name,
                        fact.get("fact_id"),
                    ),
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO edges (
                        source_node_id, target_node_id, relationship_type, source_doc, chunk_id
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        metric_node_id,
                        period_node_id,
                        "FOR_PERIOD",
                        source_name,
                        fact.get("fact_id"),
                    ),
                )
            conn.commit()

    def graph_summary(self) -> Dict[str, int]:
        with self._connect() as conn:
            num_nodes = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            num_edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        return {"num_nodes": num_nodes, "num_edges": num_edges}

    def document_has_graph(self, source_name: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM edges WHERE source_doc = ? LIMIT 1",
                (source_name,),
            ).fetchone()
        return row is not None

    def get_document_graph(
        self,
        source_name: str,
        max_nodes: Optional[int] = None,
        max_edges: Optional[int] = None,
    ) -> nx.MultiDiGraph:
        graph = nx.MultiDiGraph()

        with self._connect() as conn:
            edge_rows = conn.execute(
                """
                SELECT source_node_id, target_node_id, relationship_type, source_doc, chunk_id
                FROM edges
                WHERE source_doc = ?
                ORDER BY rowid ASC
                """,
                (source_name,),
            ).fetchall()

            if max_edges is not None:
                edge_rows = edge_rows[:max_edges]

            node_ids = []
            for row in edge_rows:
                node_ids.extend([row[0], row[1]])

            if not node_ids:
                node_rows = conn.execute(
                    """
                    SELECT DISTINCT n.node_id, n.label, n.entity_type
                    FROM nodes n
                    JOIN node_sources ns ON ns.node_id = n.node_id
                    WHERE ns.source_name = ?
                    ORDER BY n.label ASC
                    """,
                    (source_name,),
                ).fetchall()
                if max_nodes is not None:
                    node_rows = node_rows[:max_nodes]
                for node_id, label, entity_type in node_rows:
                    graph.add_node(
                        node_id,
                        label=label,
                        entity_type=entity_type,
                        sources=[source_name],
                    )
                return graph

            ordered_node_ids = list(dict.fromkeys(node_ids))
            if max_nodes is not None:
                ordered_node_ids = ordered_node_ids[:max_nodes]

            allowed_nodes = set(ordered_node_ids)
            filtered_edges = [
                row for row in edge_rows if row[0] in allowed_nodes and row[1] in allowed_nodes
            ]

            placeholders = ",".join("?" for _ in ordered_node_ids)
            node_rows = conn.execute(
                f"""
                SELECT node_id, label, entity_type
                FROM nodes
                WHERE node_id IN ({placeholders})
                """,
                ordered_node_ids,
            ).fetchall()

            source_rows = conn.execute(
                f"""
                SELECT node_id, source_name
                FROM node_sources
                WHERE node_id IN ({placeholders}) AND source_name = ?
                """,
                [*ordered_node_ids, source_name],
            ).fetchall()

        sources_by_node: Dict[str, List[str]] = {}
        for node_id, source in source_rows:
            sources_by_node.setdefault(node_id, []).append(source)

        for node_id, label, entity_type in node_rows:
            graph.add_node(
                node_id,
                label=label,
                entity_type=entity_type,
                sources=sources_by_node.get(node_id, [source_name]),
            )

        for source_node_id, target_node_id, relationship_type, source_doc, chunk_id in filtered_edges:
            graph.add_edge(
                source_node_id,
                target_node_id,
                relationship_type=relationship_type,
                source_doc=source_doc,
                chunk_id=chunk_id,
            )

        return graph

    def get_document_node_details(
        self,
        source_name: str,
        limit: int = 20,
    ) -> List[Dict[str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT n.node_id, n.label, n.entity_type
                FROM nodes n
                JOIN node_sources ns ON ns.node_id = n.node_id
                WHERE ns.source_name = ?
                ORDER BY n.label ASC
                LIMIT ?
                """,
                (source_name, limit),
            ).fetchall()
        return [
            {
                "node_id": row[0],
                "label": row[1],
                "entity_type": row[2],
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

        per_source_node_limit = None
        per_source_edge_limit = None
        if max_nodes is not None:
            per_source_node_limit = max(1, max_nodes // max(len(source_names), 1))
        if max_edges is not None:
            per_source_edge_limit = max(1, max_edges // max(len(source_names), 1))

        for source_name in source_names:
            source_graph = self.get_document_graph(
                source_name,
                max_nodes=per_source_node_limit,
                max_edges=per_source_edge_limit,
            )
            graph = nx.compose(graph, source_graph)

        return graph

    def get_query_neighborhood(
        self,
        source_names: List[str],
        query: str,
        query_entities: Optional[List[Dict]] = None,
        radius: int = 2,
        max_nodes: Optional[int] = None,
        max_edges: Optional[int] = None,
    ) -> Optional[Dict]:
        del radius
        graph = self.get_sources_graph(
            source_names,
            max_nodes=max_nodes,
            max_edges=max_edges,
        )
        if graph.number_of_nodes() == 0:
            return None

        seed_terms: List[str] = []
        for entity in query_entities or []:
            entity_name = (entity.get("name") or "").strip().lower()
            if entity_name:
                seed_terms.append(entity_name)
        for token in re.findall(r"[a-zA-Z0-9]+", (query or "").lower()):
            if len(token) >= 4 and token not in self._QUERY_STOPWORDS:
                seed_terms.append(token)
        seed_terms = list(dict.fromkeys(seed_terms))

        matched_node_ids: List[str] = []
        for node_id, attrs in graph.nodes(data=True):
            haystack = " ".join(
                [
                    str(attrs.get("label", "")),
                    str(attrs.get("entity_type", "")),
                    " ".join(attrs.get("sources", []) or []),
                ]
            ).lower()
            if any(term in haystack for term in seed_terms):
                matched_node_ids.append(node_id)

        if not matched_node_ids:
            return None

        return {
            "graph": graph,
            "matched_node_ids": matched_node_ids,
            "source_names": list(dict.fromkeys(source_names)),
        }

    def queue_job(self, source_name: str, file_hash: str) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO graph_jobs (source_name, file_hash, status) VALUES (?, ?, 'queued')",
                (source_name, file_hash),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def list_jobs(self) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT job_id, source_name, file_hash, status, error, created_at, started_at, finished_at
                FROM graph_jobs
                ORDER BY job_id DESC
                """
            ).fetchall()
        return [
            {
                "job_id": row[0],
                "source_name": row[1],
                "file_hash": row[2],
                "status": row[3],
                "error": row[4],
                "created_at": row[5],
                "started_at": row[6],
                "finished_at": row[7],
            }
            for row in rows
        ]

    def next_queued_job(self) -> Optional[Dict]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT job_id, source_name, file_hash FROM graph_jobs
                WHERE status = 'queued'
                ORDER BY job_id ASC
                LIMIT 1
                """
            ).fetchone()
        if not row:
            return None
        return {"job_id": row[0], "source_name": row[1], "file_hash": row[2]}

    def mark_job_running(self, job_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE graph_jobs SET status = 'running', started_at = CURRENT_TIMESTAMP, error = NULL WHERE job_id = ?",
                (job_id,),
            )
            conn.commit()

    def mark_job_complete(self, job_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE graph_jobs SET status = 'completed', finished_at = CURRENT_TIMESTAMP WHERE job_id = ?",
                (job_id,),
            )
            conn.commit()

    def mark_job_failed(self, job_id: int, error: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE graph_jobs SET status = 'failed', finished_at = CURRENT_TIMESTAMP, error = ? WHERE job_id = ?",
                (error[:1000], job_id),
            )
            conn.commit()
