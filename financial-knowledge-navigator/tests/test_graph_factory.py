import gc
from pathlib import Path

import pytest

from backend.core.config import settings
from backend.graph.factory import create_graph_store
from backend.graph.neo4j_store import Neo4jGraphStore
from backend.graph.sqlite_store import SQLiteGraphStore


class FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def run(self, query, **params):
        return []


class FakeDriver:
    def __init__(self):
        self.closed = False

    def session(self, database=None):
        return FakeSession()

    def close(self):
        self.closed = True


def test_graph_factory_returns_sqlite_store_when_configured(monkeypatch):
    db_path = Path("tests/.tmp_factory_graph.db")
    if db_path.exists():
        db_path.unlink()

    monkeypatch.setattr(settings, "graph_backend", "sqlite")

    try:
        store = create_graph_store(db_path=str(db_path))
        assert isinstance(store, SQLiteGraphStore)
    finally:
        del store
        gc.collect()
        db_path.unlink(missing_ok=True)


def test_graph_factory_returns_neo4j_store_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "graph_backend", "neo4j")

    store = create_graph_store(driver=FakeDriver())

    assert isinstance(store, Neo4jGraphStore)
    assert store.is_configured() is True
    store.close()


def test_graph_factory_rejects_unknown_backend():
    with pytest.raises(ValueError):
        create_graph_store(backend="mystery")
