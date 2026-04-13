"""Tests for feedback.failures_store."""

from pathlib import Path
from uuid import uuid4

from feedback.failures_store import FailureStore


def _store() -> FailureStore:
    tmp_dir = Path(".tmp")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_dir / f"failures_{uuid4().hex}.db"
    return FailureStore(db_path=str(db_path))


def test_log_failure_and_list_recent():
    store = _store()
    failure_id = store.log_failure(
        application_id="app-1",
        ats_type="greenhouse",
        company="Acme Test Corp",
        failure_step="form_fill",
        error_type="selector_failure",
        error_message="Could not locate field",
        field_name="salary_expectation",
        field_label="Expected Salary",
        selector_strategies=["exact_css", "label_based_xpath", "aria_label_match"],
    )

    rows = store.list_recent(limit=5)
    assert len(rows) == 1
    assert rows[0]["failure_id"] == failure_id
    assert rows[0]["company"] == "Acme Test Corp"
    assert rows[0]["selector_strategies"][0] == "exact_css"


def test_top_failure_patterns_aggregates():
    store = _store()
    for _ in range(3):
        store.log_failure(
            application_id="app-1",
            ats_type="workday",
            company="Acme",
            failure_step="form_fill",
            error_type="dropdown_mismatch",
        )
    store.log_failure(
        application_id="app-2",
        ats_type="greenhouse",
        company="Beta",
        failure_step="upload",
        error_type="file_reject",
    )

    patterns = store.top_failure_patterns(limit=5)
    assert len(patterns) >= 2
    assert patterns[0]["ats_type"] == "workday"
    assert patterns[0]["error_type"] == "dropdown_mismatch"
    assert patterns[0]["count"] == 3
