import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from backend.core.config import settings


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OnlineEvalStore:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path or settings.online_eval_db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS online_runs (
                    run_id TEXT PRIMARY KEY,
                    conversation_id TEXT,
                    user_message_id TEXT,
                    assistant_message_id TEXT UNIQUE,
                    created_at TEXT NOT NULL,
                    query_text TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    retrieval_backend TEXT,
                    graph_backend TEXT,
                    latency_ms REAL,
                    cache_hit INTEGER NOT NULL DEFAULT 0,
                    was_corrected INTEGER NOT NULL DEFAULT 0,
                    fact_rerank_applied INTEGER NOT NULL DEFAULT 0,
                    graph_context_origin TEXT,
                    indexed_docs_json TEXT,
                    retrieved_sources_json TEXT,
                    answer_text TEXT,
                    preliminary_answer TEXT,
                    refined_answer TEXT,
                    retrieved_context_text TEXT,
                    graph_context_text TEXT,
                    facts_context_text TEXT,
                    feedback_score INTEGER,
                    feedback_label TEXT,
                    feedback_notes TEXT,
                    feedback_updated_at TEXT
                )
                """
            )

    def clear(self) -> Dict[str, int]:
        self._init_db()
        with self._connect() as conn:
            existing_rows = conn.execute("SELECT COUNT(*) FROM online_runs").fetchone()[0]
            conn.execute("DELETE FROM online_runs")
            conn.commit()
        return {"online_telemetry_reset": existing_rows}

    def log_run(self, payload: Dict[str, Any]) -> str:
        run_id = payload.get("run_id") or uuid4().hex
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO online_runs (
                    run_id, conversation_id, user_message_id, assistant_message_id, created_at,
                    query_text, mode, retrieval_backend, graph_backend, latency_ms, cache_hit,
                    was_corrected, fact_rerank_applied, graph_context_origin, indexed_docs_json,
                    retrieved_sources_json, answer_text, preliminary_answer, refined_answer,
                    retrieved_context_text, graph_context_text, facts_context_text,
                    feedback_score, feedback_label, feedback_notes, feedback_updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    payload.get("conversation_id"),
                    payload.get("user_message_id"),
                    payload.get("assistant_message_id"),
                    payload.get("created_at") or _utc_now(),
                    payload.get("query_text", ""),
                    payload.get("mode", "unknown"),
                    payload.get("retrieval_backend"),
                    payload.get("graph_backend"),
                    float(payload.get("latency_ms") or 0.0),
                    int(bool(payload.get("cache_hit"))),
                    int(bool(payload.get("was_corrected"))),
                    int(bool(payload.get("fact_rerank_applied"))),
                    payload.get("graph_context_origin"),
                    json.dumps(payload.get("indexed_docs", []), ensure_ascii=False),
                    json.dumps(payload.get("retrieved_sources", []), ensure_ascii=False),
                    payload.get("answer_text", ""),
                    payload.get("preliminary_answer", ""),
                    payload.get("refined_answer", ""),
                    payload.get("retrieved_context_text", ""),
                    payload.get("graph_context_text", ""),
                    payload.get("facts_context_text", ""),
                    payload.get("feedback_score"),
                    payload.get("feedback_label"),
                    payload.get("feedback_notes"),
                    payload.get("feedback_updated_at"),
                ),
            )
            conn.commit()
        return run_id

    def set_feedback(
        self,
        run_id: str,
        score: int,
        label: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE online_runs
                SET feedback_score = ?, feedback_label = ?, feedback_notes = ?, feedback_updated_at = ?
                WHERE run_id = ?
                """,
                (score, label, notes, _utc_now(), run_id),
            )
            conn.commit()
        return cursor.rowcount > 0

    def list_runs(
        self,
        limit: int = 50,
        feedback_only: bool = False,
    ) -> List[Dict[str, Any]]:
        query = """
            SELECT run_id, conversation_id, assistant_message_id, created_at, query_text, mode,
                   retrieval_backend, graph_backend, latency_ms, cache_hit, was_corrected,
                   fact_rerank_applied, graph_context_origin, indexed_docs_json,
                   retrieved_sources_json, answer_text, feedback_score, feedback_label,
                   feedback_notes, feedback_updated_at
            FROM online_runs
        """
        params: List[Any] = []
        if feedback_only:
            query += " WHERE feedback_score IS NOT NULL"
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()

        results = []
        for row in rows:
            results.append(
                {
                    "run_id": row[0],
                    "conversation_id": row[1],
                    "assistant_message_id": row[2],
                    "created_at": row[3],
                    "query_text": row[4],
                    "mode": row[5],
                    "retrieval_backend": row[6],
                    "graph_backend": row[7],
                    "latency_ms": row[8],
                    "cache_hit": bool(row[9]),
                    "was_corrected": bool(row[10]),
                    "fact_rerank_applied": bool(row[11]),
                    "graph_context_origin": row[12],
                    "indexed_docs": json.loads(row[13] or "[]"),
                    "retrieved_sources": json.loads(row[14] or "[]"),
                    "answer_text": row[15] or "",
                    "feedback_score": row[16],
                    "feedback_label": row[17],
                    "feedback_notes": row[18],
                    "feedback_updated_at": row[19],
                }
            )
        return results

    def summary(self, limit: int = 200) -> Dict[str, Any]:
        runs = self.list_runs(limit=limit)
        if not runs:
            return {
                "num_runs": 0,
                "feedback_count": 0,
                "thumbs_up": 0,
                "thumbs_down": 0,
                "feedback_rate": 0.0,
                "positive_rate": 0.0,
                "avg_latency_ms": 0.0,
                "cache_hit_rate": 0.0,
            }

        feedback_runs = [run for run in runs if run["feedback_score"] is not None]
        thumbs_up = sum(1 for run in feedback_runs if run["feedback_score"] > 0)
        thumbs_down = sum(1 for run in feedback_runs if run["feedback_score"] < 0)

        return {
            "num_runs": len(runs),
            "feedback_count": len(feedback_runs),
            "thumbs_up": thumbs_up,
            "thumbs_down": thumbs_down,
            "feedback_rate": len(feedback_runs) / len(runs),
            "positive_rate": thumbs_up / len(feedback_runs) if feedback_runs else 0.0,
            "avg_latency_ms": sum(run["latency_ms"] or 0.0 for run in runs) / len(runs),
            "cache_hit_rate": sum(1 for run in runs if run["cache_hit"]) / len(runs),
        }

    def summarize_by_mode(self, limit: int = 200) -> List[Dict[str, Any]]:
        runs = self.list_runs(limit=limit)
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for run in runs:
            grouped.setdefault(run["mode"], []).append(run)

        summary_rows: List[Dict[str, Any]] = []
        for mode, mode_runs in sorted(grouped.items()):
            feedback_runs = [run for run in mode_runs if run["feedback_score"] is not None]
            thumbs_up = sum(1 for run in feedback_runs if run["feedback_score"] > 0)
            thumbs_down = sum(1 for run in feedback_runs if run["feedback_score"] < 0)
            summary_rows.append(
                {
                    "mode": mode,
                    "num_runs": len(mode_runs),
                    "avg_latency_ms": sum(run["latency_ms"] or 0.0 for run in mode_runs) / len(mode_runs),
                    "cache_hit_rate": sum(1 for run in mode_runs if run["cache_hit"]) / len(mode_runs),
                    "correction_rate": sum(1 for run in mode_runs if run["was_corrected"]) / len(mode_runs),
                    "feedback_count": len(feedback_runs),
                    "thumbs_up": thumbs_up,
                    "thumbs_down": thumbs_down,
                    "positive_rate": thumbs_up / len(feedback_runs) if feedback_runs else 0.0,
                }
            )
        return summary_rows
