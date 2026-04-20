"""
db/schema.py — SQLite schema definitions and database initialization.

All CREATE TABLE statements live here. Call init_db() once at startup
(idempotent — safe to call on every server start via IF NOT EXISTS).

Design choices:
- Single-user app: user_profile has id=1 always.
- JSON columns store structured data that doesn't need to be queried by field.
- All timestamps are ISO-8601 UTC strings (no timezone drama with SQLite).
- Foreign keys are declared but SQLite enforcement must be enabled per-connection
  via PRAGMA foreign_keys = ON.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


# ─── Table definitions ────────────────────────────────────────────────────────

_CREATE_USER_PROFILE = """
CREATE TABLE IF NOT EXISTS user_profile (
    id                    INTEGER PRIMARY KEY DEFAULT 1,
    -- Personal
    first_name            TEXT,
    last_name             TEXT,
    email                 TEXT,          -- Fernet-encrypted at rest
    phone                 TEXT,          -- Fernet-encrypted at rest
    address               TEXT,          -- Fernet-encrypted at rest
    city                  TEXT,
    state                 TEXT,
    zip                   TEXT,
    country               TEXT DEFAULT 'US',
    -- Professional links
    linkedin_url          TEXT,
    github_url            TEXT,
    portfolio_url         TEXT,
    -- Work authorisation
    authorized_to_work    INTEGER DEFAULT 1,   -- boolean
    requires_sponsorship  INTEGER DEFAULT 0,   -- boolean
    visa_status           TEXT,
    -- Job preferences
    target_salary_min     INTEGER,
    target_salary_max     INTEGER,
    remote_preference     TEXT,   -- 'remote'|'hybrid'|'onsite'|'flexible'
    willing_to_relocate   INTEGER DEFAULT 0,
    availability_weeks    INTEGER DEFAULT 2,
    -- EEO (user-controlled; stored only if user explicitly provides)
    gender                TEXT,
    race_ethnicity        TEXT,
    veteran_status        TEXT,
    disability_status     TEXT,
    -- Documents
    resume_raw_text       TEXT,
    resume_file_path      TEXT,
    -- Profile completeness notes from chat
    conversation_notes    TEXT,
    updated_at            TEXT DEFAULT (datetime('now'))
)
"""

_CREATE_EDUCATION = """
CREATE TABLE IF NOT EXISTS education (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    institution           TEXT NOT NULL,
    degree                TEXT,
    field                 TEXT,
    graduation_year       INTEGER,
    gpa                   REAL,
    relevant_coursework   TEXT,
    created_at            TEXT DEFAULT (datetime('now'))
)
"""

_CREATE_WORK_EXPERIENCE = """
CREATE TABLE IF NOT EXISTS work_experience (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    company               TEXT NOT NULL,
    title                 TEXT,
    employment_type       TEXT,   -- 'full_time'|'part_time'|'contract'|'internship'
    start_date            TEXT,
    end_date              TEXT,
    is_current            INTEGER DEFAULT 0,
    description           TEXT,
    achievements          TEXT,
    created_at            TEXT DEFAULT (datetime('now'))
)
"""

_CREATE_SKILLS = """
CREATE TABLE IF NOT EXISTS skills (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    name                  TEXT NOT NULL,
    category              TEXT,   -- 'technical'|'soft'|'language'|'certification'
    proficiency           TEXT,   -- 'beginner'|'intermediate'|'expert'
    UNIQUE(name)
)
"""

_CREATE_USER_QA = """
CREATE TABLE IF NOT EXISTS user_qa (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    question              TEXT NOT NULL,
    answer                TEXT,
    category              TEXT,   -- 'preference'|'experience'|'background'|'other'
    created_at            TEXT DEFAULT (datetime('now'))
)
"""

_CREATE_JOB_LISTINGS = """
CREATE TABLE IF NOT EXISTS job_listings (
    id                    TEXT PRIMARY KEY,
    source                TEXT NOT NULL,   -- 'jsearch'|'greenhouse'|'lever'|'manual'
    ats_type              TEXT DEFAULT 'universal',   -- detected from apply_url domain
    title                 TEXT,
    company               TEXT,
    location              TEXT,
    remote_ok             INTEGER DEFAULT 0,
    description           TEXT,
    apply_url             TEXT,
    posted_at             TEXT,
    -- Fit assessment (computed asynchronously after scraping)
    fit_score             REAL,
    fit_summary           TEXT,
    fit_strengths         TEXT,   -- JSON array of strings
    fit_gaps              TEXT,   -- JSON array of strings
    interview_likelihood  TEXT,   -- 'low'|'medium'|'medium-high'|'high'
    -- Workflow state
    status                TEXT DEFAULT 'new',
    -- 'new'|'queued'|'shadow_pending'|'reviewing'|'applied'|'skipped'
    created_at            TEXT DEFAULT (datetime('now'))
)
"""

_CREATE_APPLICATIONS = """
CREATE TABLE IF NOT EXISTS applications (
    id                    TEXT PRIMARY KEY,
    job_id                TEXT NOT NULL,
    status                TEXT NOT NULL DEFAULT 'pending',
    -- 'pending'|'shadow_running'|'shadow_review'|'awaiting_approval'
    -- |'submitted'|'failed'|'aborted'
    -- Documents
    resume_tailored_text  TEXT,
    resume_tailored_path  TEXT,
    cover_letter_text     TEXT,
    -- Form fill results
    shadow_screenshots    TEXT,   -- JSON array of file paths
    fill_log              TEXT,   -- JSON array of Stagehand action log dicts
    custom_qa             TEXT,   -- JSON object: {question: answer}
    -- Human review
    human_notes           TEXT,
    -- Outcome
    submitted_at          TEXT,
    created_at            TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (job_id) REFERENCES job_listings(id)
)
"""

_CREATE_CHAT_MESSAGES = """
CREATE TABLE IF NOT EXISTS chat_messages (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    role                  TEXT NOT NULL,   -- 'user'|'assistant'
    content               TEXT NOT NULL,
    context_type          TEXT,   -- 'general'|'job'|'apply'
    context_id            TEXT,   -- job_id or app_id
    created_at            TEXT DEFAULT (datetime('now'))
)
"""

_CREATE_CONVERSATION_SUMMARY = """
CREATE TABLE IF NOT EXISTS conversation_summary (
    id                    INTEGER PRIMARY KEY DEFAULT 1,
    summary               TEXT,
    message_count_at_last INTEGER DEFAULT 0,
    last_updated          TEXT
)
"""

_CREATE_APPLICATION_MEMORY = """
CREATE TABLE IF NOT EXISTS application_memory (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    company               TEXT NOT NULL,
    ats_type              TEXT,
    what_worked           TEXT,
    what_failed           TEXT,
    form_notes            TEXT,   -- e.g. "cover letter is a text box, not file upload"
    created_at            TEXT DEFAULT (datetime('now')),
    UNIQUE(company)
)
"""

_CREATE_EMAIL_EVENTS = """
CREATE TABLE IF NOT EXISTS email_events (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id                TEXT,
    company               TEXT,
    subject               TEXT,
    sender                TEXT,
    received_at           TEXT,
    category              TEXT,
    -- 'interview_request'|'rejection'|'offer'|'followup_needed'|'auto_reply'|'unknown'
    summary               TEXT,
    action_needed         INTEGER DEFAULT 0,
    urgency               TEXT,   -- 'low'|'medium'|'high'
    key_details           TEXT,
    raw_snippet           TEXT,
    created_at            TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (app_id) REFERENCES applications(id)
)
"""

_CREATE_SCRAPE_RUNS = """
CREATE TABLE IF NOT EXISTS scrape_runs (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    source                TEXT,
    query                 TEXT,
    location              TEXT,
    results_count         INTEGER DEFAULT 0,
    ran_at                TEXT DEFAULT (datetime('now'))
)
"""

_ALL_TABLES = [
    _CREATE_USER_PROFILE,
    _CREATE_EDUCATION,
    _CREATE_WORK_EXPERIENCE,
    _CREATE_SKILLS,
    _CREATE_USER_QA,
    _CREATE_JOB_LISTINGS,
    _CREATE_APPLICATIONS,
    _CREATE_CHAT_MESSAGES,
    _CREATE_CONVERSATION_SUMMARY,
    _CREATE_APPLICATION_MEMORY,
    _CREATE_EMAIL_EVENTS,
    _CREATE_SCRAPE_RUNS,
]

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_jobs_status ON job_listings(status)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_fit_score ON job_listings(fit_score)",
    "CREATE INDEX IF NOT EXISTS idx_apps_job_id ON applications(job_id)",
    "CREATE INDEX IF NOT EXISTS idx_apps_status ON applications(status)",
    "CREATE INDEX IF NOT EXISTS idx_email_events_app_id ON email_events(app_id)",
    "CREATE INDEX IF NOT EXISTS idx_chat_created ON chat_messages(created_at)",
]


def init_db(db_path: str | Path) -> None:
    """
    Create all tables and indexes in the SQLite database.

    Idempotent — safe to call on every server start. Uses IF NOT EXISTS
    so existing data is never affected.

    Args:
        db_path: Path to the SQLite database file. Created if absent.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")   # better concurrent read performance
        conn.execute("PRAGMA synchronous = NORMAL")  # safe + fast

        for stmt in _ALL_TABLES:
            conn.execute(stmt)

        for stmt in _INDEXES:
            conn.execute(stmt)

        conn.commit()
