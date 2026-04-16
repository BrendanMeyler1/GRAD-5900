"""
setup/init_db.py — First-run database initialization.

Usage:
    python -m setup.init_db

Creates data/v2.db with all tables and indexes. Idempotent — safe to re-run.
Also ensures data/, data/resumes/, data/generated/, data/screenshots/,
data/logs/ directories exist.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from config import settings
from db.schema import init_db


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("setup.init_db")

    # Ensure data directories exist
    dirs = [
        settings.data_dir,
        settings.resumes_dir,
        settings.generated_dir,
        settings.screenshots_dir,
        settings.logs_dir,
    ]
    for d in dirs:
        Path(d).mkdir(parents=True, exist_ok=True)
        log.info("directory ready: %s", d)

    # Initialise schema
    db_path = Path(settings.db_path)
    init_db(db_path)
    log.info("database ready: %s", db_path)

    log.info("init_db complete — run `python -m setup.seed` for demo data")
    return 0


if __name__ == "__main__":
    sys.exit(main())
