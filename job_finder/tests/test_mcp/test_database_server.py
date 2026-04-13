"""Tests for mcp_servers.database_server."""

from pathlib import Path
from uuid import uuid4

import pytest

from mcp_servers.database_server import DatabaseMCPServer


def _server() -> DatabaseMCPServer:
    root = Path(".tmp") / f"mcp_db_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    return DatabaseMCPServer(project_root=str(root))


def test_list_databases_and_tables():
    server = _server()
    dbs = server.list_databases()

    assert "feedback/failures.db" in dbs
    assert "feedback/company_memory.db" in dbs

    tables = server.list_tables("feedback/failures.db")
    assert "failures" in tables


def test_query_sqlite_readonly_and_block_write():
    server = _server()
    server.failures_store.log_failure(
        application_id="app-1",
        ats_type="greenhouse",
        company="Acme",
        failure_step="fill",
        error_type="selector_failure",
    )

    rows = server.query_sqlite(
        db_path="feedback/failures.db",
        sql="SELECT company, error_type FROM failures",
    )
    assert len(rows) == 1
    assert rows[0]["company"] == "Acme"

    with pytest.raises(ValueError):
        server.query_sqlite(
            db_path="feedback/failures.db",
            sql="UPDATE failures SET company = 'X'",
        )


def test_failure_patterns_and_company_memory_helpers():
    server = _server()
    for _ in range(2):
        server.failures_store.log_failure(
            application_id="app-1",
            ats_type="workday",
            company="Acme",
            failure_step="form_fill",
            error_type="dropdown_mismatch",
        )

    patterns = server.get_failure_patterns(limit=5)
    assert patterns[0]["ats_type"] == "workday"
    assert patterns[0]["count"] == 2

    answer_id = server.cache_company_answer(
        company_name="Acme",
        question_key="why_work_here",
        question_text="Why Acme?",
        answer_text="Strong alignment with platform scale.",
        ats_type="greenhouse",
    )
    assert answer_id

    cached = server.get_company_answer(company_name="Acme", question_key="why_work_here")
    assert cached is not None
    assert "alignment" in cached["answer_text"]
