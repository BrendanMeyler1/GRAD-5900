"""
db/store.py — All database access for job_finder_v2.

The Store class is the single source of truth for reading and writing data.
No raw SQL exists outside this file. Every public method is documented and
returns typed Pydantic models.

Thread safety:
    SQLite connections are NOT shared across threads. In FastAPI's async
    context, each request opens its own connection via get_db() dependency.
    Use Store(db_path) within a single request lifecycle.

Usage:
    from db.store import Store
    store = Store(settings.db_path, encryptor)
    profile = store.get_profile()
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from db.encryption import FieldEncryptor

log = logging.getLogger(__name__)

# ─── Pydantic models ──────────────────────────────────────────────────────────


class UserProfile(BaseModel):
    """Top-level user profile fields (personal info, preferences)."""

    id: int = 1
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str | None = None
    country: str = "US"
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None
    authorized_to_work: bool = True
    requires_sponsorship: bool = False
    visa_status: str | None = None
    target_salary_min: int | None = None
    target_salary_max: int | None = None
    remote_preference: str | None = None
    willing_to_relocate: bool = False
    availability_weeks: int = 2
    gender: str | None = None
    race_ethnicity: str | None = None
    veteran_status: str | None = None
    disability_status: str | None = None
    resume_raw_text: str | None = None
    resume_file_path: str | None = None
    conversation_notes: str | None = None
    updated_at: str | None = None

    @property
    def full_name(self) -> str:
        parts = [p for p in (self.first_name, self.last_name) if p]
        return " ".join(parts)

    @property
    def completion_pct(self) -> int:
        """Rough profile completeness 0–100 based on fields that matter for applications."""
        fields = [
            self.first_name, self.last_name, self.email, self.phone,
            self.city, self.state, self.resume_raw_text,
            self.authorized_to_work is not None,
            self.target_salary_min is not None,
            self.remote_preference,
        ]
        filled = sum(1 for f in fields if f)
        return int((filled / len(fields)) * 100)

    def is_complete_enough(self) -> bool:
        """True if profile has the minimum fields needed to submit an application."""
        return bool(self.first_name and self.last_name and self.email and self.phone)


class Education(BaseModel):
    id: int | None = None
    institution: str
    degree: str | None = None
    field: str | None = None
    graduation_year: int | None = None
    gpa: float | None = None
    relevant_coursework: str | None = None
    created_at: str | None = None


class WorkExperience(BaseModel):
    id: int | None = None
    company: str
    title: str | None = None
    employment_type: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    is_current: bool = False
    description: str | None = None
    achievements: str | None = None
    created_at: str | None = None


class Skill(BaseModel):
    id: int | None = None
    name: str
    category: str | None = None
    proficiency: str | None = None


class QA(BaseModel):
    id: int | None = None
    question: str
    answer: str | None = None
    category: str | None = None
    created_at: str | None = None


class FullProfile(BaseModel):
    """All profile data merged into one object — used by agents and context injection."""

    profile: UserProfile = Field(default_factory=UserProfile)
    education: list[Education] = Field(default_factory=list)
    experience: list[WorkExperience] = Field(default_factory=list)
    skills: list[Skill] = Field(default_factory=list)
    qa: list[QA] = Field(default_factory=list)

    @property
    def skill_names(self) -> list[str]:
        return [s.name for s in self.skills]

    @property
    def is_complete_enough(self) -> bool:
        return self.profile.is_complete_enough()

    @property
    def completion_pct(self) -> int:
        return self.profile.completion_pct

    def to_context_string(self) -> str:
        """Compact text representation for injecting into LLM system prompts."""
        p = self.profile
        lines = [
            f"Name: {p.full_name}",
            f"Email: {p.email or 'not set'}",
            f"Phone: {p.phone or 'not set'}",
            f"Location: {', '.join(filter(None, [p.city, p.state, p.country]))}",
        ]
        if p.linkedin_url:
            lines.append(f"LinkedIn: {p.linkedin_url}")
        if p.github_url:
            lines.append(f"GitHub: {p.github_url}")
        if self.education:
            lines.append("\nEDUCATION:")
            for e in self.education:
                gpa = f", GPA {e.gpa}" if e.gpa else ""
                lines.append(f"  {e.degree or 'Degree'} in {e.field or 'N/A'}, {e.institution} ({e.graduation_year or 'N/A'}){gpa}")
        if self.experience:
            lines.append("\nEXPERIENCE:")
            for x in self.experience[:5]:  # cap for token efficiency
                current = " (current)" if x.is_current else f"– {x.end_date or 'present'}"
                lines.append(f"  {x.title} at {x.company} | {x.start_date} {current}")
                if x.description:
                    lines.append(f"    {x.description[:200]}")
        if self.skills:
            lines.append(f"\nSKILLS: {', '.join(self.skill_names[:30])}")
        if p.target_salary_min and p.target_salary_max:
            lines.append(f"\nSalary target: ${p.target_salary_min:,}–${p.target_salary_max:,}")
        if p.remote_preference:
            lines.append(f"Remote preference: {p.remote_preference}")
        lines.append(f"Work authorization: {'Yes, no sponsorship needed' if not p.requires_sponsorship else 'Requires sponsorship'}")
        if self.qa:
            lines.append("\nADDITIONAL CONTEXT:")
            for q in self.qa[-10:]:
                lines.append(f"  Q: {q.question}")
                lines.append(f"  A: {q.answer}")
        return "\n".join(lines)


class JobFilters(BaseModel):
    """Filters for job listing queries."""

    status: str | None = None
    source: str | None = None
    min_fit_score: float | None = None
    remote_only: bool = False
    limit: int = 50
    offset: int = 0
    sort_by: str = "created_at"  # 'created_at'|'fit_score'|'posted_at'


class JobListing(BaseModel):
    id: str
    source: str
    ats_type: str = "universal"
    title: str | None = None
    company: str | None = None
    location: str | None = None
    remote_ok: bool = False
    description: str | None = None
    apply_url: str | None = None
    posted_at: str | None = None
    fit_score: float | None = None
    fit_summary: str | None = None
    fit_strengths: list[str] = Field(default_factory=list)
    fit_gaps: list[str] = Field(default_factory=list)
    interview_likelihood: str | None = None
    status: str = "new"
    created_at: str | None = None


class Application(BaseModel):
    id: str
    job_id: str
    status: str = "pending"
    resume_tailored_text: str | None = None
    resume_tailored_path: str | None = None
    cover_letter_text: str | None = None
    shadow_screenshots: list[str] = Field(default_factory=list)
    fill_log: list[dict] = Field(default_factory=list)
    custom_qa: dict[str, str] = Field(default_factory=dict)
    human_notes: str | None = None
    submitted_at: str | None = None
    created_at: str | None = None

    # Populated by join in get_application_with_job
    job: JobListing | None = None


class ChatMessage(BaseModel):
    id: int | None = None
    role: str
    content: str
    context_type: str | None = None
    context_id: str | None = None
    created_at: str | None = None


class AppMemory(BaseModel):
    id: int | None = None
    company: str
    ats_type: str | None = None
    what_worked: str | None = None
    what_failed: str | None = None
    form_notes: str | None = None
    created_at: str | None = None


class EmailEvent(BaseModel):
    id: int | None = None
    app_id: str | None = None
    company: str | None = None
    subject: str | None = None
    sender: str | None = None
    received_at: str | None = None
    category: str | None = None
    summary: str | None = None
    action_needed: bool = False
    urgency: str | None = None
    key_details: str | None = None
    raw_snippet: str | None = None
    created_at: str | None = None


# ─── Store ────────────────────────────────────────────────────────────────────


def _now() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


class Store:
    """
    All database read/write operations.

    Opens a SQLite connection per instance. Intended to be created per-request
    in FastAPI via the get_db() dependency injection pattern.

    Args:
        db_path: Path to the SQLite database file.
        encryptor: FieldEncryptor for sensitive field handling.
    """

    def __init__(self, db_path: str | Path, encryptor: FieldEncryptor) -> None:
        self._path = str(db_path)
        self._enc = encryptor
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            # check_same_thread=False: safe for our single-user, single-writer
            # app where WAL mode provides read concurrency.
            self._conn = sqlite3.connect(self._path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA journal_mode = WAL")
        return self._conn

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── Profile ───────────────────────────────────────────────────────────────

    def get_profile(self) -> UserProfile | None:
        """Return the single user profile record, decrypting sensitive fields."""
        row = self._get_conn().execute(
            "SELECT * FROM user_profile WHERE id = 1"
        ).fetchone()
        if not row:
            return None
        data = self._enc.decrypt_dict(_row_to_dict(row))
        return UserProfile(**data)

    def upsert_profile(self, data: dict) -> UserProfile:
        """
        Insert or update the user profile. Encrypts sensitive fields.

        Args:
            data: Dict of profile fields to set. Partial updates are fine.

        Returns:
            Updated UserProfile.
        """
        data["id"] = 1
        data["updated_at"] = _now()
        encrypted = self._enc.encrypt_dict(data)

        conn = self._get_conn()
        existing = conn.execute("SELECT id FROM user_profile WHERE id = 1").fetchone()

        if existing:
            cols = [k for k in encrypted if k != "id"]
            set_clause = ", ".join(f"{c} = ?" for c in cols)
            vals = [encrypted[c] for c in cols] + [1]
            conn.execute(f"UPDATE user_profile SET {set_clause} WHERE id = ?", vals)
        else:
            cols = list(encrypted.keys())
            placeholders = ", ".join("?" for _ in cols)
            col_names = ", ".join(cols)
            conn.execute(
                f"INSERT INTO user_profile ({col_names}) VALUES ({placeholders})",
                [encrypted[c] for c in cols],
            )

        conn.commit()
        return self.get_profile()  # type: ignore[return-value]

    def get_education(self) -> list[Education]:
        """Return all education records, most recent first."""
        rows = self._get_conn().execute(
            "SELECT * FROM education ORDER BY graduation_year DESC"
        ).fetchall()
        return [Education(**_row_to_dict(r)) for r in rows]

    def add_education(self, data: dict) -> Education:
        """Insert a new education record. Returns the created record."""
        conn = self._get_conn()
        cur = conn.execute(
            """INSERT INTO education (institution, degree, field, graduation_year, gpa, relevant_coursework)
               VALUES (:institution, :degree, :field, :graduation_year, :gpa, :relevant_coursework)""",
            {
                "institution": data.get("institution", ""),
                "degree": data.get("degree"),
                "field": data.get("field"),
                "graduation_year": data.get("graduation_year"),
                "gpa": data.get("gpa"),
                "relevant_coursework": data.get("relevant_coursework"),
            },
        )
        conn.commit()
        row = conn.execute("SELECT * FROM education WHERE id = ?", (cur.lastrowid,)).fetchone()
        return Education(**_row_to_dict(row))

    def get_experience(self) -> list[WorkExperience]:
        """Return all work experience records, most recent first."""
        rows = self._get_conn().execute(
            "SELECT * FROM work_experience ORDER BY is_current DESC, start_date DESC"
        ).fetchall()
        return [WorkExperience(**{**_row_to_dict(r), "is_current": bool(_row_to_dict(r)["is_current"])}) for r in rows]

    def add_experience(self, data: dict) -> WorkExperience:
        """Insert a new work experience record. Returns the created record."""
        conn = self._get_conn()
        cur = conn.execute(
            """INSERT INTO work_experience
               (company, title, employment_type, start_date, end_date, is_current, description, achievements)
               VALUES (:company, :title, :employment_type, :start_date, :end_date,
                       :is_current, :description, :achievements)""",
            {
                "company": data.get("company", ""),
                "title": data.get("title"),
                "employment_type": data.get("employment_type"),
                "start_date": data.get("start_date"),
                "end_date": data.get("end_date"),
                "is_current": int(data.get("is_current", False)),
                "description": data.get("description"),
                "achievements": data.get("achievements"),
            },
        )
        conn.commit()
        row = conn.execute("SELECT * FROM work_experience WHERE id = ?", (cur.lastrowid,)).fetchone()
        d = _row_to_dict(row)
        d["is_current"] = bool(d["is_current"])
        return WorkExperience(**d)

    def get_skills(self) -> list[Skill]:
        """Return all skills."""
        rows = self._get_conn().execute("SELECT * FROM skills ORDER BY name").fetchall()
        return [Skill(**_row_to_dict(r)) for r in rows]

    def upsert_skills(self, skills: list[dict]) -> list[Skill]:
        """
        Insert or update a batch of skills. Deduplicates by name.

        Args:
            skills: List of dicts with keys: name, category (optional), proficiency (optional).

        Returns:
            All skills after upsert.
        """
        conn = self._get_conn()
        for skill in skills:
            conn.execute(
                """INSERT INTO skills (name, category, proficiency) VALUES (?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET
                       category = excluded.category,
                       proficiency = excluded.proficiency""",
                (skill.get("name", ""), skill.get("category"), skill.get("proficiency")),
            )
        conn.commit()
        return self.get_skills()

    def add_qa(self, question: str, answer: str, category: str | None = None) -> QA:
        """Store a question-answer pair from profile conversation."""
        conn = self._get_conn()
        cur = conn.execute(
            "INSERT INTO user_qa (question, answer, category) VALUES (?, ?, ?)",
            (question, answer, category),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM user_qa WHERE id = ?", (cur.lastrowid,)).fetchone()
        return QA(**_row_to_dict(row))

    def get_qa(self, category: str | None = None) -> list[QA]:
        """Return Q&A notes, optionally filtered by category."""
        if category:
            rows = self._get_conn().execute(
                "SELECT * FROM user_qa WHERE category = ? ORDER BY created_at DESC",
                (category,),
            ).fetchall()
        else:
            rows = self._get_conn().execute(
                "SELECT * FROM user_qa ORDER BY created_at DESC"
            ).fetchall()
        return [QA(**_row_to_dict(r)) for r in rows]

    def get_full_profile(self) -> FullProfile:
        """
        Return all profile data merged into a FullProfile.

        This is the primary input for agents (resume_writer, fit_scorer, etc.)
        and the chat context injection.
        """
        return FullProfile(
            profile=self.get_profile() or UserProfile(),
            education=self.get_education(),
            experience=self.get_experience(),
            skills=self.get_skills(),
            qa=self.get_qa(),
        )

    # ── Jobs ──────────────────────────────────────────────────────────────────

    def upsert_job(self, data: dict) -> JobListing:
        """
        Insert or update a job listing. Generates an ID if absent.

        Args:
            data: Dict matching JobListing fields.

        Returns:
            Created or updated JobListing.
        """
        if "id" not in data or not data["id"]:
            data["id"] = str(uuid.uuid4())

        # Serialize list fields to JSON strings
        data["fit_strengths"] = json.dumps(data.get("fit_strengths", []))
        data["fit_gaps"] = json.dumps(data.get("fit_gaps", []))

        cols = [
            "id", "source", "ats_type", "title", "company", "location", "remote_ok",
            "description", "apply_url", "posted_at", "fit_score", "fit_summary",
            "fit_strengths", "fit_gaps", "interview_likelihood", "status",
        ]
        conn = self._get_conn()
        placeholders = ", ".join("?" for _ in cols)
        col_names = ", ".join(cols)
        update_clause = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "id")

        conn.execute(
            f"""INSERT INTO job_listings ({col_names}) VALUES ({placeholders})
                ON CONFLICT(id) DO UPDATE SET {update_clause}""",
            [data.get(c) for c in cols],
        )
        conn.commit()
        return self.get_job(data["id"])  # type: ignore[return-value]

    def get_job(self, job_id: str) -> JobListing | None:
        """Return a single job listing by ID."""
        row = self._get_conn().execute(
            "SELECT * FROM job_listings WHERE id = ?", (job_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_job(row)

    def get_jobs(self, filters: JobFilters | None = None) -> list[JobListing]:
        """
        Return job listings with optional filtering and sorting.

        Args:
            filters: JobFilters instance. Defaults to no filters, limit=50.

        Returns:
            Sorted list of JobListing.
        """
        f = filters or JobFilters()
        conditions: list[str] = []
        params: list[Any] = []

        if f.status:
            conditions.append("status = ?")
            params.append(f.status)
        if f.source:
            conditions.append("source = ?")
            params.append(f.source)
        if f.min_fit_score is not None:
            conditions.append("fit_score >= ?")
            params.append(f.min_fit_score)
        if f.remote_only:
            conditions.append("remote_ok = 1")

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        order = f.sort_by if f.sort_by in {"created_at", "fit_score", "posted_at"} else "created_at"
        query = f"SELECT * FROM job_listings {where} ORDER BY {order} DESC LIMIT ? OFFSET ?"
        params.extend([f.limit, f.offset])

        rows = self._get_conn().execute(query, params).fetchall()
        return [self._row_to_job(r) for r in rows]

    def update_job_status(self, job_id: str, status: str) -> None:
        """Update the workflow status of a job listing."""
        self._get_conn().execute(
            "UPDATE job_listings SET status = ? WHERE id = ?", (status, job_id)
        )
        self._get_conn().commit()

    def update_job_fit(
        self,
        job_id: str,
        score: float,
        summary: str,
        strengths: list[str],
        gaps: list[str],
        interview_likelihood: str | None = None,
    ) -> None:
        """Write fit assessment results to a job listing."""
        self._get_conn().execute(
            """UPDATE job_listings
               SET fit_score = ?, fit_summary = ?, fit_strengths = ?,
                   fit_gaps = ?, interview_likelihood = ?
               WHERE id = ?""",
            (score, summary, json.dumps(strengths), json.dumps(gaps), interview_likelihood, job_id),
        )
        self._get_conn().commit()
        log.debug("Updated fit for job %s: score=%.1f", job_id, score)

    def _row_to_job(self, row: sqlite3.Row) -> JobListing:
        d = _row_to_dict(row)
        d["fit_strengths"] = json.loads(d.get("fit_strengths") or "[]")
        d["fit_gaps"] = json.loads(d.get("fit_gaps") or "[]")
        d["remote_ok"] = bool(d.get("remote_ok", 0))
        # Apply model-level defaults for columns the DB may store as NULL
        if d.get("ats_type") is None:
            d["ats_type"] = "universal"
        if d.get("status") is None:
            d["status"] = "new"
        return JobListing(**d)

    # ── Applications ──────────────────────────────────────────────────────────

    def create_application(self, job_id: str, **kwargs: Any) -> Application:
        """
        Create a new application record.

        Args:
            job_id: ID of the associated job listing.
            **kwargs: Any additional Application fields to set.

        Returns:
            Newly created Application.
        """
        app_id = str(uuid.uuid4())
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO applications
               (id, job_id, status, resume_tailored_text, resume_tailored_path,
                cover_letter_text, shadow_screenshots, fill_log, custom_qa, human_notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                app_id,
                job_id,
                kwargs.get("status", "pending"),
                kwargs.get("resume_tailored_text"),
                kwargs.get("resume_tailored_path"),
                kwargs.get("cover_letter_text"),
                json.dumps(kwargs.get("shadow_screenshots", [])),
                json.dumps(kwargs.get("fill_log", [])),
                json.dumps(kwargs.get("custom_qa", {})),
                kwargs.get("human_notes"),
            ),
        )
        conn.commit()
        log.info("Created application", extra={"app_id": app_id, "job_id": job_id})
        return self.get_application(app_id)  # type: ignore[return-value]

    def get_application(self, app_id: str) -> Application | None:
        """Return a single application by ID."""
        row = self._get_conn().execute(
            "SELECT * FROM applications WHERE id = ?", (app_id,)
        ).fetchone()
        if not row:
            return None
        app = self._row_to_app(row)
        # Hydrate job
        app.job = self.get_job(app.job_id)
        return app

    def list_applications(self, status: str | None = None) -> list[Application]:
        """Return all applications, optionally filtered by status."""
        if status:
            rows = self._get_conn().execute(
                "SELECT * FROM applications WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = self._get_conn().execute(
                "SELECT * FROM applications ORDER BY created_at DESC"
            ).fetchall()
        apps = [self._row_to_app(r) for r in rows]
        # Hydrate jobs
        for app in apps:
            app.job = self.get_job(app.job_id)
        return apps

    def update_application(self, app_id: str, **kwargs: Any) -> Application:
        """
        Update application fields. Handles JSON serialization for list/dict fields.

        Args:
            app_id: Application ID to update.
            **kwargs: Field name → new value pairs.

        Returns:
            Updated Application.
        """
        if not kwargs:
            return self.get_application(app_id)  # type: ignore[return-value]

        # Serialize compound types
        if "shadow_screenshots" in kwargs:
            kwargs["shadow_screenshots"] = json.dumps(kwargs["shadow_screenshots"])
        if "fill_log" in kwargs:
            kwargs["fill_log"] = json.dumps(kwargs["fill_log"])
        if "custom_qa" in kwargs:
            kwargs["custom_qa"] = json.dumps(kwargs["custom_qa"])

        cols = list(kwargs.keys())
        set_clause = ", ".join(f"{c} = ?" for c in cols)
        vals = [kwargs[c] for c in cols] + [app_id]

        self._get_conn().execute(
            f"UPDATE applications SET {set_clause} WHERE id = ?", vals
        )
        self._get_conn().commit()
        return self.get_application(app_id)  # type: ignore[return-value]

    def _row_to_app(self, row: sqlite3.Row) -> Application:
        d = _row_to_dict(row)
        d["shadow_screenshots"] = json.loads(d.get("shadow_screenshots") or "[]")
        d["fill_log"] = json.loads(d.get("fill_log") or "[]")
        d["custom_qa"] = json.loads(d.get("custom_qa") or "{}")
        return Application(**d)

    # ── Chat ─────────────────────────────────────────────────────────────────

    def add_message(
        self,
        role: str,
        content: str,
        context_type: str | None = None,
        context_id: str | None = None,
    ) -> ChatMessage:
        """Persist a chat message. Returns the created record."""
        conn = self._get_conn()
        cur = conn.execute(
            "INSERT INTO chat_messages (role, content, context_type, context_id) VALUES (?, ?, ?, ?)",
            (role, content, context_type, context_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM chat_messages WHERE id = ?", (cur.lastrowid,)).fetchone()
        return ChatMessage(**_row_to_dict(row))

    def get_messages(self, limit: int = 50) -> list[ChatMessage]:
        """Return the most recent `limit` chat messages in chronological order."""
        rows = self._get_conn().execute(
            """SELECT * FROM (SELECT * FROM chat_messages ORDER BY id DESC LIMIT ?)
               ORDER BY id ASC""",
            (limit,),
        ).fetchall()
        return [ChatMessage(**_row_to_dict(r)) for r in rows]

    def get_message_count(self) -> int:
        """Total number of chat messages stored."""
        row = self._get_conn().execute("SELECT COUNT(*) FROM chat_messages").fetchone()
        return row[0]

    def get_summary(self) -> str | None:
        """Return the rolling conversation summary text, or None if not yet generated."""
        row = self._get_conn().execute(
            "SELECT summary FROM conversation_summary WHERE id = 1"
        ).fetchone()
        return row["summary"] if row else None

    def update_summary(self, summary: str, message_count: int) -> None:
        """Upsert the rolling conversation summary."""
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO conversation_summary (id, summary, message_count_at_last, last_updated)
               VALUES (1, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   summary = excluded.summary,
                   message_count_at_last = excluded.message_count_at_last,
                   last_updated = excluded.last_updated""",
            (summary, message_count, _now()),
        )
        conn.commit()

    def get_summary_message_count(self) -> int:
        """Return the message count at the time of the last summary."""
        row = self._get_conn().execute(
            "SELECT message_count_at_last FROM conversation_summary WHERE id = 1"
        ).fetchone()
        return row["message_count_at_last"] if row else 0

    # ── Application Memory ────────────────────────────────────────────────────

    def upsert_app_memory(self, company: str, **kwargs: Any) -> AppMemory:
        """
        Store or update per-company form notes.

        Args:
            company: Company name (unique key).
            **kwargs: ats_type, what_worked, what_failed, form_notes.

        Returns:
            Updated AppMemory record.
        """
        conn = self._get_conn()
        existing = conn.execute(
            "SELECT id FROM application_memory WHERE company = ?", (company,)
        ).fetchone()

        if existing:
            cols = list(kwargs.keys())
            if cols:
                set_clause = ", ".join(f"{c} = ?" for c in cols)
                conn.execute(
                    f"UPDATE application_memory SET {set_clause} WHERE company = ?",
                    [kwargs[c] for c in cols] + [company],
                )
        else:
            kwargs["company"] = company
            cols = list(kwargs.keys())
            conn.execute(
                f"INSERT INTO application_memory ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                [kwargs[c] for c in cols],
            )

        conn.commit()
        row = conn.execute(
            "SELECT * FROM application_memory WHERE company = ?", (company,)
        ).fetchone()
        return AppMemory(**_row_to_dict(row))

    def get_app_memory(self, company: str) -> AppMemory | None:
        """Return stored notes for a specific company, or None."""
        row = self._get_conn().execute(
            "SELECT * FROM application_memory WHERE company = ?", (company,)
        ).fetchone()
        return AppMemory(**_row_to_dict(row)) if row else None

    # ── Email Events ──────────────────────────────────────────────────────────

    def add_email_event(self, data: dict) -> EmailEvent:
        """Persist a classified email event."""
        conn = self._get_conn()
        cur = conn.execute(
            """INSERT INTO email_events
               (app_id, company, subject, sender, received_at, category, summary,
                action_needed, urgency, key_details, raw_snippet)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data.get("app_id"),
                data.get("company"),
                data.get("subject"),
                data.get("sender"),
                data.get("received_at"),
                data.get("category"),
                data.get("summary"),
                int(data.get("action_needed", False)),
                data.get("urgency"),
                data.get("key_details"),
                data.get("raw_snippet"),
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM email_events WHERE id = ?", (cur.lastrowid,)).fetchone()
        d = _row_to_dict(row)
        d["action_needed"] = bool(d["action_needed"])
        return EmailEvent(**d)

    def get_email_events(
        self,
        app_id: str | None = None,
        action_needed: bool | None = None,
        limit: int = 50,
    ) -> list[EmailEvent]:
        """Return email events with optional filtering."""
        conditions: list[str] = []
        params: list[Any] = []

        if app_id:
            conditions.append("app_id = ?")
            params.append(app_id)
        if action_needed is not None:
            conditions.append("action_needed = ?")
            params.append(int(action_needed))

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._get_conn().execute(
            f"SELECT * FROM email_events {where} ORDER BY received_at DESC LIMIT ?",
            params + [limit],
        ).fetchall()

        result = []
        for r in rows:
            d = _row_to_dict(r)
            d["action_needed"] = bool(d["action_needed"])
            result.append(EmailEvent(**d))
        return result

    # ── Scrape Runs ───────────────────────────────────────────────────────────

    def log_scrape_run(
        self, source: str, query: str, location: str, results_count: int
    ) -> None:
        """Record a scrape run for audit/debugging."""
        self._get_conn().execute(
            "INSERT INTO scrape_runs (source, query, location, results_count) VALUES (?, ?, ?, ?)",
            (source, query, location, results_count),
        )
        self._get_conn().commit()
