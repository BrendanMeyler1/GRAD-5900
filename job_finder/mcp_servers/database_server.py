"""
Database MCP Server (Phase 2 Step 7).

Bridges to:
- SQLite databases (outcomes/failures/company memory)
- ChromaDB collections (read queries)
- Company memory convenience operations
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from feedback.company_memory_store import CompanyMemoryStore
from feedback.failures_store import FailureStore


_WRITE_KEYWORDS = {
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "create",
    "replace",
    "truncate",
    "attach",
    "detach",
    "vacuum",
}


class DatabaseMCPServer:
    """Safe DB access façade for MCP tool exposure."""

    def __init__(self, project_root: str = ".") -> None:
        self.project_root = Path(project_root).resolve()
        self.allowed_roots = [
            (self.project_root / "data").resolve(),
            (self.project_root / "feedback").resolve(),
        ]
        self.failures_store = FailureStore(
            db_path=str((self.project_root / "feedback" / "failures.db").resolve())
        )
        self.company_store = CompanyMemoryStore(
            db_path=str((self.project_root / "feedback" / "company_memory.db").resolve())
        )

    def _resolve_db_path(self, db_path: str) -> Path:
        candidate = (self.project_root / db_path).resolve()
        if candidate.suffix != ".db":
            raise ValueError(f"Only .db files are allowed: {db_path}")
        if not any(str(candidate).startswith(str(root)) for root in self.allowed_roots):
            raise ValueError(f"Database path is outside allowed roots: {db_path}")
        candidate.parent.mkdir(parents=True, exist_ok=True)
        return candidate

    @staticmethod
    def _is_read_only_sql(sql: str) -> bool:
        normalized = (sql or "").strip().lower()
        if not normalized:
            return False

        # First keyword must be SELECT/PRAGMA/WITH
        first_token = re.split(r"\s+", normalized, maxsplit=1)[0]
        if first_token not in {"select", "pragma", "with"}:
            return False

        # Block obvious writes anywhere in query text
        tokens = set(re.findall(r"[a-z_]+", normalized))
        return _WRITE_KEYWORDS.isdisjoint(tokens)

    def list_databases(self) -> list[str]:
        """List available .db files under allowed roots."""
        dbs: list[str] = []
        for root in self.allowed_roots:
            if not root.exists():
                continue
            for path in root.rglob("*.db"):
                dbs.append(str(path.relative_to(self.project_root)).replace("\\", "/"))
        return sorted(dbs)

    def list_tables(self, db_path: str) -> list[str]:
        """List table names in a SQLite DB."""
        resolved = self._resolve_db_path(db_path)
        with sqlite3.connect(str(resolved)) as conn:
            rows = conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                ORDER BY name
                """
            ).fetchall()
        return [str(row[0]) for row in rows]

    def query_sqlite(
        self,
        db_path: str,
        sql: str,
        params: list[Any] | tuple[Any, ...] | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """
        Execute read-only SQL and return rows as dictionaries.
        """
        if not self._is_read_only_sql(sql):
            raise ValueError("Only read-only SELECT/PRAGMA/WITH queries are allowed.")

        resolved = self._resolve_db_path(db_path)
        normalized = (sql or "").strip().lower()
        first_token = re.split(r"\s+", normalized, maxsplit=1)[0]
        if first_token == "pragma":
            effective_sql = sql
        else:
            effective_sql = f"SELECT * FROM ({sql}) LIMIT {max(1, int(limit))}"

        with sqlite3.connect(str(resolved)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(effective_sql, params or []).fetchall()
        return [dict(row) for row in rows]

    def get_failure_patterns(self, limit: int = 10) -> list[dict[str, Any]]:
        """Convenience proxy to FailureStore aggregation."""
        return self.failures_store.top_failure_patterns(limit=limit)

    def list_recent_failures(self, limit: int = 50) -> list[dict[str, Any]]:
        """Convenience proxy to recent failure logs."""
        return self.failures_store.list_recent(limit=limit)

    def cache_company_answer(
        self,
        company_name: str,
        question_key: str,
        question_text: str,
        answer_text: str,
        ats_type: str | None = None,
    ) -> str:
        """Write-through helper for company-memory cached answers."""
        return self.company_store.cache_answer(
            company_name=company_name,
            question_key=question_key,
            question_text=question_text,
            answer_text=answer_text,
            ats_type=ats_type,
        )

    def get_company_answer(
        self,
        company_name: str,
        question_key: str,
    ) -> dict[str, Any] | None:
        """Read cached answer from company memory."""
        return self.company_store.get_cached_answer(
            company_name=company_name,
            question_key=question_key,
        )

    def chroma_query(
        self,
        collection_name: str,
        query_text: str,
        k: int = 5,
        persist_dir: str = "data/chroma",
    ) -> list[dict[str, Any]]:
        """
        Query Chroma collection directly.
        """
        try:
            import chromadb
        except Exception as exc:  # pragma: no cover - dependency/runtime specific
            raise RuntimeError("chromadb runtime is not available.") from exc

        path = (self.project_root / persist_dir).resolve()
        if not str(path).startswith(str((self.project_root / "data").resolve())):
            raise ValueError("persist_dir must be under data/")

        client = chromadb.PersistentClient(path=str(path))
        collection = client.get_collection(collection_name)
        result = collection.query(query_texts=[query_text], n_results=max(1, int(k)))

        ids = (result.get("ids") or [[]])[0]
        docs = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]

        rows: list[dict[str, Any]] = []
        for doc_id, doc, metadata, distance in zip(ids, docs, metadatas, distances):
            rows.append(
                {
                    "id": doc_id,
                    "document": doc,
                    "metadata": metadata,
                    "distance": distance,
                }
            )
        return rows

    def describe(self) -> dict[str, Any]:
        """Return server metadata."""
        return {
            "server": "database_mcp",
            "project_root": str(self.project_root),
            "allowed_roots": [str(p) for p in self.allowed_roots],
            "capabilities": [
                "list_databases",
                "list_tables",
                "query_sqlite_readonly",
                "get_failure_patterns",
                "list_recent_failures",
                "cache_company_answer",
                "get_company_answer",
                "chroma_query",
            ],
        }


def build_fastmcp_app(project_root: str = "."):
    """
    Optional FastMCP app factory.
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception as exc:  # pragma: no cover - depends on runtime package API
        raise RuntimeError(
            "FastMCP runtime not available. Install/upgrade `mcp` package."
        ) from exc

    service = DatabaseMCPServer(project_root=project_root)
    app = FastMCP("job_finder_database")

    @app.tool()
    def list_databases() -> list[str]:
        return service.list_databases()

    @app.tool()
    def list_tables(db_path: str) -> list[str]:
        return service.list_tables(db_path=db_path)

    @app.tool()
    def query_sqlite(
        db_path: str,
        sql: str,
        params: list[Any] | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        return service.query_sqlite(db_path=db_path, sql=sql, params=params, limit=limit)

    @app.tool()
    def get_failure_patterns(limit: int = 10) -> list[dict[str, Any]]:
        return service.get_failure_patterns(limit=limit)

    @app.tool()
    def list_recent_failures(limit: int = 50) -> list[dict[str, Any]]:
        return service.list_recent_failures(limit=limit)

    @app.tool()
    def cache_company_answer(
        company_name: str,
        question_key: str,
        question_text: str,
        answer_text: str,
        ats_type: str | None = None,
    ) -> str:
        return service.cache_company_answer(
            company_name=company_name,
            question_key=question_key,
            question_text=question_text,
            answer_text=answer_text,
            ats_type=ats_type,
        )

    @app.tool()
    def get_company_answer(company_name: str, question_key: str) -> dict[str, Any] | None:
        return service.get_company_answer(company_name=company_name, question_key=question_key)

    return app


if __name__ == "__main__":  # pragma: no cover
    app = build_fastmcp_app()
    app.run()
