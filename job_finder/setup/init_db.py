"""
Database Initialization — Creates all SQLite databases with schemas.

Creates:
    - pii_vault.db      (handled by PIIVault.__init__)
    - account_vault.db  (accounts table)
    - data/outcomes.db  (applications + status_history tables)
    - feedback/failures.db    (failures table with indexes)
    - feedback/company_memory.db (companies, cached_answers, replay_refs)
    - data/checkpoints.db     (created by LangGraph SqliteSaver)

Run with: python -m setup.init_db

Schemas from Appendix F of the implementation plan.
"""

import os
import sqlite3
import sys
from pathlib import Path

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def _ensure_dir(path: str) -> None:
    """Create parent directories if needed."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def init_account_vault(db_path: str = "account_vault.db") -> None:
    """Create the account vault database (Appendix F.2)."""
    _ensure_dir(db_path)
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            account_id        TEXT PRIMARY KEY,
            company           TEXT NOT NULL,
            ats_type          TEXT NOT NULL,
            username          TEXT NOT NULL,
            password          TEXT NOT NULL,
            session_cookies   TEXT,
            browser_context   TEXT,
            status            TEXT DEFAULT 'active',
            created_at        TEXT NOT NULL,
            last_used_at      TEXT
        );
    """)
    conn.close()
    print(f"  ✓ {db_path}")


def init_outcomes_db(db_path: str = "data/outcomes.db") -> None:
    """Create the outcomes/applications database (Appendix F.3)."""
    _ensure_dir(db_path)
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS applications (
            application_id    TEXT PRIMARY KEY,
            listing_id        TEXT NOT NULL,
            company           TEXT NOT NULL,
            role_title        TEXT NOT NULL,
            ats_type          TEXT NOT NULL,
            fit_score         INTEGER,
            alive_score       REAL,
            status            TEXT NOT NULL,
            resume_version    TEXT,
            cover_letter_ver  TEXT,
            time_to_apply_s   INTEGER,
            human_interventions INTEGER DEFAULT 0,
            submitted_at      TEXT,
            created_at        TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS status_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id  TEXT NOT NULL,
            status          TEXT NOT NULL,
            timestamp       TEXT NOT NULL,
            FOREIGN KEY (application_id) REFERENCES applications(application_id)
        );
    """)
    conn.close()
    print(f"  ✓ {db_path}")


def init_failures_db(db_path: str = "feedback/failures.db") -> None:
    """Create the failures database (Appendix F.4)."""
    _ensure_dir(db_path)
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS failures (
            failure_id              TEXT PRIMARY KEY,
            application_id          TEXT,
            ats_type                TEXT NOT NULL,
            company                 TEXT NOT NULL,
            failure_step            TEXT NOT NULL,
            error_type              TEXT NOT NULL,
            field_name              TEXT,
            field_label             TEXT,
            selector_strategies     TEXT,
            strategy_that_worked    TEXT,
            fix_applied             TEXT,
            error_message           TEXT,
            timestamp               TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_failures_ats_error
            ON failures(ats_type, error_type);
        CREATE INDEX IF NOT EXISTS idx_failures_step
            ON failures(failure_step);
    """)
    conn.close()
    print(f"  ✓ {db_path}")


def init_company_memory_db(db_path: str = "feedback/company_memory.db") -> None:
    """Create the company memory database (Appendix F.5)."""
    _ensure_dir(db_path)
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS companies (
            company_id       TEXT PRIMARY KEY,
            company_name     TEXT NOT NULL,
            ats_type         TEXT,
            field_patterns   TEXT,
            last_applied     TEXT,
            last_outcome     TEXT,
            created_at       TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS cached_answers (
            answer_id       TEXT PRIMARY KEY,
            company_id      TEXT NOT NULL,
            question_key    TEXT NOT NULL,
            question_text   TEXT NOT NULL,
            answer_text     TEXT NOT NULL,
            used_count      INTEGER DEFAULT 1,
            last_used       TEXT NOT NULL,
            FOREIGN KEY (company_id) REFERENCES companies(company_id)
        );

        CREATE TABLE IF NOT EXISTS replay_refs (
            company_id      TEXT NOT NULL,
            trace_id        TEXT NOT NULL,
            FOREIGN KEY (company_id) REFERENCES companies(company_id)
        );

        CREATE INDEX IF NOT EXISTS idx_cached_answers_company_question
            ON cached_answers(company_id, question_key);
        CREATE INDEX IF NOT EXISTS idx_replay_refs_company
            ON replay_refs(company_id);
    """)
    conn.close()
    print(f"  ✓ {db_path}")


def init_checkpoints_db(db_path: str = "data/checkpoints.db") -> None:
    """Create placeholder for LangGraph checkpoints database."""
    _ensure_dir(db_path)
    # LangGraph's SqliteSaver creates its own schema; just ensure the file exists
    conn = sqlite3.connect(db_path)
    conn.close()
    print(f"  ✓ {db_path} (placeholder — LangGraph manages schema)")


def init_all() -> None:
    """Initialize all databases."""
    print("Initializing job_finder databases...")
    print()

    # Ensure data directories exist
    os.makedirs("data/raw", exist_ok=True)
    os.makedirs("data/processed", exist_ok=True)
    os.makedirs("data/chroma", exist_ok=True)
    os.makedirs("feedback", exist_ok=True)
    os.makedirs("replay/traces", exist_ok=True)

    # Note: pii_vault.db is created by PIIVault.__init__() which requires
    # the encryption key — it self-initializes on first use.
    print("  ℹ pii_vault.db — created on first use by PIIVault (requires PII_VAULT_KEY)")

    init_account_vault()
    init_outcomes_db()
    init_failures_db()
    init_company_memory_db()
    init_checkpoints_db()

    print()
    print("✅ All databases initialized successfully.")
    print()
    print("Next steps:")
    print("  1. Copy .env.example to .env and fill in your API keys")
    print("  2. Set PII_VAULT_KEY (generate with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\")")
    print("  3. Run the API: uvicorn api.main:app --reload --port 8000")


if __name__ == "__main__":
    # Change to project root so relative paths work
    os.chdir(project_root)
    init_all()
