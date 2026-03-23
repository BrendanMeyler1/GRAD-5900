import gc
from pathlib import Path

from backend.eval.release_workflow import ReleaseWorkflowStore


def _gate_result():
    return {
        "overall_ready": True,
        "best_candidate_mode": "file_search",
        "deployable_modes": ["file_search"],
        "blockers": [],
        "thresholds": {
            "min_combined_overall": 0.6,
            "min_ragas_overall": 0.55,
        },
    }


def test_release_workflow_records_and_summarizes_decisions():
    db_path = Path("tests/.tmp_release_workflow.db")
    reports_dir = Path("tests/.tmp_release_reports")
    if db_path.exists():
        db_path.unlink()
    if reports_dir.exists():
        for child in reports_dir.iterdir():
            child.unlink(missing_ok=True)
        reports_dir.rmdir()

    try:
        store = ReleaseWorkflowStore(db_path=str(db_path), reports_dir=str(reports_dir))
        decision_id = store.record_decision(
            decision="promote",
            gate_result=_gate_result(),
            offline_eval_results={
                "summary": {
                    "file_search": {
                        "average_combined_overall": 0.74,
                        "average_ragas_overall": 0.68,
                    }
                }
            },
            online_summary={"num_runs": 10, "positive_rate": 0.8},
            note="Ready to promote file_search.",
            selected_mode="file_search",
        )

        latest = store.latest_decision()
        summary = store.summary()

        assert latest is not None
        assert latest["decision_id"] == decision_id
        assert latest["decision"] == "promote"
        assert latest["selected_mode"] == "file_search"
        assert summary["total_decisions"] == 1
        assert summary["promotions"] == 1
        assert summary["latest_mode"] == "file_search"
    finally:
        del store
        gc.collect()
        if db_path.exists():
            db_path.unlink(missing_ok=True)
        if reports_dir.exists():
            for child in reports_dir.iterdir():
                child.unlink(missing_ok=True)
            reports_dir.rmdir()


def test_release_workflow_exports_markdown_report():
    db_path = Path("tests/.tmp_release_workflow_report.db")
    reports_dir = Path("tests/.tmp_release_reports_export")
    if db_path.exists():
        db_path.unlink()
    if reports_dir.exists():
        for child in reports_dir.iterdir():
            child.unlink(missing_ok=True)
        reports_dir.rmdir()

    try:
        store = ReleaseWorkflowStore(db_path=str(db_path), reports_dir=str(reports_dir))
        decision_id = store.record_decision(
            decision="hold",
            gate_result={
                **_gate_result(),
                "overall_ready": False,
                "deployable_modes": [],
                "blockers": ["Latency too high."],
            },
            offline_eval_results={"summary": {"graphrag": {"average_combined_overall": 0.58}}},
            online_summary={"num_runs": 4, "avg_latency_ms": 6200},
            note="Hold until latency improves.",
            selected_mode="graphrag",
        )

        report_path = Path(store.export_markdown_report(decision_id))
        text = report_path.read_text(encoding="utf-8")

        assert report_path.exists()
        assert "Deployment Gate Release Decision" in text
        assert "Hold until latency improves." in text
        assert "Latency too high." in text
    finally:
        del store
        gc.collect()
        if db_path.exists():
            db_path.unlink(missing_ok=True)
        if reports_dir.exists():
            for child in reports_dir.iterdir():
                child.unlink(missing_ok=True)
            reports_dir.rmdir()
