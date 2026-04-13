"""Schema tests for Step 6 feedback DB initialization."""

import sqlite3
from pathlib import Path
from uuid import uuid4

from setup.init_db import init_company_memory_db, init_failures_db


def _tables_and_indexes(db_path: Path) -> tuple[set[str], set[str]]:
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT type, name FROM sqlite_master WHERE type IN ('table', 'index')"
        ).fetchall()
    tables = {name for type_, name in rows if type_ == "table"}
    indexes = {name for type_, name in rows if type_ == "index"}
    return tables, indexes


def test_init_failures_db_creates_schema_and_indexes():
    tmp_dir = Path(".tmp")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_dir / f"failures_init_{uuid4().hex}.db"

    init_failures_db(db_path=str(db_path))
    tables, indexes = _tables_and_indexes(db_path)

    assert "failures" in tables
    assert "idx_failures_ats_error" in indexes
    assert "idx_failures_step" in indexes


def test_init_company_memory_db_creates_schema_and_indexes():
    tmp_dir = Path(".tmp")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_dir / f"company_memory_init_{uuid4().hex}.db"

    init_company_memory_db(db_path=str(db_path))
    tables, indexes = _tables_and_indexes(db_path)

    assert "companies" in tables
    assert "cached_answers" in tables
    assert "replay_refs" in tables
    assert "idx_cached_answers_company_question" in indexes
    assert "idx_replay_refs_company" in indexes
