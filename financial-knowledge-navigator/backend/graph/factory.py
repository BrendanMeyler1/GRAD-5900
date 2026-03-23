from typing import Optional

from backend.core.config import settings
from backend.graph.base import GraphStore
from backend.graph.neo4j_store import Neo4jGraphStore
from backend.graph.sqlite_store import SQLiteGraphStore


def create_graph_store(
    backend: Optional[str] = None,
    **overrides,
) -> GraphStore:
    """
    Create the configured graph-store backend.

    Defaults to SQLite so the app keeps working out of the box, while Neo4j
    can be enabled from config without changing the rest of the codebase.
    """

    selected_backend = (backend or settings.graph_backend or "sqlite").strip().lower()

    if selected_backend == "sqlite":
        db_path = overrides.get("db_path", settings.graph_db_path)
        return SQLiteGraphStore(db_path)

    if selected_backend == "neo4j":
        return Neo4jGraphStore(
            uri=overrides.get("uri", settings.neo4j_uri),
            username=overrides.get("username", settings.neo4j_username),
            password=overrides.get("password", settings.neo4j_password),
            database=overrides.get("database", settings.neo4j_database),
            driver=overrides.get("driver"),
        )

    raise ValueError(
        f"Unsupported GRAPH_BACKEND '{selected_backend}'. Expected 'sqlite' or 'neo4j'."
    )
