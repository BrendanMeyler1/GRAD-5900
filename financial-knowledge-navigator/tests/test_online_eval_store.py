from pathlib import Path
from uuid import uuid4

from backend.telemetry.online_eval_store import OnlineEvalStore


def _store_path() -> Path:
    base_dir = Path("tests/apptemp")
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / f"online_eval_{uuid4().hex}.db"


def test_online_eval_store_logs_runs_and_feedback():
    store = OnlineEvalStore(db_path=str(_store_path()))

    run_id = store.log_run(
        {
            "conversation_id": "conv-1",
            "assistant_message_id": "assistant-1",
            "query_text": "How did revenue change?",
            "mode": "file_search",
            "retrieval_backend": "openai_file_search",
            "graph_backend": "sqlite",
            "latency_ms": 120.5,
            "cache_hit": True,
            "retrieved_sources": ["tesla-q4.pdf"],
            "answer_text": "Revenue increased.",
        }
    )

    assert run_id
    runs = store.list_runs(limit=10)
    assert len(runs) == 1
    assert runs[0]["query_text"] == "How did revenue change?"
    assert runs[0]["cache_hit"] is True

    assert store.set_feedback(run_id, score=1, label="helpful")
    feedback_runs = store.list_runs(limit=10, feedback_only=True)

    assert len(feedback_runs) == 1
    assert feedback_runs[0]["feedback_score"] == 1
    assert feedback_runs[0]["feedback_label"] == "helpful"


def test_online_eval_store_summarizes_by_mode():
    store = OnlineEvalStore(db_path=str(_store_path()))

    store.log_run(
        {
            "assistant_message_id": "assistant-1",
            "query_text": "Q1 revenue?",
            "mode": "file_search",
            "latency_ms": 100,
            "cache_hit": True,
            "was_corrected": False,
        }
    )
    second_run_id = store.log_run(
        {
            "assistant_message_id": "assistant-2",
            "query_text": "Q4 margin?",
            "mode": "graphrag",
            "latency_ms": 240,
            "cache_hit": False,
            "was_corrected": True,
        }
    )
    store.set_feedback(second_run_id, score=-1, label="needs_work")

    summary = store.summary(limit=10)
    by_mode = {row["mode"]: row for row in store.summarize_by_mode(limit=10)}

    assert summary["num_runs"] == 2
    assert summary["feedback_count"] == 1
    assert summary["thumbs_down"] == 1
    assert "file_search" in by_mode
    assert "graphrag" in by_mode
    assert by_mode["graphrag"]["correction_rate"] == 1.0
    assert by_mode["file_search"]["cache_hit_rate"] == 1.0


def test_online_eval_store_clear_resets_database():
    db_path = _store_path()
    store = OnlineEvalStore(db_path=str(db_path))
    store.log_run(
        {
            "assistant_message_id": "assistant-1",
            "query_text": "What changed?",
            "mode": "file_search",
        }
    )

    result = store.clear()

    assert result["online_telemetry_reset"] == 1
    assert Path(db_path).exists()
    assert store.list_runs(limit=10) == []
