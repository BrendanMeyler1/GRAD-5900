"""
Checkpointing — SQLite-based state persistence for LangGraph.

Enables workflow recovery after failures: if a step crashes,
the system can resume from the last checkpoint instead of
restarting the entire pipeline.
"""

import logging
from pathlib import Path

logger = logging.getLogger("job_finder.graph.checkpoints")

# Default checkpoint database path
DEFAULT_CHECKPOINT_PATH = "data/checkpoints.db"


import sqlite3

_checkpointer_instance = None

def get_checkpointer(db_path: str = DEFAULT_CHECKPOINT_PATH):
    """Create a LangGraph SqliteSaver checkpointer.

    Args:
        db_path: Path to the SQLite database for checkpoints.

    Returns:
        A SqliteSaver instance configured for the given path.
    """
    global _checkpointer_instance
    if _checkpointer_instance is not None:
        return _checkpointer_instance

    # Ensure directory exists
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
        conn = sqlite3.connect(db_path, check_same_thread=False)
        _checkpointer_instance = SqliteSaver(conn)
        logger.info(f"Checkpointer initialized: {db_path}")
        return _checkpointer_instance
    except ImportError:
        logger.warning(
            "langgraph checkpoint module not available. "
            "Running without checkpointing."
        )
        return None
